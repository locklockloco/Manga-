"""Independent source coverage and export QA; successful export never approves art.

Analysis is strip-local. Long RGB/RGBA PNGs are decoded row by row with bounded
zlib output, and only two source rasters (up to 64 MiB) are cached. Coverage uses
three ink thresholds and pale edge evidence, not the segmentation mask alone.
"""
from __future__ import annotations

from collections import OrderedDict, Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import zlib

import cv2
import numpy as np
from PIL import Image, ImageDraw

QA_VERSION = '3.1.0'
INACTIVE = {'REJECTED', 'MERGED', 'SPLIT', 'SUPERSEDED', 'DELETED', 'DISCARDED'}
TEXT_CLASSES = {'text_only', 'title_card', 'title_or_credits', 'standalone_sfx', 'dialogue_or_narration_only'}
REVIEW_CLASSES = TEXT_CLASSES | {'compound_layout', 'uncertain'}


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _prediction_key(manifest):
    geometry = [{'id':p.get('id'),'bounds':p.get('bounds'),'polygon':p.get('polygon'),
                 'classification':p.get('classification'),'active':p.get('active',True),
                 'status':p.get('status') if p.get('status') in INACTIVE else 'active'}
                for p in manifest.get('panels',[])]
    return hashlib.sha256(json.dumps(geometry,sort_keys=True).encode()).hexdigest()


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path leaves output directory')
    return path


def _active(panel):
    return panel.get('status', 'NEEDS_REVIEW') not in INACTIVE and panel.get('active', True)


def _human(panel):
    review = panel.get('review') or {}
    return bool(panel.get('status') == 'VERIFIED'
                and review.get('artwork_checked') is True
                and review.get('method') == 'explicit_human_visual_review'
                and review.get('reviewer') in {'user', 'human'})


def _approved(panel):
    return _active(panel) and _human(panel) and panel.get('classification', 'illustration') == 'illustration'


def _group(panel):
    return (panel.get('group_key') or panel.get('hypothesis_group') or panel.get('alternative_group') or panel.get('group_id')
            or panel.get('review_group') or panel.get('parent_id'))


def _resolved_groups(manifest):
    resolved = set()
    for panel in manifest.get('panels',[]):
        if (panel.get('status') == 'SUPERSEDED' and panel.get('selected_children')) or (_human(panel) and panel.get('classification','illustration') == 'illustration'):
            resolved.add(panel['id'])
            if _group(panel):
                resolved.add(_group(panel))
    return resolved


def _review_only(panel, resolved_groups=None):
    resolved_groups = resolved_groups or set()
    group_unresolved = bool(_group(panel)) and _group(panel) not in resolved_groups
    unresolved_parent = ((panel.get('hypothesis_role') or panel.get('proposal_role')) in {'parent','alternative'}
                         or bool(panel.get('alternatives')) or bool(panel.get('children')))
    unresolved_parent = unresolved_parent and panel.get('id') not in resolved_groups
    return (panel.get('classification', 'illustration') in REVIEW_CLASSES
            or group_unresolved or unresolved_parent)


def _source_x(source):
    alignment = source.get('alignment') or {}
    return int(source.get('x0', source.get('offset_x', alignment.get('x', 0))))


def _polygon(panel):
    value = panel.get('polygon')
    if isinstance(value, dict):
        value = value.get('points') or value.get('vertices')
    if value is None:
        return None
    points = np.asarray(value, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        raise ValueError('Polygon must contain at least three finite global [x,y] vertices')
    return points


def _bounds(panel):
    values = panel.get('bounds', [])
    if len(values) != 4 or any(isinstance(x, bool) or int(x) != x for x in values):
        raise ValueError('Bounds must be four integer coordinates')
    x0, y0, x1, y1 = map(int, values)
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise ValueError('Empty or invalid region bounds')
    return x0, y0, x1, y1


def _mask_for(panel, source):
    """Region footprint in a source's own stored raster coordinates."""
    w, h = int(source['width']), int(source['height'])
    sx, sy = _source_x(source), int(source['y0'])
    x0, y0, x1, y1 = _bounds(panel)
    left, top, right, bottom = max(0, x0-sx), max(0, y0-sy), min(w, x1-sx), min(h, y1-sy)
    mask = np.zeros((h, w), dtype=np.uint8)
    if left >= right or top >= bottom:
        return mask.astype(bool)
    poly = _polygon(panel)
    if poly is None:
        mask[top:bottom, left:right] = 1
    else:
        # Match the exporter's Pillow rasterization exactly at slanted edges.
        image_mask = Image.new('L',(w,h),0)
        ImageDraw.Draw(image_mask).polygon([(int(x)-sx,int(y)-sy) for x,y in poly],fill=1)
        mask = np.asarray(image_mask).copy()
        image_mask.close()
        mask[:top] = 0
        mask[bottom:] = 0
        mask[:, :left] = 0
        mask[:, right:] = 0
    return mask.astype(bool)


def _ink(rgba):
    alpha = rgba[..., 3].astype(np.float32) / 255
    rgb = np.rint(rgba[..., :3] * alpha[..., None] + 255*(1-alpha[..., None])).astype(np.uint8)
    minimum = rgb.min(2)
    visible = rgba[..., 3] > 0
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    strong, standard, faint = (minimum <= 240) & visible, (minimum <= 248) & visible, (minimum <= 252) & visible
    edge = (edges >= 6) & visible
    return {'strong':strong, 'standard':standard, 'faint':faint, 'edge':edge,
            'foreground': faint | edge}, rgb


class _SourceCache:
    def __init__(self, root, budget=64*1024*1024):
        self.root, self.budget = root, budget
        self.items = OrderedDict()
        self.bytes = 0
        self.max_bytes_observed = 0

    def get(self, source):
        key = source['id']
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key]
        if int(source['width'])*int(source['height']) > 80_000_000:
            raise ValueError('Source exceeds bounded 80 MP decode limit')
        with Image.open(_path(self.root, source['path'])) as im:
            if im.size != (source['width'], source['height']):
                raise ValueError('Source dimensions changed after indexing')
            value = np.asarray(im.convert('RGBA')).copy()
        while self.items and (len(self.items) >= 2 or self.bytes+value.nbytes > self.budget):
            _, previous = self.items.popitem(last=False)
            self.bytes -= previous.nbytes
        if value.nbytes <= self.budget:
            self.items[key] = value
            self.bytes += value.nbytes
            self.max_bytes_observed = max(self.max_bytes_observed, self.bytes)
        return value


def _unfilter(data, previous, bpp, kind):
    raw = np.frombuffer(data, dtype=np.uint8)
    if kind == 0:
        return raw.copy()
    if kind == 1:
        return (np.cumsum(raw.reshape(-1,bpp), axis=0, dtype=np.uint64) % 256).astype(np.uint8).ravel()
    if kind == 2:
        return (raw.astype(np.uint16)+previous) .astype(np.uint8)
    if kind not in {3, 4}:
        raise ValueError('Invalid PNG row filter')
    out = raw.copy()
    for i in range(len(out)):
        left = int(out[i-bpp]) if i >= bpp else 0
        up = int(previous[i])
        if kind == 3:
            prediction = (left+up)//2
        else:
            upper_left = int(previous[i-bpp]) if i >= bpp else 0
            p = left+up-upper_left
            pa, pb, pc = abs(p-left), abs(p-up), abs(p-upper_left)
            prediction = left if pa <= pb and pa <= pc else up if pb <= pc else upper_left
        out[i] = (int(out[i])+prediction) & 255
    return out


