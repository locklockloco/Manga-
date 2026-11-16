"""Byte-preserving local chapter import with explicit exclusions and provenance.

Coordinates refer to stored raster pixels (EXIF orientation is never applied). No
network requests are made when opening a Safari webarchive.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, asdict
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
import os
import json
import plistlib
import re
import stat
import shutil
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote, urldefrag, urljoin, urlparse
import warnings as warning_module
import zipfile

from PIL import Image

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGES = 10000
MAX_RESOURCE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_PIXELS = 80_000_000
IMPORT_VERSION = "3.1"
_FORMAT_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_IRRELEVANT = re.compile(r"(?:^|[/_.\- ])(?:favicon|icon|avatar|logo|thumbnail|thumb|banner|advertisement|advert|ads)(?:$|[/_.\- \d])", re.I)
_CHAPTER_HINT = re.compile(r"(?:_imagelist|viewer_img|comic[_-]?image|chapter[_-]?image|wt_viewer|episode[_-]?image|data-episode)", re.I)


def natural_key(value: str) -> tuple:
    """Stable numeric filename order: strip2 precedes strip10."""
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", value))


@dataclass
class _Candidate:
    name: str
    origin: str
    data: bytes | None = None
    file: Path | None = None
    zip_member: str | None = None
    byte_count: int | None = None
    role: str = ""
    referenced: bool = True
    width: int = 0
    height: int = 0
    mode: str = ""
    format: str = ""
    digest: str = ""
    has_transparency: bool = False
    reason: str = ""
    missing: bool = False
    reference_count: int = 0

    def read(self) -> bytes:
        if self.data is not None:
            return self.data
        assert self.file is not None
        if self.zip_member is not None:
            with zipfile.ZipFile(self.file) as archive:
                return archive.read(self.zip_member)
        return self.file.read_bytes()


class _ImageReferences(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images: list[dict] = []
        self.base = ""

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "base" and attr.get("href"):
            self.base = attr["href"]
        if tag != "img":
            return
        urls = []
        # Lazy image attributes tend to contain the original; src may be a spacer.
        for key in ("data-url", "data-original", "data-src", "data-lazy-src"):
            if attr.get(key) and not attr[key].startswith("data:"):
                urls.append(attr[key].strip())
        for key in ("data-srcset", "srcset"):
            entries = []
            for entry in attr.get(key, "").split(","):
                parts = entry.strip().split()
                if parts:
                    try:
                        size = float(parts[1].rstrip("wx")) if len(parts) > 1 else 1.0
                    except ValueError:
                        size = 1.0
                    entries.append((size, parts[0]))
            urls.extend(url for _, url in sorted(entries, reverse=True) if not url.startswith("data:"))
        if attr.get("src") and not attr["src"].startswith("data:"):
            urls.append(attr["src"].strip())
        self.images.append({"urls": urls, "role": " ".join(str(v) for k, v in attrs if v is not None)})


def _safe_name(name: str) -> str:
    name = PurePosixPath(name.replace("\\", "/")).name
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return (name or "image")[:160]


def _canonical_url(value: str) -> str:
    return urldefrag(value)[0]


def _validate_image(candidate: _Candidate) -> None:
    if candidate.missing:
        return
    try:
        size = candidate.byte_count if candidate.byte_count is not None else (len(candidate.data) if candidate.data is not None else candidate.file.stat().st_size)
        if size > MAX_RESOURCE_BYTES:
            candidate.reason = f"Resource exceeds {MAX_RESOURCE_BYTES} byte limit"
            return
        _check_decode_memory(0, 0, size)
        raw = candidate.read()
        candidate.digest = sha256(raw).hexdigest()
        if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) > 24 and raw[24] > 8:
            candidate.reason = "High-bit-depth PNG requires a dedicated 16-bit export path; not silently reduced to 8-bit"
            return
        with warning_module.catch_warnings():
            warning_module.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as im:
                candidate.width, candidate.height = im.size
                candidate.mode = im.mode
                candidate.has_transparency = "A" in im.mode or "transparency" in im.info
                if im.mode in {"I", "F"} or im.mode.startswith("I;"):
                    candidate.reason = "High-bit-depth raster unsupported; original must not be silently quantized"
                    return
                candidate.format = im.format or ""
                if candidate.format not in _FORMAT_EXTENSIONS:
                    candidate.reason = "Unsupported raster format (only JPEG, PNG and WebP)"
                    return
                if im.width * im.height > MAX_PIXELS:
                    candidate.reason = f"Raster exceeds {MAX_PIXELS} pixel safety limit"
                    return
                _check_decode_memory(im.width, im.height, len(raw))
                if getattr(im, "n_frames", 1) > 1:
                    candidate.reason = "Animated image requires explicit frame extraction"
                    return
                im.verify()
            # verify() does not decode JPEG's entropy stream, so load as well.
            with Image.open(BytesIO(raw)) as im:
                im.load()
    except (OSError, ValueError, SyntaxError, MemoryError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        candidate.reason = f"Invalid or unsafe image: {exc}"


def _folder_candidates(source: Path, output: Path, notes: list[str]) -> list[_Candidate]:
    if source == output or source == output / "originals":
        raise ValueError("Input folder and output folder must be different")
    files = []
    for item in source.rglob("*"):
        if item.is_symlink():
            continue
        if item.is_file() and item.suffix.casefold() in IMAGE_EXTENSIONS:
            # Results nested inside the input must never be imported recursively.
            if item.resolve().is_relative_to(output):
                continue
            files.append(item)
    files.sort(key=lambda p: (natural_key(p.relative_to(source).as_posix()), p.as_posix()))
    if not files:
        raise ValueError("No JPEG, PNG or WebP images found in input folder")
    notes.append("Folder order uses natural relative-path order; confirm the chapter reading order in review.")
    return [_Candidate(p.name, p.relative_to(source).as_posix(), file=p) for p in files]


def _zip_candidates(source: Path, notes: list[str]) -> list[_Candidate]:
    candidates = []
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_IMAGES * 2:
            raise ValueError("ZIP has too many members")
        total = 0
        seen_members = set()
        for info in infos:
            if info.filename in seen_members:
                raise ValueError(f"Duplicate ZIP member name rejected: {info.filename}")
            seen_members.add(info.filename)
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", info.filename):
                raise ValueError(f"Unsafe ZIP member: {info.filename}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"Symlink ZIP member rejected: {info.filename}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES or info.file_size > MAX_RESOURCE_BYTES:
                raise ValueError("ZIP uncompressed resource size exceeds safety limits")
            if info.file_size > 8 * 1024 * 1024 and info.file_size / max(1, info.compress_size) > 2000:
                raise ValueError(f"Suspicious ZIP compression ratio: {info.filename}")
        image_infos = [i for i in infos if not i.is_dir()
                       and PurePosixPath(i.filename).suffix.casefold() in IMAGE_EXTENSIONS
                       and not i.filename.startswith("__MACOSX/")
                       and not PurePosixPath(i.filename).name.startswith("._")]
        image_infos.sort(key=lambda i: (natural_key(i.filename), i.filename))
        for info in image_infos:
            candidates.append(_Candidate(PurePosixPath(info.filename).name, info.filename, file=source, zip_member=info.filename, byte_count=info.file_size))
    if not candidates:
        raise ValueError("ZIP contains no supported chapter images")
    notes.append("ZIP order uses natural member-path order; confirm the chapter reading order in review.")
    return candidates


def _webarchive_candidates(source: Path, notes: list[str]) -> list[_Candidate]:
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Webarchive exceeds 1 GiB import limit; use an image ZIP for very large chapters")
    available = available_memory_bytes()
    if available is not None and source.stat().st_size * 4 > available * .65:
        raise MemoryError("Insufficient available memory to parse Safari plist; use a ZIP of original strips")
    try:
        root = plistlib.loads(source.read_bytes())
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid Safari webarchive: {exc}") from exc
    if not isinstance(root, dict):
        raise ValueError("Safari webarchive root must be a dictionary")
    resources: dict[str, dict] = {}
    documents = []
    total = 0

    def visit(node, depth=0):
        nonlocal total
        if depth > 20:
            raise ValueError("Webarchive frame nesting exceeds safety limit")
        main = node.get("WebMainResource", {})
        if isinstance(main, dict):
            documents.append(main)
        for resource in [main, *node.get("WebSubresources", [])]:
            if not isinstance(resource, dict):
                continue
            data = resource.get("WebResourceData", b"")
            if not isinstance(data, bytes):
                continue
            total += len(data)
            if total > MAX_TOTAL_BYTES or len(data) > MAX_RESOURCE_BYTES:
                raise ValueError("Webarchive resource size exceeds safety limits")
            url = _canonical_url(resource.get("WebResourceURL", ""))
            if url:
                resources.setdefault(url, resource)
        for child in node.get("WebSubframeArchives", []):
            if isinstance(child, dict):
                visit(child, depth + 1)
    visit(root)
    images: dict[str, _Candidate] = {}
    for url, resource in resources.items():
        mime = str(resource.get("WebResourceMIMEType", "")).lower()
        suffix = PurePosixPath(urlparse(url).path).suffix.casefold()
        if mime.startswith("image/") or suffix in IMAGE_EXTENSIONS:
            images[url] = _Candidate(unquote(PurePosixPath(urlparse(url).path).name) or "image", url,
                                     data=resource.get("WebResourceData", b""), referenced=False)
    if not images:
        raise ValueError("No embedded images found in Safari webarchive; save the complete page or use a ZIP")
    ordered = []
    missing = []
    seen = set()
    for document in documents:
        data = document.get("WebResourceData", b"")
        if not isinstance(data, bytes):
            continue
        encoding = document.get("WebResourceTextEncodingName", "utf-8") or "utf-8"
        try:
            markup = data.decode(encoding, errors="replace")
        except LookupError:
            markup = data.decode("utf-8", errors="replace")
        parser = _ImageReferences()
        parser.feed(markup)
        base = urljoin(document.get("WebResourceURL", ""), parser.base)
        for reference in parser.images:
            resolved = [_canonical_url(urljoin(base, url)) for url in reference["urls"]]
            chosen = next((url for url in resolved if url in images), None)
            if chosen is None:
                if resolved:
                    missing.append(resolved[0])
                    if _CHAPTER_HINT.search(reference["role"]):
                        ordered.append(_Candidate(unquote(PurePosixPath(urlparse(resolved[0]).path).name) or "missing_image",
                                                  resolved[0], role=reference["role"], missing=True,
                                                  reason="HTML chapter image has no embedded resource"))
                continue
            if chosen in seen:
                images[chosen].reference_count += 1
                continue
            seen.add(chosen)
            candidate = images[chosen]
            candidate.referenced = True
            candidate.reference_count += 1
            candidate.role = reference["role"]
            ordered.append(candidate)
    remaining = [c for url, c in images.items() if url not in seen]
    remaining.sort(key=lambda c: (natural_key(c.origin), c.origin))
    ordered.extend(remaining)
    if missing:
        notes.append(f"{len(missing)} HTML image references have no embedded image; missing content is not downloaded or invented.")
        notes.append("Missing image references: " + "; ".join(missing[:20]))
    if not seen:
        notes.append("HTML image order unavailable; embedded resources use natural URL order. Reading order needs review.")
    else:
        notes.append("Webarchive chapter order follows embedded HTML image order; unreferenced resources are listed separately.")
    if len(documents) > 1:
        notes.append("Webarchive includes subframes; their relative reading order needs review.")
    return ordered


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def available_memory_bytes():
    """Best effort available memory, without a required platform dependency."""
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except ImportError:
        pass
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    try:
        return os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except (AttributeError, OSError, ValueError):
        return None


def _check_decode_memory(width, height, encoded_bytes):
    estimated = width * height * 12 + encoded_bytes * 2
    available = available_memory_bytes()
    if available is not None and estimated > available * .65:
        raise MemoryError(f'Estimated strip decode needs {estimated} bytes; available memory is {available} bytes')


def _fsync_directory(directory):
    try:
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass  # Directory fsync is unsupported on some Windows filesystems.


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name, suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_unchanged(path: Path, raw: bytes, digest: str) -> None:
    """Commit complete source bytes atomically, never replace different content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # The caller owns the single-writer job lock. Any staging files left for
    # this exact original by a killed previous writer are therefore stale.
    for stale in path.parent.glob('.' + path.name + '*.tmp'):
        stale.unlink(missing_ok=True)
    if path.exists():
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Refusing to overwrite a different original: {path}")
        return
    free = shutil.disk_usage(path.parent).free
    if len(raw) + 1024 * 1024 > free:
        raise OSError(f'Insufficient disk space to preserve original ({len(raw)} bytes, {free} free)')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, prefix='.' + path.name,
                                         suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # The application supplies the single-writer lock. Check again so an
        # existing original can never be replaced by changed input on resume.
        if path.exists():
            if sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Refusing to overwrite a different original: {path}")
        else:
            os.replace(temporary, path)
            _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _numeric_token(item):
    match = re.match(r'^(.*?)(\d+)(\.[^.]+)?$', item['original_name'])
    if not match:
        return None
    # WEBTOON resource filenames often prepend a different upload timestamp to
    # each otherwise consistently numbered strip. It is not the reading index.
    prefix = re.sub(r'^\d{10,}[_-]', '', match.group(1)).casefold()
    return prefix, int(match.group(2))


