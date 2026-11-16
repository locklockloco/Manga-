"""Content-based job identity and preserved-file validation before workspace reuse.

Filesystem timestamps are never accepted as evidence of unchanged source bytes.
Folder plans describe source candidates independently of their editable reading
order. Display/report generation and the preflight pause are operational settings;
all settings which change imported or segmented artwork belong to the identity.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

PROCESSING_DEFAULTS = {'include_all': False, 'min_gutter': 12, 'padding': 4,
                       'vertical': True, 'reading_direction': 'ltr'}


class JobIdentityError(ValueError):
    """Request cannot safely reuse this chapter; saved work remains untouched."""


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        before = os.fstat(handle.fileno())
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
        after = os.fstat(handle.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise JobIdentityError(f'Source changed while its bytes were being checked: {path}')
    return h.hexdigest()


def _canonical_path(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def processing_options(options):
    result = {key: options.get(key, default) for key, default in PROCESSING_DEFAULTS.items()}
    result.update(include_all=bool(result['include_all']), vertical=bool(result['vertical']),
                  min_gutter=int(result['min_gutter']), padding=int(result['padding']))
    return result


def fingerprint_input(input_path, output_dir):
    """Stream source SHA-256s; include additions/deletions/renames in folder plan."""
    from .importer import IMAGE_EXTENSIONS, MAX_IMAGES, natural_key
    source, output = Path(input_path).resolve(), Path(output_dir).resolve()
    if not source.exists():
        raise JobIdentityError(f'Input does not exist: {source}. Saved work was not changed.')
    if source.is_dir():
        if source == output or source == output / 'originals':
            raise JobIdentityError('Input folder and output folder must be different')
        candidates = []
        for path in source.rglob('*'):
            if path.is_symlink() or not path.is_file() or path.suffix.casefold() not in IMAGE_EXTENSIONS:
                continue
            if path.resolve().is_relative_to(output):
                continue
            candidates.append(path)
        candidates.sort(key=lambda path: (natural_key(path.relative_to(source).as_posix()), path.as_posix()))
        if not candidates:
            raise JobIdentityError('No supported source images found. Saved work was not changed.')
        if len(candidates) > MAX_IMAGES:
            raise JobIdentityError('Too many source image candidates; split the chapter')
        entries = [{'name': path.relative_to(source).as_posix(), 'size': path.stat().st_size,
                    'sha256': digest_file(path)} for path in candidates]
        content = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return {'version': 1, 'input': _canonical_path(source), 'kind': 'folder',
                'content_sha256': content, 'entries': entries}
    if not source.is_file() or source.suffix.casefold() not in IMAGE_EXTENSIONS | {'.zip', '.webarchive'}:
        raise JobIdentityError('Choose a folder, ZIP, Safari webarchive or supported raster image')
    if source.is_relative_to(output):
        raise JobIdentityError('Input file must be outside its output workspace')
    checksum = digest_file(source)
    return {'version': 1, 'input': _canonical_path(source), 'kind': 'file',
            'content_sha256': checksum, 'size': source.stat().st_size}


def _read_report(root, name):
    path = root / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise JobIdentityError(f'Cannot validate saved source evidence {name}: {exc}') from exc


def _records(report):
    if not report:
        return []
    return [record for key in ('sources', 'excluded', 'invalid_sources')
            for record in report.get(key, []) if isinstance(record, dict)]


def _check_preserved(root, m, store):
    """Hash originals and authoritative revisions, including partial import cache."""
    expected = {}
    def add(path, checksum, role):
        if not path:
            return
        if not checksum:
            raise JobIdentityError(f'No checksum for saved {role}: {path}; manual recovery is required')
        if path in expected and expected[path] != checksum:
            raise JobIdentityError(f'Conflicting saved checksums for {path}; manual recovery is required')
        expected[path] = checksum
    for record in _records(m):
        add(record.get('path'), record.get('sha256'), 'original')
    for record in _records(_read_report(root, 'reports/import.json')):
        add(record.get('path'), record.get('sha256'), 'original')
    for cache_path in sorted((root / 'reports/import_cache').glob('*.json')):
        cache = _read_report(root, cache_path.relative_to(root))
        add(cache.get('path'), cache.get('metadata', {}).get('digest'), 'cached original')
    for panel in (m or {}).get('panels', []):
        add(panel.get('file'), panel.get('sha256'), 'immutable artwork')
        if panel.get('reconstructed_file'):
            add(panel['reconstructed_file'], panel.get('sha256'), 'reconstruction')
    for item in store.items('region'):
        panel = item.get('panel', {})
        add(panel.get('file'), panel.get('sha256'), 'checkpoint artwork')
    for relative, checksum in expected.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise JobIdentityError(f'Saved original/artwork is missing or unsafe: {relative}. Saved work was not changed.')
        if digest_file(path) != checksum:
            raise JobIdentityError(f'Saved original/artwork checksum changed: {relative}. Restore it from a trusted backup; saved work was not changed.')


def _legacy_source_match(root, source, plan, manifest):
    """Adopt older work only when its preserved sources match requested content.

    Older releases did not hash archive containers. Their embedded source hashes,
    source origins, saved input path and chapter ID provide the migration proof.
    Once adopted, the full container/folder fingerprint is pinned for future use.
    """
    report = _read_report(root, 'reports/import.json')
    records = _records(report) or _records(manifest)
    if not records:
        # An interrupted old import may have only per-candidate records.
        for cache_path in sorted((root / 'reports/import_cache').glob('*.json')):
            cache = _read_report(root, cache_path.relative_to(root))
            fp = cache.get('fingerprint', {})
            if _canonical_path(fp.get('input_path', source)) != _canonical_path(source):
                raise JobIdentityError('Cached originals belong to a different input')
            records.append({'origin': fp.get('origin'), 'path': cache.get('path'),
                            'sha256': cache.get('metadata', {}).get('digest')})
    if not records:
        raise JobIdentityError('This legacy workspace has no sufficient source identity evidence. Use a new output folder.')
    expected = {r.get('origin'): r for r in records if r.get('origin')}
    if plan['kind'] == 'folder':
        incoming = {entry['name']: entry['sha256'] for entry in plan['entries']}
    elif source.suffix.casefold() not in {'.zip', '.webarchive'}:
        incoming = {str(source): plan['content_sha256']}
    else:
        from .importer import _zip_candidates, _webarchive_candidates
        candidates = (_zip_candidates if source.suffix.casefold() == '.zip' else _webarchive_candidates)(source, [])
        incoming = {}
        if source.suffix.casefold() == '.zip':
            import zipfile
            with zipfile.ZipFile(source) as archive:
                for candidate in candidates:
                    h = hashlib.sha256()
                    with archive.open(candidate.zip_member) as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                            h.update(chunk)
                    incoming[candidate.origin] = h.hexdigest()
        else:
            for candidate in candidates:
                incoming[candidate.origin] = hashlib.sha256(candidate.data).hexdigest() if candidate.data is not None else None
    # Complete import reports enumerate excluded/corrupt resources too. A partial
    # old cache cannot prove no source was added/deleted; require a new workspace.
    if set(incoming) != set(expected):
        raise JobIdentityError('Legacy source inventory differs or is incomplete. Use a new output folder; saved work was not changed.')
    for origin, record in expected.items():
        if record.get('sha256') and incoming[origin] != record['sha256']:
            raise JobIdentityError(f'Legacy source content changed: {origin}. Saved work was not changed.')
        if not record.get('sha256') and not record.get('missing'):
            raise JobIdentityError(f'Legacy source lacks content evidence: {origin}. Use a new output folder.')


def validate_request(root, source, chapter, options, job, manifest, store):
    """Return an accepted plan; perform no job, manifest or projection writes."""
    root, source = Path(root).resolve(), Path(source).resolve()
    saved_plan = job.get('input_identity') or (manifest or {}).get('input_identity')
    evidence = bool(job.get('input') or manifest or (root / 'reports/import_cache').exists())
    for label, value in (('saved job', job.get('input')), ('saved chapter', (manifest or {}).get('input'))):
        if value and _canonical_path(value) != _canonical_path(source):
            raise JobIdentityError(f'This {label} belongs to a different input. Use a new output folder; saved work was not changed.')
    for value in (job.get('chapter'), (manifest or {}).get('chapter')):
        if value and value != chapter:
            raise JobIdentityError('Chapter ID differs from this saved workspace. Use a new output folder; saved work was not changed.')
    wanted = processing_options(options)
    saved_options = [value for value in (job.get('processing_options'), job.get('options'),
                                          (manifest or {}).get('options')) if value is not None]
    if any(processing_options(value) != wanted for value in saved_options):
        raise JobIdentityError('Processing settings changed. Use a new output folder or targeted difference review; saved work was not changed.')
    if evidence and not saved_options:
        raise JobIdentityError('Legacy workspace has no processing-settings identity; use a new output folder')
    if evidence and not (job.get('chapter') or (manifest or {}).get('chapter')):
        raise JobIdentityError('Legacy workspace has no chapter identity; use a new output folder')
    if evidence and not (job.get('input') or (manifest or {}).get('input')):
        raise JobIdentityError('Legacy workspace has no input identity; use a new output folder')
    plan = fingerprint_input(source, root)
    if saved_plan is not None:
        if any(value != plan for value in (job.get('input_identity'), (manifest or {}).get('input_identity')) if value is not None):
            raise JobIdentityError('Input source inventory/content changed since this job began. Restore the original input or use a new output folder; saved work was not changed.')
    elif evidence:
        _legacy_source_match(root, source, plan, manifest)
    _check_preserved(root, manifest, store)
    return plan