def _png_rows(path):
    """Yield (header, row_index, decoded row) without loading a full crop.

    CRCs, all five PNG filters, end marker, row count and decompression completion
    are checked. Generated exports are non-interlaced 8-bit RGB/RGBA PNGs.
    """
    with Path(path).open('rb') as handle:
        if handle.read(8) != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Export is not a PNG')
        header, decoder, buffer, row_index, previous = None, zlib.decompressobj(), bytearray(), 0, None
        ended, saw_data = False, False
        while True:
            head = handle.read(8)
            if len(head) != 8:
                raise ValueError('Truncated PNG chunk header')
            length, name = struct.unpack('!I4s',head)
            if length > 128*1024*1024:
                raise ValueError('PNG chunk exceeds resource limit')
            remaining, crc = length, zlib.crc32(name)
            payload = bytearray() if name == b'IHDR' else None
            while remaining:
                block = handle.read(min(65536,remaining))
                if not block:
                    raise ValueError('Truncated PNG chunk')
                remaining -= len(block)
                crc = zlib.crc32(block,crc)
                if payload is not None:
                    payload.extend(block)
                if name == b'IDAT':
                    if header is None:
                        raise ValueError('PNG image data before header')
                    saw_data = True
                    pending = block
                    while pending:
                        decoded = decoder.decompress(pending,65536)
                        pending = decoder.unconsumed_tail
                        buffer.extend(decoded)
                        row_bytes = header['width']*header['channels']
                        while len(buffer) >= row_bytes+1:
                            if row_index >= header['height']:
                                raise ValueError('PNG has excess decoded rows')
                            kind = buffer[0]
                            raw = bytes(buffer[1:row_bytes+1])
                            del buffer[:row_bytes+1]
                            row = _unfilter(raw,previous,header['channels'],kind)
                            previous = row
                            yield header,row_index,row.reshape(header['width'],header['channels'])
                            row_index += 1
            crc_bytes = handle.read(4)
            if len(crc_bytes) != 4 or struct.unpack('!I',crc_bytes)[0] != crc & 0xffffffff:
                raise ValueError('PNG chunk checksum failed')
            if name == b'IHDR':
                if header is not None or len(payload) != 13:
                    raise ValueError('Invalid PNG header')
                w,h,depth,color,compression,filtering,interlace = struct.unpack('!2I5B',payload)
                if not w or not h or depth != 8 or color not in {2,6} or compression or filtering or interlace:
                    raise ValueError('Expected non-interlaced 8-bit RGB/RGBA export')
                if w > 100_000 or w*h > 2_000_000_000:
                    raise ValueError('Export dimensions exceed QA limits')
                header = {'width':w,'height':h,'channels':3 if color == 2 else 4}
                previous = np.zeros(w*header['channels'],dtype=np.uint8)
            if name == b'IEND':
                ended = True
                break
        if not ended or not saw_data or header is None or row_index != header['height'] or buffer or not decoder.eof:
            raise ValueError('Incomplete PNG pixel stream')
        if handle.read(1):
            raise ValueError('Unexpected bytes after PNG end')


def _components(mask, source, thumbnail, evidence=None, rgb=None):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
    result = []
    counts = {key:np.bincount(labels.ravel(),weights=value.ravel(),minlength=count) for key,value in (evidence or {}).items() if key!='foreground'}
    colored = (np.ptp(rgb,axis=2)>18) if rgb is not None else np.zeros(mask.shape,dtype=bool)
    color_counts = np.bincount(labels.ravel(),weights=colored.ravel(),minlength=count)
    sx,sy = _source_x(source),int(source['y0'])
    for i in range(1,count):
        x,y,w,h,area = map(int,stats[i])
        result.append({'component':i,'source_bounds':[x,y,x+w,y+h],
                       'chapter_bounds':[x+sx,y+sy,x+w+sx,y+h+sy], 'pixels':area,
                       'thumbnail':thumbnail,'thumbnail_source_bounds':[0,0,source['width'],source['height']],
                       'ink_strength_counts':{key:int(value[i]) for key,value in counts.items()}})
    for component in result:
        component.update(_component_risk(component,source,float(color_counts[component['component']])/max(1,component['pixels'])))
    return result