def _ordering_warnings(records, kind):
    issues, groups, hashes = [], {}, {}
    for record in records:
        if record.get('status') == 'EXCLUDED':
            continue
        token = _numeric_token(record)
        if token:
            groups.setdefault(token[0], []).append((token[1], record['id']))
        checksum = record.get('sha256')
        if checksum:
            hashes.setdefault(checksum, []).append(record['id'])
        if record.get('reference_count', 0) > 1:
            issues.append({'code': 'REPEATED_HTML_REFERENCE', 'source_ids': [record['id']],
                           'count': record['reference_count'], 'requires_review': True})
    for prefix, numbers in groups.items():
        unique = sorted({number for number, _ in numbers})
        if len(unique) >= 2:
            missing = [number for a, b in zip(unique, unique[1:])
                       for number in range(a + 1, min(b, a + MAX_IMAGES))]
            if missing:
                issues.append({'code': 'MISSING_NUMERIC_STRIPS', 'prefix': prefix,
                               'missing_numbers': missing, 'requires_review': True})
        by_number = {}
        for number, source_id in numbers:
            by_number.setdefault(number, []).append(source_id)
        for number, ids in by_number.items():
            if len(ids) > 1:
                issues.append({'code': 'DUPLICATE_NUMERIC_STRIP', 'number': number,
                               'source_ids': ids, 'requires_review': True})
        if [number for number, _ in numbers] != sorted(number for number, _ in numbers):
            issues.append({'code': 'HTML_FILENAME_ORDER_CONFLICT' if kind == 'webarchive' else 'OUT_OF_ORDER_STRIPS',
                           'source_ids': [source_id for _, source_id in numbers], 'requires_review': True})
    for checksum, ids in hashes.items():
        if len(ids) > 1:
            issues.append({'code': 'DUPLICATE_SOURCE_BYTES', 'source_ids': ids,
                           'sha256': checksum, 'requires_review': True})
    widths = {r.get('width') for r in records if r.get('status') == 'IMPORTED'}
    if len(widths) > 1:
        issues.append({'code': 'MIXED_WIDTH_ALIGNMENT_UNKNOWN', 'widths': sorted(widths),
                       'requires_review': True})
    return issues