def _source_thumbnail(root, source, rgb, components):
    relative = f"reports/coverage/{source['id']}.png"
    path = _path(root,relative)
    path.parent.mkdir(parents=True,exist_ok=True)
    image = Image.fromarray(rgb)
    image.thumbnail((620,1200),Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    sx,sy = image.width/source['width'], image.height/source['height']
    for component in components:
        x0,y0,x1,y1 = component['source_bounds']
        draw.rectangle((x0*sx,y0*sy,max(x1*sx,x0*sx+1),max(y1*sy,y0*sy+1)),outline='#ff3535',width=1)
    tmp = path.with_suffix('.tmp.png')
    image.save(tmp)
    os.replace(tmp,path)
    image.close()
    return relative


def _checkpoint(callback, index, total, item):
    if callback:
        callback('QA',index,total,item)


def _component_risk(component, source, colored_fraction=0.0):
    """Rank uncovered ink without claiming to recognize people, text, or objects.

    Faint-only components are retained exactly. A low rank means weak evidence
    of a missing illustration, never authorization to discard those pixels.
    """
    x0,y0,x1,y1 = component['source_bounds']
    width,height = x1-x0,y1-y0
    pixels = component['pixels']
    density = pixels/max(1,width*height)
    ink = component.get('ink_strength_counts') or {}
    strong,standard = ink.get('strong',0),ink.get('standard',0)
    substantial = pixels >= max(300,source['width']*source['height']*.0004)
    has_extent = max(width,height) >= max(28,source['width']*.045)
    has_color = colored_fraction >= .12
    if strong == 0 and (standard == 0 or standard/max(1,pixels)<.02):
        kind,priority,rank = 'compression_or_faint_detail_requires_review','low',70
        explanation = 'Only pale threshold/edge evidence; JPEG ringing, a fade, or real faint detail remain possible. Exact pixels and coordinates are retained.'
    elif substantial and has_extent and (has_color or density >= .38 or min(width,height) >= source['width']*.08):
        kind,priority,rank = 'meaningful_uncovered_artwork','high',15
        explanation = 'Substantial uncovered source ink or color suggests potentially missing artwork or important connected lettering. This is geometric evidence, not semantic certification.'
    else:
        kind,priority,rank = 'unclassified_text_or_sparse_art','medium' if standard>=8 else 'low',45 if standard>=8 else 60
        explanation = 'Uncovered definite ink is too small or sparse to identify reliably as text versus drawing. Retain it for classification; no OCR or deletion is implied.'
    return {'risk_type':kind,'priority':priority,'priority_rank':rank,'requires_review':True,
            'evidence':{'strong_pixels_min_240':int(strong),'standard_pixels_min_248':int(standard),
                        'all_detected_pixels':int(pixels),'bbox_width':width,'bbox_height':height,
                        'bbox_ink_density':round(density,5),'colored_pixel_fraction':round(colored_fraction,5),
                        'semantic_classification_certified':False,'explanation':explanation}}


def _crossings(mask, foreground, source, panel, strong=None, standard=None):
    """Separate actual interior line cuts from source edges and pale JPEG halos.

    The broad faint/edge foreground remains the coverage detector. Strong
    linework on BOTH sides is required for a high-priority cutoff suspicion;
    otherwise the observation stays visible as weaker boundary evidence.
    """
    eroded = cv2.erode(mask.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
    native = np.zeros(mask.shape,dtype=bool)
    native[0,:] = native[-1,:] = True
    native[:,0] = native[:,-1] = True
    inner = mask & ~eroded & ~native
    exterior = cv2.dilate(mask.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool) & ~mask & ~native
    neighborhood = np.ones((3,3),np.uint8)
    strong = foreground if strong is None else strong
    standard = foreground if standard is None else standard
    definite_crossing = inner & strong & cv2.dilate((exterior & strong).astype(np.uint8),neighborhood).astype(bool)
    standard_crossing = inner & standard & cv2.dilate((exterior & standard).astype(np.uint8),neighborhood).astype(bool)
    faint_crossing = inner & foreground & cv2.dilate((exterior & foreground).astype(np.uint8),neighborhood).astype(bool)
    pixels = int(definite_crossing.sum())
    boundary_count = int(inner.sum())
    suspected = pixels >= 5 and pixels/max(1,boundary_count) > .02
    return {'source_id':source['id'],'boundary_pixels_on_ink':int((inner & foreground).sum()),
            'crossing_pixels':pixels,'strong_crossing_pixels':pixels,
            'standard_crossing_pixels':int(standard_crossing.sum()),
            'faint_or_edge_crossing_pixels':int(faint_crossing.sum()),
            'native_source_edge_content_pixels':int((mask & native & foreground).sum()),
            'boundary_pixels':boundary_count,'suspected_cut':suspected,
            'risk_type':'internal_significant_linework_cut' if suspected else 'faint_boundary_or_native_edge_observation',
            'priority':'high' if suspected else 'low','priority_rank':10 if suspected else 75,
            'note':'Original full-bleed/source-edge content is informational. Pale halo crossings do not prove an illustration cutoff; exact faint coverage warnings are retained.'}


def _seam_risks(manifest):
    """Keep human review state separate from actual discontinuity evidence."""
    human_checked_ids = set()
    for panel in manifest.get('panels',[]):
        if _human(panel) and panel.get('review',{}).get('joins_checked') is True:
            human_checked_ids.update(panel.get('join_ids',[]))
    result = []
    for join in manifest.get('joins',[]):
        review = join.get('review') or {}
        human = (join.get('id') in human_checked_ids or
                 (review.get('reviewer') in {'user','human'} and
                  review.get('method') == 'explicit_human_visual_review' and
                  review.get('joins_checked',review.get('checked')) is True))
        seam_y = join.get('y')
        used_by = [p['id'] for p in manifest.get('panels',[]) if _active(p) and seam_y is not None and p.get('bounds',[0,0,0,0])[1] < seam_y < p.get('bounds',[0,0,0,0])[3]]
        flags = list(join.get('flags') or [])
        risk_flags = [flag for flag in flags if any(token in flag.upper() for token in
                      ('OVERLAP','MISSING','DISCONTINUITY','WIDTH_MISMATCH','ALIGNMENT','OUT_OF_ORDER','CORRUPT','DUPLICATE_SOURCE'))]
        context_jump = (float(join.get('seam_gradient_ratio') or 0) >= 6 and
                        float(join.get('border_rgb_mae') or 0) >= 35 and
                        float(join.get('horizontal_gradient_correlation') or 0) < .2)
        actual_risk = bool(join.get('blocked') or join.get('possible_overlap_rows') or risk_flags or context_jump)
        evidence = list(risk_flags)
        if join.get('blocked'):
            evidence.append('JOIN_BLOCKED_BY_PREFLIGHT')
        if join.get('possible_overlap_rows'):
            evidence.append('REPEATED_BORDER_ROWS_REQUIRE_VERIFICATION')
        if context_jump:
            evidence.append('SEAM_GRADIENT_EXCEEDS_NEIGHBORING_ROWS_WITH_LOW_FEATURE_CORRELATION')
        if not used_by:
            kind = 'preflight_source_adjacency_risk' if actual_risk else 'unused_source_adjacency_not_a_reconstruction_join'
            priority,rank = ('high',20) if actual_risk else ('low',98)
            evidence.append('NO_ACTIVE_EXPORTED_REGION_CROSSES_THIS_ADJACENCY')
        elif actual_risk:
            kind,priority,rank = 'risky_source_join','blocker' if join.get('blocked') else 'high',0 if join.get('blocked') else 20
        elif join.get('assessment') in {'uncertain','alignment_unknown'}:
            kind,priority,rank = 'uncertain_continuation_requires_inspection','medium',40
            evidence.append('CONTINUATION_EVIDENCE_IS_INCONCLUSIVE')
        else:
            kind,priority,rank = ('human_checked','low',95) if human else ('ordinary_exact_row_append_unreviewed','low',85)
            evidence.append('EXACT_SOURCE_ROWS_RETAINED_WITHOUT_REPAIR')
        result.append({'id':join.get('id'),'seam_y':join.get('y'),
                       'source_ids':[join.get('left_source'),join.get('right_source')],
                       'kind':kind,'priority':priority,'priority_rank':rank,
                       'human_checked':human,'requires_human_inspection':bool(used_by) and not human,
                       'used_by_regions':used_by,'inspect_priority':priority if used_by else 'none',
                       'actual_integrity_risk':actual_risk,'evidence':evidence,
                       'assessment':join.get('assessment'),'operation':join.get('operation'),
                       'note':'An unchanged sequential seam is not automatically suspicious or human-verified.'})
    return result


def _review_priorities(report, manifest):
    """Build a short actionable queue; exact faint components remain per-source."""
    regions = {p['id']:p for p in manifest.get('panels',[])}
    duplicates = {ident for group in report.get('duplicate_groups',[]) for ident in group}
    seam_by_id = {j['id']:j for j in report.get('seam_risks',[])}
    queue = []
    for source in report['per_source']:
        if source.get('integrity') != 'PASS':
            queue.append({'kind':'source_integrity','id':source['source_id'],'priority':'blocker','priority_rank':0,
                          'source_ids':[source['source_id']],'reason':'Original source checksum or decoding failed.'})
        grouped = {}
        for component in source.get('uncovered_components',[]):
            grouped.setdefault(component['risk_type'],[]).append(component)
        source['coverage_risk_counts'] = {kind:len(items) for kind,items in grouped.items()}
        for kind,items in grouped.items():
            priority = min(items,key=lambda item:item['priority_rank'])
            queue.append({'kind':kind,'id':source['source_id']+':'+kind,
                          'priority':priority['priority'],'priority_rank':priority['priority_rank'],
                          'source_ids':[source['source_id']],'component_count':len(items),
                          'component_ids':[item['component'] for item in items],
                          'pixels':sum(item['pixels'] for item in items),
                          'reason':priority['evidence']['explanation']})
    for region in report['per_region']:
        panel = regions.get(region['id'],{})
        actual_seams = [seam_by_id[j] for j in panel.get('join_ids',[]) if j in seam_by_id]
        reasons = []
        cutoff = any(item.get('suspected_cut') for item in region.get('linework_crossings',[]))
        region['cutoff_risk'] = cutoff
        if region.get('errors') or region.get('source_mapping',{}).get('status') == 'FAIL':
            kind,priority,rank = 'export_or_coordinate_integrity_failure','blocker',0
            reasons = list(region.get('errors',[]))+list(region.get('source_mapping',{}).get('errors',[]))
        elif cutoff:
            kind,priority,rank = 'suspected_illustration_cutoff','high',10
            reasons = ['Significant original linework crosses an interior cut on both sides.']
        elif panel.get('classification') == 'compound_layout':
            kind,priority,rank = 'unresolved_split_merge_or_attachment','high',20
            reasons = ['Independent-scene grouping is unresolved; all parent pixels remain available.']
        elif any(j['actual_integrity_risk'] for j in actual_seams):
            kind,priority,rank = 'risky_source_join','high',20
            reasons = [e for j in actual_seams if j['actual_integrity_risk'] for e in j['evidence']]
        elif region['id'] in duplicates:
            kind,priority,rank = 'duplicate_candidate_needs_review','medium',30
            reasons = ['Export resembles another candidate; no artwork was removed.']
        elif any(j['kind']=='uncertain_continuation_requires_inspection' for j in actual_seams):
            kind,priority,rank = 'uncertain_continuation_requires_inspection','medium',40
            reasons = ['Context evidence at an internal source seam is inconclusive.']
        elif panel.get('classification') == 'uncertain':
            kind,priority,rank = 'unclassified_content_retained','medium',45
            reasons = ['Retained region needs a content category; no missing-illustration claim.']
        elif panel.get('classification') in TEXT_CLASSES:
            kind,priority,rank = 'retained_text_classification_review','medium',50
            reasons = ['Text/SFX/title content is retained separately and is not lost artwork.']
        elif any(item['id']==region['id'] for item in report.get('unresolved_groups',[])):
            kind,priority,rank = 'unresolved_attachment_or_grouping','medium',50
            reasons = ['Composition is retained; the alternative grouping still needs a decision.']
        elif 'EXCESSIVE_OR_OFFCENTER_EXTERIOR_WHITESPACE_CHECK' in region.get('warnings',[]):
            kind,priority,rank = 'exterior_margin_requires_review','medium',55
            reasons = ['Exterior-margin geometry needs inspection; meaningful negative space must remain.']
        elif any(not j['human_checked'] for j in actual_seams):
            kind,priority,rank = 'ordinary_exact_source_seams_unreviewed','low',85
            reasons = ['Source rows are unchanged with no integrity-risk evidence; human inspection is pending.']
        else:
            kind,priority,rank = 'ordinary_artwork_human_review','low',95
            reasons = ['No high-priority geometric problem found; human artwork approval remains separate.']
        region.update(risk_type=kind,priority=priority,priority_rank=rank,risk_reasons=reasons,
                      human_review_required=not region.get('human_verified',False))
        queue.append({'kind':kind,'id':region['id'],'region_id':region['id'],'priority':priority,
                      'priority_rank':rank,'reason':' '.join(reasons),'bounds':panel.get('bounds'),
                      'source_ids':[r['source_id'] for r in panel.get('source_regions',[])]})
    for seam in report.get('seam_risks',[]):
        if seam['requires_human_inspection'] or seam['actual_integrity_risk']:
            queue.append({'kind':seam['kind'],'id':seam['id'],'priority':seam['priority'],
                          'priority_rank':seam['priority_rank'],'source_ids':seam['source_ids'],
                          'seam_y':seam['seam_y'],'reason':'; '.join(seam['evidence'])})
    report['review_queue'] = sorted(queue,key=lambda item:(item['priority_rank'],str(item['id'])))
    report['risk_priority_convention'] = 'Lower priority_rank is more urgent. Ranking does not approve or discard content.'
    report['risk_totals'] = {'queue_items_by_priority':dict(Counter(item['priority'] for item in queue)),
                            'regions_by_risk_type':dict(Counter(item['risk_type'] for item in report['per_region'])),
                            'uncovered_components_by_type':dict(Counter(c['risk_type'] for source in report['per_source'] for c in source.get('uncovered_components',[])))}


def _box_intersection(a,b):
    return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))


def _area(b):
    return max(0,b[2]-b[0])*max(0,b[3]-b[1])


def _iou(a,b):
    intersection = _box_intersection(a,b)
    return intersection/max(1,_area(a)+_area(b)-intersection)


def _fingerprint(sample):
    rgb = sample[...,:3]
    if sample.shape[2] == 4:
        alpha = sample[...,3:4].astype(np.float32)/255
        rgb = np.rint(rgb*alpha+255*(1-alpha)).astype(np.uint8)
    gray = cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray,(9,8),interpolation=cv2.INTER_AREA)
    bits = small[:,1:] > small[:,:-1]
    dhash = sum(int(value)<<i for i,value in enumerate(bits.ravel()))
    hist = cv2.calcHist([rgb],[0,1,2],None,[8,8,8],[0,256,0,256,0,256]).ravel()
    hist /= max(1,float(hist.sum()))
    return dhash,hist


def _mapping_validation(panel, sources):
    mapping = panel.get('source_regions')
    if mapping is None:
        return {'status':'REVIEW_REQUIRED','errors':['MISSING_SOURCE_COORDINATE_MAP']}
    x0,y0,x1,y1 = _bounds(panel)
    expected = []
    for source in sources:
        sx,sy = _source_x(source),int(source['y0'])
        a,b = max(y0,sy),min(y1,source['y1'])
        left,right = max(x0,sx),min(x1,sx+source['width'])
        if a < b and left < right:
            expected.append((source['id'],(left-sx,a-sy,right-sx,b-sy),(left,a,right,b)))
    try:
        actual = [(r['source_id'],tuple(r['source_bounds']),tuple(r['chapter_bounds'])) for r in mapping]
    except (KeyError,TypeError) as exc:
        return {'status':'FAIL','errors':['INVALID_SOURCE_COORDINATE_MAP: '+str(exc)]}
    if actual != expected:
        return {'status':'FAIL','errors':['SOURCE_COORDINATE_MAP_DIFFERS_FROM_ACTUAL_CROP'],
                'expected':[{'source_id':x[0],'source_bounds':list(x[1]),'chapter_bounds':list(x[2])} for x in expected]}
    return {'status':'PASS','errors':[]}


def _region_file(root,panel,sources,cache,verify_pixels):
    result = {'id':panel['id'],'file':panel.get('file'),'errors':[], 'warnings':[],
              'pixel_fidelity':'NOT_RUN' if not verify_pixels else 'PASS','pixels_compared':0,
              'mismatched_pixels':0,'mismatch_examples':[]}
    sample = None
    try:
        x0,y0,x1,y1 = _bounds(panel)
        poly = _polygon(panel)
        file = _path(root,panel.get('file',''))
        if not file.is_file():
            raise ValueError('Missing export file')
        actual_sha = _sha(file)
        result['sha256'] = actual_sha
        if panel.get('sha256') and actual_sha != panel['sha256']:
            result['errors'].append('EXPORT_CHECKSUM_CHANGED')
        sample = np.full((64,64,4),255,dtype=np.uint8)
        sample_ys = np.minimum(y1-y0-1,np.floor(np.arange(64)*(y1-y0)/64).astype(int))
        sample_xs = np.minimum(x1-x0-1,np.floor(np.arange(64)*(x1-x0)/64).astype(int))
        source_index = 0
        for header,row_number,row in _png_rows(file):
            if (header['width'],header['height']) != (x1-x0,y1-y0):
                raise ValueError('Export dimensions differ from bounds')
            for sample_y in np.flatnonzero(sample_ys == row_number):
                sample[sample_y,:,:header['channels']] = row[sample_xs]
            if not verify_pixels:
                continue
            global_y = y0+row_number
            while source_index < len(sources) and sources[source_index]['y1'] <= global_y:
                source_index += 1
            if source_index >= len(sources) or sources[source_index]['y0'] > global_y:
                result['errors'].append('EXPORT_ROWS_HAVE_NO_SOURCE')
                continue
            source = sources[source_index]
            expected_source = cache.get(source)
            sx = _source_x(source)
            left,right = max(x0,sx),min(x1,sx+source['width'])
            if right <= left:
                if row.shape[1] == 3 or np.any(row[:,3] != 0):
                    result['errors'].append('OPAQUE_PIXELS_WITHOUT_SOURCE')
                continue
            expected = expected_source[global_y-source['y0'],left-sx:right-sx,:header['channels']]
            actual = row[left-x0:right-x0]
            included = np.ones(right-left,dtype=bool)
            if poly is not None:
                # Raster exactly as coverage does, one source-local row at a time.
                row_mask_image = Image.new('L',(x1-x0,1),0)
                ImageDraw.Draw(row_mask_image).polygon([(int(x)-x0,int(y)-global_y) for x,y in poly],fill=1)
                polygon_row = np.asarray(row_mask_image).copy()
                row_mask_image.close()
                included = polygon_row[0,left-x0:right-x0].astype(bool)
                if header['channels'] != 4:
                    result['errors'].append('POLYGON_EXPORT_HAS_NO_ALPHA')
                elif np.any(row[~polygon_row[0].astype(bool),3] != 0):
                    result['errors'].append('POLYGON_OUTSIDE_MASK_NOT_TRANSPARENT')
            if header['channels'] == 3 and np.any(expected_source[global_y-source['y0'],left-sx:right-sx,3][included] != 255):
                result['errors'].append('ORIGINAL_SOURCE_ALPHA_LOST')
            different = np.any(expected != actual,axis=1) & included
            count = int(different.sum())
            result['pixels_compared'] += int(included.sum())
            result['mismatched_pixels'] += count
            if count and len(result['mismatch_examples']) < 10:
                for xx in np.flatnonzero(different)[:10-len(result['mismatch_examples'])]:
                    result['mismatch_examples'].append({'source_id':source['id'],
                        'source_xy':[int(xx+left-sx),int(global_y-source['y0'])],
                        'chapter_xy':[int(xx+left),int(global_y)]})
            if header['channels'] == 3 and (left > x0 or right < x1):
                result['errors'].append('OPAQUE_PADDING_WITHOUT_SOURCE')
            if header['channels'] == 4:
                if left > x0 and np.any(row[:left-x0,3]) or right < x1 and np.any(row[right-x0:,3]):
                    result['errors'].append('OPAQUE_PADDING_WITHOUT_SOURCE')
        if result['mismatched_pixels']:
            result['errors'].append('SOURCE_PIXEL_MISMATCH')
        if sample is not None:
            masks,_ = _ink(sample)
            ink = masks['foreground']
            result['display_metrics'] = {'foreground_fraction':round(float(ink.mean()),5)}
            ys,xs = np.nonzero(ink)
            if len(xs):
                margins = {'left':int(xs.min())/64,'right':(63-int(xs.max()))/64,
                           'top':int(ys.min())/64,'bottom':(63-int(ys.max()))/64}
                result['display_metrics']['outside_margin_fractions'] = margins
                if max(margins.values()) > .18:
                    result['warnings'].append('EXCESSIVE_OR_OFFCENTER_EXTERIOR_WHITESPACE_CHECK')
            if min(x1-x0,y1-y0) < 80:
                result['warnings'].append('UNEXPECTEDLY_TINY_OR_NARROW_REGION')
            if (y1-y0)/(x1-x0) > 8:
                result['warnings'].append('EXTREMELY_TALL_REGION_CHECK_FOR_FALSE_MERGE')
            n,_,stats,_ = cv2.connectedComponentsWithStats(ink.astype(np.uint8),8)
            if sum(int(st[cv2.CC_STAT_AREA]) >= ink.size*.1 for st in stats[1:]) > 1:
                result['warnings'].append('SEPARATED_LARGE_CONTENT_COMPONENTS_CHECK_GROUPING')
    except Exception as exc:
        result['errors'].append(str(exc))
    result['errors'] = list(dict.fromkeys(result['errors']))
    if result['errors']:
        result['pixel_fidelity'] = 'FAIL' if verify_pixels else 'NOT_RUN'
    result['file_integrity'] = 'FAIL' if result['errors'] else 'PASS'
    return result,sample