def _assign_coordinates(sources, invalid_sources):
    y, previous, segment = 0, None, 0
    for order, source in enumerate(sources, 1):
        reasons = []
        if previous is not None:
            lo, hi = sorted((previous['candidate_order'], source['candidate_order']))
            invalid_between = [r for r in invalid_sources if lo < r['candidate_order'] < hi]
            if invalid_between:
                reasons.append({'code': 'INVALID_OR_MISSING_SOURCE_BETWEEN',
                                'source_ids': [r['id'] for r in invalid_between]})
            a, b = _numeric_token(previous), _numeric_token(source)
            if a and b and a[0] == b[0] and b[1] != a[1] + 1:
                reasons.append({'code': 'NUMERIC_SOURCE_DISCONTINUITY', 'previous_number': a[1],
                                'next_number': b[1]})
            if previous['width'] != source['width']:
                reasons.append({'code': 'WIDTH_ALIGNMENT_UNKNOWN', 'left_width': previous['width'],
                                'right_width': source['width']})
        elif any(r['candidate_order'] < source['candidate_order'] for r in invalid_sources):
            reasons.append({'code': 'INVALID_OR_MISSING_CHAPTER_START'})
        if reasons:
            segment += 1
        source.update(order=order, y0=y, y1=y + source['height'], segment_id=segment,
                      discontinuity_before=reasons, join_before_status='BLOCKED' if reasons else 'UNVERIFIED')
        if reasons:
            source['discontinuity_y'] = y
        else:
            source.pop('discontinuity_y', None)
        previous, y = source, y + source['height']
    return y