def _order_report(manifest,sources):
    issues = []
    orders = [s.get('order') for s in sources]
    if len(set(orders)) != len(orders) or orders != sorted(orders):
        issues.append('DUPLICATE_OR_REORDERED_SOURCE_ORDER')
    for left,right in zip(sources,sources[1:]):
        if left['y1'] != right['y0']:
            issues.append('CHAPTER_SOURCE_COORDINATE_GAP_OR_OVERLAP')
    numbered = []
    for s in sources:
        match = re.search(r'(\d+)\.[^.]+$',s.get('original_name',''))
        if match:
            numbered.append(int(match.group(1)))
    missing = list(manifest.get('missing_sources') or [])
    if len(numbered) == len(sources) and numbered and max(numbered)-min(numbered) <= max(5,len(sources)*4):
        missing.extend(i for i in range(min(numbered),max(numbered)+1) if i not in numbered)
        if len(set(numbered)) != len(numbered):
            issues.append('REPEATED_NUMERIC_SOURCE_NAME')
        if numbered != sorted(numbered):
            issues.append('NUMERIC_SOURCE_NAME_ORDER_REQUIRES_REVIEW')
    if missing:
        issues.append('MISSING_SOURCE_SEQUENCE_MEMBERS')
    preflight = manifest.get('preflight') or {}
    human_confirmed = preflight.get('order_confirmed') is True and preflight.get('reviewer') in {'user','human'}
    return {'status':'REVIEW_REQUIRED' if issues or not human_confirmed else 'PASS',
            'issues':list(dict.fromkeys(issues)),'missing':missing,
            'human_order_confirmation':human_confirmed,
            'note':'Numeric and coordinate consistency do not prove narrative reading order.'}


def run_qa(root, manifest, checkpoint=None, verify_pixels=True):
    """Run independent checks, persist reports/qa.json, and never approve a crop.

    checkpoint, when supplied, receives ('QA', completed, total, stable_id).
    No classification, status, source file or artwork is changed by this function.
    """
    root = Path(root).expanduser().resolve()
    sources = sorted(manifest.get('sources',[]),key=lambda s:s['y0'])
    panels = [p for p in manifest.get('panels',[]) if _active(p)]
    cache = _SourceCache(root)
    resolved_groups = _resolved_groups(manifest)
    report = {'version':QA_VERSION,'generated_at':datetime.now(timezone.utc).isoformat(),
              'status':'REVIEW_REQUIRED','automatic_approval':False,
              'algorithm_version':manifest.get('algorithm_version'),
              'prediction_geometry_sha256':_prediction_key(manifest),
              'scope':'Pixel and geometric evidence only; panel semantics require human inspection.',
              'thresholds':{'ink_min_channel':[240,248,252],'sobel_edge_sum':6,
                            'uncovered_min_component_pixels':1},
              'per_source':[],'per_region':[],'overlaps':[],'near_duplicates':[],
              'unresolved_groups':[],'errors':[], 'required_checks':{}}
    source_errors = 0 if sources else 1
    if not sources:
        report['errors'].append('NO_INDEXED_ORIGINAL_SOURCES')
    region_crossings = {p['id']:[] for p in panels}
    region_boundary_observations = {p['id']:[] for p in panels}
    invalid_regions = {}
    # Validate footprints once; invalid metadata is never silently counted covered.
    valid_panels = []
    for p in panels:
        try:
            _bounds(p)
            _polygon(p)
            valid_panels.append(p)
        except Exception as exc:
            invalid_regions[p.get('id','unknown')] = str(exc)
    total_work = len(sources)+len(panels)
    coverage_totals = Counter()
    for source_index,source in enumerate(sources):
        _checkpoint(checkpoint,source_index,total_work,source['id'])
        item = {'source_id':source['id'],'width':source['width'],'height':source['height'],
                'integrity':'PASS','errors':[],'uncovered_components':[]}
        try:
            path = _path(root,source['path'])
            actual_sha = _sha(path)
            item['sha256'] = actual_sha
            if not source.get('sha256'):
                item['integrity'] = 'UNRESOLVED'
                item['errors'].append('NO_ORIGINAL_SOURCE_CHECKSUM')
            elif actual_sha != source['sha256']:
                item['integrity'] = 'FAIL'
                item['errors'].append('SOURCE_CHECKSUM_CHANGED')
            rgba = cache.get(source)
            masks,rgb = _ink(rgba)
            fg = masks['foreground']
            proposal = np.zeros(fg.shape,dtype=bool)
            review = np.zeros(fg.shape,dtype=bool)
            multiplicity = np.zeros(fg.shape,dtype=np.uint16)
            intersecting = [p for p in valid_panels if p['bounds'][1] < source['y1'] and p['bounds'][3] > source['y0']]
            for p in intersecting:
                mask = _mask_for(p,source)
                if _review_only(p,resolved_groups):
                    review |= mask
                else:
                    proposal |= mask
                multiplicity += mask
                crossing = _crossings(mask,fg,source,p,masks['strong'],masks['standard'])
                if crossing['faint_or_edge_crossing_pixels'] or crossing['native_source_edge_content_pixels']:
                    region_boundary_observations[p['id']].append(crossing)
                if crossing['suspected_cut']:
                    region_crossings[p['id']].append(crossing)
            represented = proposal | review
            uncovered = fg & ~represented
            blank_declared = np.zeros(fg.shape,dtype=bool)
            for gutter in (manifest.get('analysis') or {}).get('gutters',[]):
                a,b = max(source['y0'],int(gutter['y0'])),min(source['y1'],int(gutter['y1']))
                if a < b:
                    blank_declared[a-source['y0']:b-source['y0']] = True
            declared_ink = blank_declared & fg & ~represented
            thumbnail = f"reports/coverage/{source['id']}.png"
            components = _components(uncovered,source,thumbnail,masks,rgb)
            if components:
                _source_thumbnail(root,source,rgb,components)
            item.update({'foreground_pixels':int(fg.sum()),
                         'foreground_by_threshold':{k:int(v.sum()) for k,v in masks.items() if k!='foreground'},
                         'illustration_proposal_foreground_pixels':int((proposal & fg).sum()),
                         'review_only_foreground_pixels':int((review & ~proposal & fg).sum()),
                         'intentional_blank_gutter_pixels':int((blank_declared & ~fg & ~represented).sum()),
                         'unclassified_background_pixels':int((~fg & ~represented & ~blank_declared).sum()),
                         'uncovered_foreground_pixels':int(uncovered.sum()),
                         'uncovered_strong_pixels':int((uncovered & masks['strong']).sum()),
                         'uncovered_standard_pixels':int((uncovered & masks['standard']).sum()),
                         'uncovered_faint_only_pixels':int((uncovered & masks['faint'] & ~masks['standard']).sum()),
                         'uncovered_edge_only_pixels':int((uncovered & masks['edge'] & ~masks['faint']).sum()),
                         'declared_blank_contains_foreground_pixels':int(declared_ink.sum()),
                         'overlap_foreground_pixels':int(((multiplicity > 1) & fg).sum()),
                         'uncovered_components':components,'coverage_thumbnail':thumbnail if components else None})
            for key in ('foreground_pixels','illustration_proposal_foreground_pixels','review_only_foreground_pixels',
                        'intentional_blank_gutter_pixels','unclassified_background_pixels','uncovered_foreground_pixels',
                        'uncovered_strong_pixels','uncovered_standard_pixels','uncovered_faint_only_pixels','uncovered_edge_only_pixels','overlap_foreground_pixels'):
                coverage_totals[key] += item[key]
        except Exception as exc:
            item['integrity'] = 'FAIL'
            item['errors'].append(str(exc))
        if item['integrity'] != 'PASS':
            source_errors += 1
        report['per_source'].append(item)
    fingerprints = {}
    for index,p in enumerate(panels):
        _checkpoint(checkpoint,len(sources)+index,total_work,p.get('id','unknown'))
        if p.get('id') in invalid_regions:
            result,sample = {'id':p.get('id'),'errors':[invalid_regions[p['id']]],'warnings':[],
                             'file_integrity':'FAIL','pixel_fidelity':'FAIL','pixels_compared':0},None
        else:
            result,sample = _region_file(root,p,sources,cache,verify_pixels)
        try:
            result['source_mapping'] = _mapping_validation(p,sources)
        except Exception as exc:
            result['source_mapping'] = {'status':'FAIL','errors':[str(exc)]}
        projection = p.get('output_file')
        if projection:
            try:
                projected_path = _path(root,projection)
                if not projected_path.is_file():
                    raise ValueError('MISSING_OUTPUT_PROJECTION')
                if result.get('sha256') and _sha(projected_path) != result['sha256']:
                    raise ValueError('OUTPUT_PROJECTION_DIFFERS_FROM_IMMUTABLE_REVISION')
                result['projection_integrity'] = 'PASS'
            except Exception as exc:
                result['projection_integrity'] = 'FAIL'
                result['errors'].append(str(exc))
        result['linework_crossings'] = region_crossings.get(p.get('id'),[])
        result['boundary_observations'] = region_boundary_observations.get(p.get('id'),[])
        if result['linework_crossings']:
            result['warnings'].append('CUT_BOUNDARY_INTERSECTS_SIGNIFICANT_ORIGINAL_LINEWORK')
        result['human_verified'] = _human(p)
        result['approved_artwork'] = _approved(p)
        result['classification'] = p.get('classification','illustration')
        if p.get('status') == 'VERIFIED' and not _human(p):
            result['warnings'].append('VERIFIED_LABEL_IS_NOT_A_HUMAN_APPROVAL')
        if p.get('classification') == 'compound_layout' or (bool(_group(p)) and _group(p) not in resolved_groups) or (bool(p.get('alternatives') or p.get('children')) and p.get('id') not in resolved_groups):
            report['unresolved_groups'].append({'id':p['id'],'group':_group(p),
                'reason':'Compound/alternative remains review-only until explicit grouping decision.'})
        if _approved(p) and not str(p.get('output_file') or p.get('file','')).startswith('approved_artwork/'):
            result['warnings'].append('APPROVED_ARTWORK_ROUTING_MISMATCH')
        if not _approved(p) and str(p.get('output_file') or p.get('file','')).startswith('approved_artwork/'):
            result['errors'].append('UNVERIFIED_CONTENT_IN_APPROVED_ARTWORK')
        if sample is not None:
            fingerprints[p['id']] = _fingerprint(sample)
        report['per_region'].append(result)
    # Pairwise metadata/perceptual work is cheap: no source rasters held per pair.
    duplicate_edges = []
    for i,p in enumerate(valid_panels):
        for q in valid_panels[i+1:]:
            intersection = _box_intersection(p['bounds'],q['bounds'])
            if intersection:
                report['overlaps'].append({'ids':[p['id'],q['id']], 'bbox_intersection_pixels':intersection,
                    'bbox_iou':round(_iou(p['bounds'],q['bounds']),5),
                    'kind':'alternative_group' if _group(p) and _group(p)==_group(q) else 'spatial_overlap',
                    'note':'Bounding-box overlap alone does not prove duplicate artwork; polygons may be disjoint.'})
            if p['id'] not in fingerprints or q['id'] not in fingerprints:
                continue
            pa,pb = p['bounds'],q['bounds']
            aspect_a,aspect_b = (pa[2]-pa[0])/(pa[3]-pa[1]),(pb[2]-pb[0])/(pb[3]-pb[1])
            if abs(math.log(aspect_a/aspect_b)) > .10:
                continue
            ha,hista = fingerprints[p['id']]
            hb,histb = fingerprints[q['id']]
            distance = int((ha^hb).bit_count())
            hist_distance = float(cv2.compareHist(hista,histb,cv2.HISTCMP_BHATTACHARYYA))
            exact = bool(p.get('sha256') and p.get('sha256') == q.get('sha256'))
            if exact or distance <= 6 and hist_distance < .16:
                duplicate_edges.append({'ids':[p['id'],q['id']], 'exact_bytes':exact,
                                        'dhash_distance':distance,'histogram_distance':round(hist_distance,5),
                                        'status':'REVIEW_REQUIRED'})
    report['near_duplicates'] = duplicate_edges
    parent = {}
    def find(x):
        parent.setdefault(x,x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for edge in duplicate_edges:
        a,b = map(find,edge['ids'])
        parent[b] = a
    groups = {}
    for key in parent:
        groups.setdefault(find(key),[]).append(key)
    report['duplicate_groups'] = list(groups.values())
    order = _order_report(manifest,manifest.get('sources',[]))
    broken = [p['id'] for p in report['per_region'] if p.get('errors')]
    pixel_failed = [p['id'] for p in report['per_region'] if p.get('pixel_fidelity') == 'FAIL']
    report['seam_risks'] = _seam_risks(manifest)
    suspected_seams = [j for j in report['seam_risks'] if j['actual_integrity_risk'] and j['used_by_regions']]
    uncertain_seams = [j for j in report['seam_risks'] if j['kind']=='uncertain_continuation_requires_inspection']
    ordinary_unreviewed_seams = [j for j in report['seam_risks'] if j['kind']=='ordinary_exact_row_append_unreviewed']
    unreviewed_seams = [j for j in report['seam_risks'] if j['requires_human_inspection']]
    unused_adjacencies = [j for j in report['seam_risks'] if not j['used_by_regions']]
    unused_risky_adjacencies = [j for j in unused_adjacencies if j['actual_integrity_risk']]
    approved = sum(_approved(p) for p in panels)
    reviewed_nonart = sum(_human(p) and p.get('classification') != 'illustration' for p in panels)
    classifications = Counter(p.get('classification','illustration') for p in panels)
    tasks=[p for p in manifest.get('panels',[]) if p.get('review_task')]
    report['source_review_tasks']=[{'id':p['id'],'bounds':p['bounds'],'source_regions':p.get('source_regions',[]),'markers':p.get('source_review',{}).get('boundary_markers',[])} for p in tasks]
    required = report['required_checks']
    required['source_review_tasks']={'status':'REVIEW_REQUIRED' if tasks else 'PASS','count':len(tasks)}
    required['source_integrity'] = {'status':'FAIL' if source_errors else 'PASS','failed_sources':source_errors}
    required['pixel_fidelity'] = {'status':'NOT_RUN' if not verify_pixels else 'FAIL' if broken or pixel_failed or source_errors else 'PASS',
                                  'checked_regions':len(panels),'failed_regions':pixel_failed,
                                  'pixels_compared':sum(p.get('pixels_compared',0) for p in report['per_region'])}
    mapping_statuses = [p['source_mapping']['status'] for p in report['per_region']]
    required['source_coordinate_mapping'] = {'status':'FAIL' if 'FAIL' in mapping_statuses else 'REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in mapping_statuses else 'PASS',
        'failed_regions':[p['id'] for p in report['per_region'] if p['source_mapping']['status']=='FAIL']}
    required['source_coverage'] = {'status':'FAIL' if source_errors else 'REVIEW_REQUIRED' if coverage_totals['uncovered_foreground_pixels'] else 'PASS',
                                   'uncovered_foreground_pixels':coverage_totals['uncovered_foreground_pixels'],
                                   'note':'At tested ink/edge thresholds only; does not establish semantic illustration completeness.'}
    required['panel_boundaries'] = {'status':'PASS' if panels and all(_human(p) for p in panels) and not report['unresolved_groups'] else 'REVIEW_REQUIRED',
                                    'suspected_linework_cuts':sum(bool(p.get('linework_crossings')) for p in report['per_region']),
                                    'note':'Algorithmic geometry warnings require visual inspection, not automatic verification.'}
    required['joining'] = {'status':'REVIEW_REQUIRED' if suspected_seams or unreviewed_seams else 'PASS',
                           'suspicious_seams':len(suspected_seams),'uncertain_seams':len(uncertain_seams),
                           'ordinary_unreviewed_seams':len(ordinary_unreviewed_seams),'unreviewed_seams':len(unreviewed_seams),
                           'unused_source_adjacencies':len(unused_adjacencies),'unused_preflight_adjacency_risks':len(unused_risky_adjacencies),
                           'note':'Actual integrity-risk evidence is separate from ordinary unchanged seams awaiting human inspection.'}
    required['reading_order'] = order
    required['classification'] = {'status':'PASS' if panels and all(_human(p) for p in panels) and not report['unresolved_groups'] else 'REVIEW_REQUIRED',
                                   'counts':dict(classifications)}
    required['human_verification'] = {'status':'PASS' if panels and all(_human(p) for p in panels) else 'REVIEW_REQUIRED',
                                      'human_checked_regions':sum(_human(p) for p in panels),
                                      'automatic_or_assistant_verified_labels':sum(p.get('status') == 'VERIFIED' and not _human(p) for p in panels)}
    required['export_files'] = {'status':'FAIL' if broken else 'PASS','broken_or_missing':broken}
    required['duplicates_and_overlap'] = {'status':'REVIEW_REQUIRED' if duplicate_edges or report['overlaps'] else 'PASS',
                                         'duplicate_groups':len(groups),'overlap_pairs':len(report['overlaps'])}
    benchmark_file = root/'reports'/'benchmark.json'
    benchmark = json.loads(benchmark_file.read_text()) if benchmark_file.exists() else {}
    benchmark_accepted = benchmark.get('acceptance_gates_pass') is True and benchmark.get('annotation_reviewer') in {'user','human'} and not benchmark.get('provisional_labels',True) and benchmark.get('prediction_geometry_sha256') == _prediction_key(manifest)
    required['benchmark_acceptance'] = {'status':'PASS' if benchmark_accepted else 'REVIEW_REQUIRED' if benchmark else 'NOT_RUN',
        'note':'No release claim from missing or provisional assistant-annotated reference labels.'}
    report['totals'] = {'strips_indexed':len(sources),'excluded_strips':len(manifest.get('excluded',[])),
                        'missing_strips':len(order['missing']),'proposed_crops':len(panels),
                        'approved_artwork':approved,'human_reviewed_non_artwork':reviewed_nonart,
                        'needs_review':sum(not _human(p) for p in panels),
                        'rejected_crops':sum(p.get('status') == 'REJECTED' for p in manifest.get('panels',[])),
                        'text_only_regions':sum(classifications[k] for k in TEXT_CLASSES),
                        'unresolved_compound_or_alternative_regions':len(report['unresolved_groups']),
                        'suspicious_seams':len(suspected_seams),'uncertain_seams':len(uncertain_seams),
                        'ordinary_unreviewed_seams':len(ordinary_unreviewed_seams),'unreviewed_seams':len(unreviewed_seams),
                        'unused_source_adjacencies':len(unused_adjacencies),'preflight_source_adjacency_risks':len(unused_risky_adjacencies),
                        'duplicate_groups':len(groups),
                        'broken_or_missing_files':len(broken),'source_integrity_errors':source_errors,
                        'uncovered_components':sum(len(s.get('uncovered_components',[])) for s in report['per_source']),
                        **dict(coverage_totals)}
    _review_priorities(report,manifest)
    component_types = report['risk_totals']['uncovered_components_by_type']
    report['totals'].update(meaningful_uncovered_artwork_candidates=component_types.get('meaningful_uncovered_artwork',0),
                            unclassified_uncovered_content_components=component_types.get('unclassified_text_or_sparse_art',0),
                            compression_or_faint_detail_components=component_types.get('compression_or_faint_detail_requires_review',0),
                            suspected_interior_cutoff_regions=sum(r.get('cutoff_risk',False) for r in report['per_region']),
                            retained_text_regions=sum(classifications[k] for k in TEXT_CLASSES),
                            retained_unclassified_regions=classifications.get('uncertain',0))
    report['resources'] = {'source_cache_limit_bytes':cache.budget,'cached_source_limit':2,
                            'maximum_cached_bytes_observed':cache.max_bytes_observed,
                            'full_chapter_bitmap_allocated':False,'long_png_decode':'row_stream'}
    if any(check['status'] == 'FAIL' for check in required.values()):
        report['status'] = 'FAILED'
    elif all(check['status'] == 'PASS' for check in required.values()):
        report['status'] = 'REVIEW_COMPLETE'
    report['production_export_enabled'] = report['status'] == 'REVIEW_COMPLETE'
    report['zero_claim_validation'] = {'qa_did_not_mark_any_region_verified':True,
        'no_false_human_verified_claims':not any(p.get('status')=='VERIFIED' and not _human(p) for p in panels),
        'pixel_fidelity_is_not_panel_correctness':True,'no_benchmark_claim_without_manual_labels':True,
        'assistant_review_is_not_human_approval':True}
    _write_json(root/'reports'/'qa.json',report)
    _checkpoint(checkpoint,total_work,total_work,'qa_complete')
    return report


def compare_gold(root, manifest, goldfile):
    """Compare proposals to externally supplied, explicitly reviewed bounds.

    The bounding-box metric is intentionally named; this is not semantic object
    completeness and does not create or approve gold labels. Polygon annotations
    are retained in the input but boundary scores currently use their bounds.
    """
    root = Path(root).resolve()
    goldpath = Path(goldfile)
    gold = json.loads(goldpath.read_text(encoding='utf-8'))
    labels = gold.get('regions',gold.get('annotations',[]))
    provenance = gold.get('annotation_method') or gold.get('labels_source') or ''
    reviewed = provenance in {'manual_visual_inspection','manual_annotation','human_reviewed'}
    reviewed = reviewed and all(g.get('reviewed') is True or g.get('status') in {'MANUALLY_REVIEWED','HUMAN_REVIEWED'} for g in labels)
    result = {'status':'NOT_EVALUATED','gold_file':str(goldpath),'gold_sha256':_sha(goldpath),
              'annotation_method':provenance,'label_count':len(labels),
              'prediction_geometry_sha256':_prediction_key(manifest),
              'algorithm_version':manifest.get('algorithm_version'),
              'metric':'reviewed_axis_aligned_bounds_iou_and_edge_tolerance',
              'definitions':{'match_iou':.5,'boundary_tolerance_pixels':4,
                             'severe_cutoff':'matched illustration retains less than 95% of reviewed bounds area',
                             'missed_artwork':'no selected illustration proposal retains 10% of reviewed bounds area; retained review compounds are reported separately, not silently lost content'},
              'limitations':'Bounds metrics are geometric evidence; they do not establish every character/object is complete.'}
    if not labels or not reviewed:
        result['reason'] = 'No explicitly manually reviewed annotation set; no score fabricated.'
        _write_json(root/'reports'/'benchmark.json',result)
        return result
    expected_sources = gold.get('source_checksums') or {}
    actual_sources = {s['id']:s.get('sha256') for s in manifest.get('sources',[])}
    if any(actual_sources.get(key) != digest for key,digest in expected_sources.items()):
        result['reason'] = 'Gold source checksums do not match this chapter; no benchmark score computed.'
        _write_json(root/'reports'/'benchmark.json',result)
        return result
    all_predictions = [p for p in manifest.get('panels',[]) if _active(p) and p.get('classification','illustration') == 'illustration' and not _review_only(p,_resolved_groups(manifest))]
    windows = gold.get('evaluation_windows') or [g['bounds'] for g in labels]
    predictions = [p for p in all_predictions if any(_box_intersection(_bounds(p),window)>0 for window in windows)]
    result.update(evaluation_scope='reviewed_region_windows_only',
        precision_scope='Conditional selected-window precision, not whole-chapter precision. Unlabeled chapter predictions are not false positives.',
        chapter_predictions_not_scored=len(all_predictions)-len(predictions),
        annotation_reviewer=gold.get('reviewer'), provisional_labels=gold.get('provisional',False))
    artworks = [g for g in labels if g.get('classification','illustration') in {'illustration','embedded_sfx_or_bubble'}]
    matches, used = [],set()
    for g in artworks:
        available = [(i,_iou(_bounds(g),_bounds(p))) for i,p in enumerate(predictions) if i not in used]
        if available:
            index,iou = max(available,key=lambda pair:pair[1])
            if iou >= .5:
                used.add(index)
                matches.append((g,predictions[index],iou))
    true_boundaries = 0
    for g,p,_ in matches:
        true_boundaries += sum(abs(a-b)<=4 for a,b in zip(_bounds(g),_bounds(p)))
    missed,cutoffs,splits,merges,wrong_sfx,gutter = [],[],[],[],[],[]
    retained_for_review,not_in_any_active_region = [],[]
    all_active_regions = [p for p in manifest.get('panels',[]) if _active(p)]
    details = []
    for g in artworks:
        gb = _bounds(g)
        retaining = [(p,_box_intersection(gb,_bounds(p))/max(1,_area(gb))) for p in predictions]
        best = max(retaining,key=lambda pair:pair[1],default=(None,0))
        gid = g.get('id',str(gb))
        if best[1] < .1:
            missed.append(gid)
            retained = [p['id'] for p in all_active_regions if _box_intersection(gb,_bounds(p))/max(1,_area(gb)) >= .95]
            if retained:
                retained_for_review.append({'gold_id':gid,'region_ids':retained})
            else:
                not_in_any_active_region.append(gid)
        elif best[1] < .95:
            cutoffs.append(gid)
        pieces = [p['id'] for p,coverage in retaining if coverage >= .05]
        if len(pieces)>1 and best[1] < .95:
            splits.append({'gold_id':gid,'prediction_ids':pieces})
        if best[0]:
            pb = _bounds(best[0])
            gutter.append(1-_box_intersection(gb,pb)/max(1,_area(pb)))
        details.append({'gold_id':gid,'best_prediction':best[0]['id'] if best[0] else None,
                        'retained_bounds_fraction':round(best[1],6),
                        'best_iou':max((_iou(gb,_bounds(p)) for p in predictions),default=0)})
    for p in predictions:
        covered = [g.get('id',str(g['bounds'])) for g in artworks if _box_intersection(_bounds(g),_bounds(p))/max(1,_area(_bounds(g))) >= .8]
        if len(covered)>1:
            merges.append({'prediction_id':p['id'],'gold_ids':covered})
    for g in labels:
        if g.get('classification') in {'standalone_sfx','title_or_credits','dialogue_or_narration_only','title_card','text_only'}:
            wrong = [p['id'] for p in predictions if _iou(_bounds(g),_bounds(p)) >= .5]
            if wrong:
                wrong_sfx.append({'gold_id':g.get('id'),'classification':g.get('classification'),'wrong_artwork_ids':wrong})
    qa_path = root/'reports'/'qa.json'
    qa = json.loads(qa_path.read_text()) if qa_path.exists() else None
    fidelity_pass = bool(qa and qa.get('required_checks',{}).get('pixel_fidelity',{}).get('status')=='PASS')
    false_verified = sum(p.get('status') == 'VERIFIED' and not _human(p) for p in manifest.get('panels',[]))
    result.update({'status':'EVALUATED','artwork_labels':len(artworks),'illustration_predictions':len(predictions),
                   'matches_at_iou_0_5':len(matches),
                   'panel_precision':len(matches)/max(1,len(predictions)),
                   'panel_recall':len(matches)/max(1,len(artworks)),
                   'boundary_precision':true_boundaries/max(1,4*len(predictions)),
                   'boundary_recall':true_boundaries/max(1,4*len(artworks)),
                   'false_merges':merges,'false_splits':splits,'missed_artwork':missed,
                   'missed_illustration_outputs_retained_for_review':retained_for_review,
                   'gold_regions_not_represented_in_any_active_region':not_in_any_active_region,
                   'severe_cutoff_count':len(cutoffs),'severe_cutoff_gold_ids':cutoffs,
                   'wrong_text_sfx_routing':wrong_sfx,
                   'mean_exterior_gutter_retention_fraction':sum(gutter)/max(1,len(gutter)),
                   'review_workload':sum(_active(p) and not _human(p) for p in manifest.get('panels',[])),
                   'false_verified_claims':false_verified,'pixel_fidelity_pass':fidelity_pass,
                   'per_gold_region':details})
    result['acceptance_gates_pass'] = not (cutoffs or missed or false_verified) and fidelity_pass
    result['production_export_enabled'] = False
    result['production_note'] = 'Benchmark success alone does not approve unreviewed chapter proposals.'
    _write_json(root/'reports'/'benchmark.json',result)
    return result