def _save_preflight(output, result):
    _atomic_json(output / 'reports' / 'import.json', result)
    _atomic_json(output / 'reports' / 'preflight.json', {
        'version': IMPORT_VERSION, 'updated_at': _utc_now(),
        'source_order': [source['id'] for source in result['sources']],
        'sources': result['sources'], 'invalid_sources': result.get('invalid_sources', []),
        'excluded': result.get('excluded', []), 'warnings': result.get('preflight_warnings', []),
        'errors': result.get('errors', []), 'order_reviewed': result.get('order_reviewed', False)})


def preflight(output_dir):
    """Read canonical import state, even after interruption between report writes."""
    root = Path(output_dir)
    result = json.loads((root / 'reports' / 'import.json').read_text(encoding='utf-8'))
    return {'version': result.get('import_version', IMPORT_VERSION),
            'source_order': [source['id'] for source in result['sources']],
            'sources': result['sources'], 'invalid_sources': result.get('invalid_sources', []),
            'excluded': result.get('excluded', []), 'warnings': result.get('preflight_warnings', []),
            'errors': result.get('errors', []), 'order_reviewed': result.get('order_reviewed', False),
            'coordinate_revision': result.get('coordinate_revision', 0)}


def reorder_sources(output_dir, ids):
    """Persist an explicit source permutation, keeping original IDs and bytes.

    This changes chapter coordinates. Callers must invalidate/review coordinate
    dependent proposals, never silently apply old crop rectangles to new order.
    """
    root = Path(output_dir)
    result = json.loads((root / 'reports' / 'import.json').read_text(encoding='utf-8'))
    mapping = {source['id']: source for source in result['sources']}
    if (not isinstance(ids, list) or not all(isinstance(value, str) for value in ids)
            or len(ids) != len(mapping) or set(ids) != set(mapping)):
        raise ValueError('Reorder must include every valid source ID exactly once')
    sources = [mapping[source_id] for source_id in ids]
    result['sources'] = sources
    result['height'] = _assign_coordinates(sources, result.get('invalid_sources', []))
    result['order_reviewed'] = True
    result['coordinate_revision'] = int(result.get('coordinate_revision', 0)) + 1
    result['preflight_warnings'] = _ordering_warnings(sources, result['input_type'])
    _save_preflight(root, result)
    return sources


def _candidate_fingerprint(candidate, source):
    if candidate.file is not None:
        status = candidate.file.stat()
    else:
        status = source.stat()
    return {'input_path': str(source), 'origin': candidate.origin, 'file_size': status.st_size,
            'mtime_ns': status.st_mtime_ns, 'zip_member': candidate.zip_member,
            'version': IMPORT_VERSION}


def _candidate_metadata(candidate):
    return {key: getattr(candidate, key) for key in ('width', 'height', 'mode', 'format', 'digest',
            'has_transparency', 'reason', 'missing', 'reference_count')}


def import_chapter(input_path: str | Path, output_dir: str | Path, include_all: bool = False,
                   checkpoint=None) -> dict:
    """Import with crash-safe per-candidate validation/original caches.

    A durable cache record is committed before each progress callback. A killed
    import therefore resumes at the first uncached candidate without decoding or
    copying previously completed originals. Relevance filtering is separate from
    format/security validation. Missing/corrupt strips block subsequent joins.
    """
    source = Path(input_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"Input does not exist: {source}")
    if source.is_file() and source.is_relative_to(output):
        raise ValueError("Place the output outside the input file's current output tree")
    output.mkdir(parents=True, exist_ok=True)
    notes = []
    single = source.is_file() and source.suffix.casefold() in IMAGE_EXTENSIONS
    if source.is_dir():
        candidates = _folder_candidates(source, output, notes)
        kind = 'folder'
    elif source.suffix.casefold() == '.zip':
        candidates = _zip_candidates(source, notes)
        kind = 'zip'
    elif source.suffix.casefold() == '.webarchive':
        candidates = _webarchive_candidates(source, notes)
        kind = 'webarchive'
    elif single:
        candidates = [_Candidate(source.name, str(source), file=source)]
        kind = 'image'
    else:
        raise ValueError('Choose a folder, ZIP, Safari .webarchive, JPEG, PNG or WebP')
    if len(candidates) > MAX_IMAGES:
        raise ValueError(f'More than {MAX_IMAGES} candidate images; split the chapter')
    cache_dir = output / 'reports' / 'import_cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    registry_path = output / 'reports' / 'import_registry.json'
    registry = json.loads(registry_path.read_text(encoding='utf-8')) if registry_path.exists() else {'origins': {}, 'next_id': 1}
    for candidate in candidates:
        identity = str(source) + '\n' + candidate.origin
        if identity not in registry['origins']:
            registry['origins'][identity] = f"S{registry['next_id']:04d}"
            registry['next_id'] += 1
    _atomic_json(registry_path, registry)
    records, cache_hits = [], 0
    for position, candidate in enumerate(candidates, 1):
        source_id = registry['origins'][str(source) + '\n' + candidate.origin]
        fingerprint = _candidate_fingerprint(candidate, source)
        cache_key = sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        cache_path = cache_dir / (cache_key + '.json')
        cache, cache_valid = {}, False
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding='utf-8'))
                relative = cache.get('path')
                copied = output / relative if relative else None
                copied_stat = copied.stat() if copied else None
                transient_error = any(word in cache.get('metadata', {}).get('reason', '').lower()
                                      for word in ('memory', 'permission', 'no such file', 'temporar'))
                cache_valid = not transient_error and cache.get('fingerprint') == fingerprint and (candidate.missing or (
                    copied_stat is not None and copied_stat.st_size == cache.get('original_size')
                    and copied_stat.st_mtime_ns == cache.get('original_mtime_ns')))
            except (OSError, ValueError, KeyError):
                cache_valid = False
        if cache_valid:
            for key, value in cache['metadata'].items():
                setattr(candidate, key, value)
            cache_hits += 1
            relative = cache.get('path')
        else:
            _validate_image(candidate)
            relative = None
            if not candidate.missing:
                size = candidate.byte_count if candidate.byte_count is not None else (
                    len(candidate.data) if candidate.data is not None else candidate.file.stat().st_size)
                if size <= MAX_RESOURCE_BYTES:
                    raw = candidate.read()
                    candidate.digest = candidate.digest or sha256(raw).hexdigest()
                    filename = _safe_name(candidate.name)
                    if Path(filename).suffix.casefold() not in IMAGE_EXTENSIONS and candidate.format in _FORMAT_EXTENSIONS:
                        filename += _FORMAT_EXTENSIONS[candidate.format]
                    relative = (Path('originals') / f'{source_id}_{filename}').as_posix()
                    _write_unchanged(output / relative, raw, candidate.digest)
            copied_stat = (output / relative).stat() if relative else None
            _atomic_json(cache_path, {'fingerprint': fingerprint, 'metadata': _candidate_metadata(candidate),
                         'path': relative, 'original_size': copied_stat.st_size if copied_stat else None,
                         'original_mtime_ns': copied_stat.st_mtime_ns if copied_stat else None,
                         'completed_at': _utc_now()})
        record = {'id': source_id, 'candidate_order': position, 'original_name': candidate.name,
                  'origin': candidate.origin, 'path': relative, 'width': candidate.width,
                  'height': candidate.height, 'sha256': candidate.digest, 'mode': candidate.mode,
                  'format': candidate.format, 'html_role': candidate.role,
                  'has_transparency': candidate.has_transparency, 'reference_count': candidate.reference_count,
                  'status': 'INVALID' if candidate.reason else 'IMPORTED', 'error': candidate.reason,
                  'missing': candidate.missing, 'cached': cache_valid}
        records.append(record)
        # This partial report is durable even when a callback pauses/cancels or a
        # process is deliberately killed before the final report is assembled.
        _atomic_json(output / 'reports' / 'import_progress.json', {
            'stage': 'IMPORT', 'current': position, 'total': len(candidates),
            'source_id': source_id, 'status': record['status'], 'checksum': candidate.digest,
            'error': candidate.reason, 'updated_at': _utc_now(), 'cache_hits': cache_hits})
        if checkpoint:
            checkpoint('IMPORT', position, len(candidates), source_id=source_id,
                       status='CACHED' if cache_valid else record['status'],
                       checksum=candidate.digest, error=candidate.reason)
    chapter_hint_exists = any(_CHAPTER_HINT.search(r['html_role']) for r in records if not r['error'])
    widths = Counter(r['width'] for r in records if not r['error'] and r['width'] >= 240 and r['height'] >= 240)
    dominant_width, dominant_count = widths.most_common(1)[0] if widths else (0, 0)
    sources, excluded, invalid, errors = [], [], [], []
    for record, candidate in zip(records, candidates):
        reason = record['error']
        if reason:
            invalid.append(record.copy())
            errors.append({'source_id': record['id'], 'origin': record['origin'],
                           'code': 'MISSING_SOURCE' if record['missing'] else 'INVALID_SOURCE', 'error': reason})
        if not reason and not (single or include_all):
            if record['width'] < 160 or record['height'] < 90:
                reason = 'Very small graphic; unlikely to be chapter artwork'
            elif _IRRELEVANT.search(record['original_name'] + ' ' + record['html_role']) and not _CHAPTER_HINT.search(record['html_role']):
                reason = 'Filename/HTML role indicates icon, thumbnail, branding or advertisement'
            elif kind == 'webarchive' and chapter_hint_exists and not _CHAPTER_HINT.search(record['html_role']):
                reason = 'Outside HTML chapter-image list; review exclusion if needed'
            elif kind == 'webarchive' and not candidate.referenced:
                reason = 'Embedded resource is not referenced by a page image element'
            elif dominant_count >= 3 and record['width'] < dominant_width * .35 and record['height'] < dominant_width * .65:
                reason = 'Small graphic compared with repeated chapter-strip width'
        if reason:
            record['status'] = 'INVALID' if record['error'] else 'EXCLUDED'
            excluded.append({**record, 'reason': reason})
        else:
            sources.append(record)
    height = _assign_coordinates(sources, invalid)
    issues = _ordering_warnings(records, kind)
    for record in invalid:
        issues.append({'code': 'INVALID_OR_MISSING_SOURCE', 'source_ids': [record['id']],
                       'reason': record['error'], 'requires_review': True})
    if len({r['width'] for r in sources}) > 1:
        notes.append('Mixed source widths: alignment is unknown and joins are blocked for review.')
    if excluded:
        notes.append(f'{len(excluded)} candidate images excluded; every exclusion is retained in the preflight report.')
    if invalid:
        notes.append(f'{len(invalid)} invalid/missing source(s): continuity is blocked, never silently collapsed.')
    notes.extend(f"{issue['code']}: {json.dumps(issue, ensure_ascii=False)}" for issue in issues)
    notes.append('Source raster coordinates ignore EXIF display orientation to preserve stored pixel coordinates.')
    result = {'sources': sources, 'excluded': excluded, 'invalid_sources': invalid,
              'warnings': notes, 'errors': errors, 'preflight_warnings': issues,
              'input': str(source), 'input_type': kind, 'import_version': IMPORT_VERSION,
              'width': max((r['width'] for r in sources), default=0), 'height': height,
              'cache_hits': cache_hits, 'total_candidates': len(candidates), 'order_reviewed': False,
              'coordinate_revision': 0, 'status': 'REVIEW_REQUIRED' if issues or errors else 'IMPORTED'}
    # Preserve an explicitly reviewed source order over routine resume/retry.
    previous_report = output / 'reports' / 'import.json'
    if previous_report.exists():
        previous = json.loads(previous_report.read_text(encoding='utf-8'))
        if previous.get('order_reviewed') and {r['id'] for r in previous.get('sources', [])} == {r['id'] for r in sources}:
            mapping = {r['id']: r for r in sources}
            result['sources'] = [mapping[r['id']] for r in previous['sources']]
            result['height'] = _assign_coordinates(result['sources'], invalid)
            result['order_reviewed'] = True
            result['coordinate_revision'] = previous.get('coordinate_revision', 0)
    _save_preflight(output, result)
    if not sources:
        raise ValueError('No chapter images survived validation/filtering. See reports/import.json for exclusion reasons, inspect the input or retry with --include-all.')
    return result
