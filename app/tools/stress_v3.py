"""Representative V3 stress/crash harness. All artwork here is SYNTHETIC.

Generate full-size 690x1280 strips, then run a measured subprocess. No benchmark
result is claimed until the resulting JSON records a completed actual run.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import shutil
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
WIDTH, STRIP_HEIGHT, CYCLE = 690, 1280, 15360


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    tmp.replace(path)


def scene_definitions():
    # Full row, 2/3-per-strip, 2-source, and 4-source scenes are all represented.
    return [
        ('full_bleed', [0, 0, 690, 1240]),
        ('two_per_strip_A', [24, 1300, 665, 1810]),
        ('two_per_strip_B', [60, 1870, 625, 2520]),
        ('two_source', [15, 2580, 675, 4930]),
        ('three_per_strip_A', [25, 5150, 660, 5470]),
        ('three_per_strip_B_12px_gap', [75, 5482, 615, 5820]),
        ('three_per_strip_C_6px_gap', [40, 5826, 650, 6360]),
        ('four_source_tall', [10, 6500, 680, 11210]),
        ('colored_gutter_scene', [80, 11410, 610, 12500]),
        ('dialogue_narration_only', [105, 12600, 585, 13020]),
        ('borderless_bubble', [0, 13100, 690, 15320]),
    ]


def generate_fixture(directory, strips=200, corrupt_at=None):
    """Only one full-size strip is allocated at a time; dimensions are never tiny."""
    import numpy as np
    from PIL import Image, ImageDraw
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    input_dir = directory / 'input'
    input_dir.mkdir(exist_ok=True)
    definitions = scene_definitions()
    expected, originals = [], []
    for cycle in range((strips * STRIP_HEIGHT + CYCLE - 1) // CYCLE):
        for name, bounds in definitions:
            b = [bounds[0], bounds[1] + cycle * CYCLE, bounds[2], min(bounds[3] + cycle * CYCLE, strips * STRIP_HEIGHT)]
            if b[1] < b[3]:
                expected.append({'name': f'{name}_{cycle:03d}', 'bounds': b})
    for index in range(1, strips + 1):
        y0, y1 = (index - 1) * STRIP_HEIGHT, index * STRIP_HEIGHT
        # Slightly warm paper, modest JPEG ringing, and colored separator bands.
        paper = (255, 255, 255) if index % 4 else (248, 246, 239)
        image = Image.new('RGB', (WIDTH, STRIP_HEIGHT), paper)
        draw = ImageDraw.Draw(image)
        cyc = y0 // CYCLE
        for repeat in range(max(0, cyc - 1), cyc + 2):
            off = repeat * CYCLE
            for top, bottom, color in ((11210, 11410, (196, 215, 228)), (12500, 12600, (235, 220, 202))):
                a, b = max(y0, off + top), min(y1, off + bottom)
                if a < b:
                    draw.rectangle((0, a-y0, WIDTH-1, b-y0-1), fill=color)
        for number, item in enumerate(expected):
            x0, sy0, x1, sy1 = item['bounds']
            a, b = max(y0, sy0), min(y1, sy1)
            if a >= b:
                continue
            w, h = x1-x0, b-a
            yy, xx = np.mgrid[a-sy0:b-sy0, :w]
            pixels = np.empty((h, w, 3), dtype=np.uint8)
            pixels[:, :, 0] = (30 + xx * 60 // max(w, 1) + number * 13) % 190
            pixels[:, :, 1] = 55 + (yy * 3 // 19) % 120
            pixels[:, :, 2] = 100 + (xx // 6 + yy // 9) % 100
            panel = Image.fromarray(pixels)
            image.paste(panel, (x0, a-y0))
            panel.close()
            draw = ImageDraw.Draw(image)
            # Geometric silhouette/weapon span seams without resetting per strip.
            center = (x0+x1)//2
            for global_row in range(max(sy0+80, a-120), min(sy1, b+120), 180):
                draw.line((x0+10, global_row-y0, x1-11, global_row+100-y0), fill=(82, 192, 174), width=5)
            head = sy0 + min(250, (sy1-sy0)//3)
            draw.ellipse((center-55, head-55-y0, center+55, head+55-y0), fill=(232, 190, 146), outline=(15, 22, 35), width=4)
            draw.polygon([(center-45, head+35-y0), (center+45, head+35-y0), (center+70, sy1-24-y0), (center-65, sy1-24-y0)], fill=(92, 45, 119))
            draw.line((x0+14, head+110-y0, x1-15, head+170-y0), fill=(235, 218, 140), width=10)
            if 'dialogue' in item['name']:
                draw.rectangle((x0+12, a-y0, x1-13, b-y0-1), fill='white')
                for line in range(35, sy1-sy0-20, 50):
                    draw.text((x0+35, sy0+line-y0), 'SYNTHETIC STORY DIALOGUE - RETAIN FOR REVIEW', fill=(24, 24, 30))
            elif number % 3 == 0:
                bubble_y = sy0 + 75
                draw.ellipse((x0+20, bubble_y-y0, min(x1-20, x0+245), bubble_y+115-y0), fill='white', outline=(15, 20, 30), width=4)
                draw.text((x0+42, bubble_y+35-y0), 'KEEP ORIGINAL\nBUBBLE + SFX', fill=(15, 20, 30))
        draw.text((6, 6), f'SYNTHETIC STRESS STRIP {index:04d}/{strips:04d}', fill=(12, 12, 20))
        # Every third source is JPEG; strip 90 is a genuine JPEG before corruption.
        extension = '.jpg' if index % 3 == 0 else '.png'
        path = input_dir / f'STRESS_{index:04d}{extension}'
        if extension == '.jpg':
            image.save(path, quality=96, subsampling=0)
        else:
            image.save(path)
        image.close()
        if corrupt_at == index:
            # Header and partial encoded payload: never a valid full decode.
            path.write_bytes(path.read_bytes()[:113])
        originals.append({'order':index, 'name':path.name, 'sha256':sha256(path), 'bytes':path.stat().st_size, 'corrupt':corrupt_at==index})
    metadata = {'synthetic':True, 'strips':strips, 'width':WIDTH, 'height':STRIP_HEIGHT,
                'total_pixels':strips*WIDTH*STRIP_HEIGHT, 'corrupt_at':corrupt_at,
                'originals':originals, 'expected_geometry':expected,
                'coverage':'full bleed; 2/3 panels per source; 2/4-strip art; 6/12px white and off-white gutters; colored separators; bubbles; text; tall scene',
                'warning':'Geometry fixtures and throughput measurements do not establish semantic completeness on real manga.'}
    write_json(directory/'fixture.json', metadata)
    return metadata


def process_metrics(pid):
    """Linux sampling without an optional psutil dependency; None on other OSes."""
    try:
        status = Path(f'/proc/{pid}/status').read_text()
        rss = next(int(line.split()[1])*1024 for line in status.splitlines() if line.startswith('VmRSS:'))
        # Parenthesized command can contain spaces; numeric fields begin after ).
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        cpu = (int(fields[11])+int(fields[12])) / os.sysconf('SC_CLK_TCK')
        return {'rss_bytes':rss, 'cpu_seconds':cpu}
    except (OSError, ValueError, StopIteration, AttributeError):
        return {'rss_bytes':None, 'cpu_seconds':None}


def read_status(output_dir):
    try:
        from manga_tool.pipeline import job_status
        return job_status(output_dir)
    except Exception as error:
        return {'not_ready':str(error)}


CHILD_CODE = '''import hashlib, json, os, signal, sys
from pathlib import Path
expected = os.environ.get('MANGA_EXPECTED_ALGORITHM')
if expected:
    from manga_tool.detection import ALGORITHM_VERSION
    if ALGORITHM_VERSION != expected:
        raise RuntimeError('Frozen detector version mismatch: '+ALGORITHM_VERSION+' != '+expected)
expected_files = json.loads(os.environ.get('MANGA_EXPECTED_PY_HASHES','{}'))
for rel, digest in expected_files.items():
    if hashlib.sha256(Path(rel).read_bytes()).hexdigest() != digest:
        raise RuntimeError('Frozen Python source changed: '+rel)
if os.environ.get('MANGA_TEST_SIGKILL') == '1':
    saved_exit = os._exit
    def forced_exit(code):
        if code == 86:
            os.kill(os.getpid(), signal.SIGKILL)
        saved_exit(code)
    os._exit = forced_exit
from manga_tool.storage import Store, JobPaused, JobCancelled
control = os.environ.get('MANGA_STRESS_CONTROL')
if control:
    saved_checkpoint = Store.checkpoint
    requested = False
    def controlled(self,stage,current,total,**info):
        global requested
        if not requested and stage == os.environ.get('MANGA_STRESS_CONTROL_STAGE','INDEX') and current == int(os.environ.get('MANGA_STRESS_CONTROL_AT','-1')):
            self.request_control(control)
            requested = True
        return saved_checkpoint(self,stage,current,total,**info)
    Store.checkpoint = controlled
from manga_tool.pipeline import process_chapter
try:
    result = process_chapter(sys.argv[1], sys.argv[2], chapter=os.environ.get('MANGA_STRESS_CHAPTER','STRESS'), min_gutter=int(os.environ.get('MANGA_STRESS_MIN_GUTTER','12')), generate_contact_sheets=False)
    print(json.dumps({'panels': len(result.get('panels', [])), 'job': result.get('job', {})}))
except JobPaused as exc:
    print(str(exc))
    sys.exit(3)
except JobCancelled as exc:
    print(str(exc))
    sys.exit(4)
'''



def run_process(fixture_dir, output_dir, log_dir, name='run', env=None, timeout=3600):
    fixture_dir, output_dir, log_dir = map(Path, (fixture_dir, output_dir, log_dir))
    log_dir.mkdir(parents=True, exist_ok=True)
    run_env = dict(os.environ)
    run_env.update(env or {})
    # Do not accidentally inherit hooks into a clean resume process.
    for key in ('MANGA_TEST_EXIT_STAGE','MANGA_TEST_EXIT_AT','MANGA_TEST_EXIT_POINT','MANGA_TEST_SIGKILL','MANGA_STRESS_CONTROL','MANGA_STRESS_CONTROL_STAGE','MANGA_STRESS_CONTROL_AT','MANGA_STRESS_CHAPTER','MANGA_STRESS_MIN_GUTTER'):
        if key not in (env or {}):
            run_env.pop(key, None)
    source_hashes_before=python_source_hashes()
    expected_hashes=json.loads(run_env.get('MANGA_EXPECTED_PY_HASHES','{}'))
    if expected_hashes and source_hashes_before != expected_hashes:
        raise AssertionError('Python source changed after the validation freeze')
    started = time.monotonic()
    peak_rss = 0
    last_cpu = 0.
    observed_cpu = False
    last_status = None
    progress_path = log_dir/f'{name}.progress.jsonl'
    with (log_dir/f'{name}.stdout.log').open('w') as stdout, (log_dir/f'{name}.stderr.log').open('w') as stderr, progress_path.open('w') as progress:
        proc = subprocess.Popen([sys.executable, '-u', '-c', CHILD_CODE, str(fixture_dir/'input'), str(output_dir)], cwd=ROOT, env=run_env, stdout=stdout, stderr=stderr)
        while proc.poll() is None:
            metrics = process_metrics(proc.pid)
            peak_rss = max(peak_rss, metrics['rss_bytes'] or 0)
            last_cpu = max(last_cpu, metrics['cpu_seconds'] or 0.)
            observed_cpu = observed_cpu or metrics['cpu_seconds'] is not None
            status = read_status(output_dir)
            recorded_status=compact_progress_status(status)
            record = {'elapsed_seconds':round(time.monotonic()-started,3), **metrics, 'job':recorded_status}
            progress.write(json.dumps(record, default=str)+'\n')
            progress.flush()
            last_status = recorded_status
            if time.monotonic()-started > timeout:
                proc.kill()
                proc.wait()
                raise TimeoutError(f'Stress subprocess exceeded {timeout}s; logs retained in {log_dir}')
            time.sleep(.20)
        returncode = proc.wait()
    elapsed = time.monotonic()-started
    metadata = json.loads((fixture_dir/'fixture.json').read_text())
    result = {'name':name, 'synthetic':True, 'strips':metadata['strips'], 'source_dimensions':[WIDTH,STRIP_HEIGHT],
              'elapsed_seconds':round(elapsed,3), 'peak_sampled_rss_bytes':peak_rss or None,
              'sampled_cpu_seconds':round(last_cpu,3) if observed_cpu else None, 'strips_per_second':round(metadata['strips']/elapsed,4),
              'returncode':returncode, 'status':read_status(output_dir), 'last_sampled_status':last_status,
              'expected_algorithm_version':run_env.get('MANGA_EXPECTED_ALGORITHM'),
              'python_source_hashes_before':source_hashes_before,'python_source_hashes_after':python_source_hashes(),
              'output_dir':str(output_dir.resolve()), 'progress_log':str(progress_path.resolve()),
              'measurement_note':'Shared managed host; concurrent jobs may affect wall time. 0.2s Linux /proc sampling. Peak RSS is observed, not a guaranteed upper bound. CPU may omit final sampling interval.'}
    write_json(log_dir/f'{name}.json', result)
    if expected_hashes and result['python_source_hashes_after'] != expected_hashes:
        raise AssertionError('Python source changed during the measured subprocess; retain logs but do not claim a frozen-build pass')
    return result


def png_rows(path):
    """Read streamed 8-bit RGB(A) PNG rows with bounded decompression memory."""
    decompressor = zlib.decompressobj()
    pending = bytearray()
    width = height = channels = None
    seen = 0
    with Path(path).open('rb') as f:
        if f.read(8) != b'\x89PNG\r\n\x1a\n':
            raise AssertionError(f'Not a PNG: {path}')
        while True:
            size_bytes = f.read(4)
            if not size_bytes:
                break
            size = struct.unpack('!I',size_bytes)[0]
            kind, payload, crc_bytes = f.read(4), f.read(size), f.read(4)
            assert len(payload)==size and len(crc_bytes)==4, 'Truncated PNG chunk'
            assert zlib.crc32(kind+payload)&0xffffffff == struct.unpack('!I',crc_bytes)[0], 'PNG CRC failure'
            if kind==b'IHDR':
                width,height,depth,color,compression,filter_method,interlace=struct.unpack('!2I5B',payload)
                assert depth==8 and color in (2,6) and interlace==0
                channels=3 if color==2 else 4
            if kind==b'IDAT':
                assert channels is not None
                compressed=payload
                stride=width*channels+1
                while compressed:
                    data=decompressor.decompress(compressed,stride*64)
                    pending.extend(data)
                    compressed=decompressor.unconsumed_tail
                    while len(pending)>=stride:
                        row=bytes(pending[:stride]); del pending[:stride]
                        assert row[0]==0, 'Expected the application streaming exporter to use PNG filter 0'
                        seen+=1
                        yield row[1:]
            if kind==b'IEND':
                break
    assert seen==height and not pending and decompressor.eof, 'Incomplete PNG rows'


def audit_output(fixture_dir, output_dir, report_path=None):
    from manga_tool.chapter import Chapter
    from manga_tool.pipeline import load_manifest
    fixture_dir, output_dir = Path(fixture_dir), Path(output_dir)
    metadata = json.loads((fixture_dir/'fixture.json').read_text())
    by_name = {item['name']:item for item in metadata['originals']}
    manifest = load_manifest(output_dir)
    original_checks, pixel_checks, pixel_count, errors = [], [], 0, []
    for source in manifest['sources']+manifest.get('invalid_sources',[]):
        name = Path(source.get('original_name',source['path'])).name
        original = by_name.get(name)
        copied = output_dir/source['path']
        ok = bool(original and copied.exists() and sha256(copied)==original['sha256'])
        original_checks.append({'source_id':source['id'],'name':name,'byte_identical':ok})
        if not ok:
            errors.append(f'Original mismatch/missing: {source["id"]}')
    # Even a corrupt imported resource must remain byte-identical and recoverable.
    checked_names={item['name'] for item in original_checks}
    for original in metadata['originals']:
        if original['name'] not in checked_names:
            copies=list((output_dir/'originals').glob('*_'+original['name']))
            ok=len(copies)==1 and sha256(copies[0])==original['sha256']
            original_checks.append({'name':original['name'],'byte_identical':ok,'not_in_valid_sources':True})
            if not ok:
                errors.append(f'Original not retained: {original["name"]}')
    reader = Chapter(output_dir, manifest['sources'])
    ids, referenced, checksums, unexported = [], set(), {}, []
    for panel in manifest.get('panels',[]):
        if panel.get('status') in ('MERGED','SUPERSEDED','DELETED'):
            continue
        if not panel.get('file'):
            unexported.append({'id':panel['id'],'status':panel.get('status'),'error':panel.get('error'),'flags':panel.get('flags',[])})
            if not (metadata.get('corrupt_at') or metadata.get('missing_at')):
                errors.append(f'Unexported region in clean fixture: {panel["id"]}')
            continue
        ids.append(panel['id'])
        path = output_dir/panel['file']
        referenced.add(path.resolve())
        if panel.get('output_file'):
            referenced.add((output_dir/panel['output_file']).resolve())
        if not path.is_file():
            errors.append(f'Missing crop: {panel["id"]}')
            continue
        checksums[panel['id']] = sha256(path)
        if checksums[panel['id']] != panel.get('sha256'):
            errors.append(f'Manifest checksum mismatch: {panel["id"]}')
        if panel.get('output_file'):
            projected=output_dir/panel['output_file']
            if not projected.is_file() or sha256(projected)!=checksums[panel['id']]:
                errors.append(f'Missing or corrupt named projection: {panel["id"]}')
        try:
            rows = png_rows(path)
            x0,y0,x1,y1 = reader.validate(panel['bounds'])
            count = 0
            for _, a, piece in reader.pieces(panel['bounds']):
                expected_piece=piece
                if panel.get('polygon'):
                    from PIL import Image, ImageDraw, ImageChops
                    expected_piece=piece.convert('RGBA')
                    alpha=Image.new('L',piece.size,0)
                    ImageDraw.Draw(alpha).polygon([(px-x0,py-a) for px,py in panel['polygon']],fill=255)
                    expected_piece.putalpha(ImageChops.multiply(expected_piece.getchannel('A'),alpha))
                    alpha.close()
                for y in range(expected_piece.height):
                    with expected_piece.crop((0,y,expected_piece.width,y+1)) as row:
                        assert next(rows)==row.tobytes(), f'Pixel mismatch row {count}'
                    count+=1
                if expected_piece is not piece:
                    expected_piece.close()
            try:
                next(rows)
                raise AssertionError('Surplus output rows')
            except StopIteration:
                pass
            assert count==y1-y0
            pixel_count+=(x1-x0)*(y1-y0)
            pixel_checks.append({'id':panel['id'],'identical':True,'rows':count})
        except Exception as error:
            errors.append(f'{panel["id"]}: {type(error).__name__}: {error}')
            pixel_checks.append({'id':panel['id'],'identical':False,'error':str(error)})
    if len(ids)!=len(set(ids)):
        errors.append('Duplicate stable panel IDs')
    # Superseded/history/reconstruction/seam PNGs are deliberate, not orphans.
    orphan_files=[]
    from manga_tool.storage import Store
    historical={(output_dir/rel).resolve() for rel in Store(output_dir).referenced_files()}
    for folder in ('proposals','needs_review','approved_artwork','cropped_panels','review/revisions'):
        for path in (output_dir/folder).rglob('*.png'):
            if path.resolve() not in referenced and path.resolve() not in historical:
                orphan_files.append(str(path.relative_to(output_dir)))
    if orphan_files:
        errors.append('Unreferenced active output files')
    temporary_files=[str(path.relative_to(output_dir)) for path in output_dir.rglob('*.tmp')]
    if temporary_files:
        errors.append('Unrecovered staging files')
    report={'synthetic':True,'source_count':len(manifest['sources']),'fixture_source_count':metadata['strips'],
            'panel_count':len(ids),'unexported_regions':unexported,'original_byte_checks':original_checks,'pixel_checks':pixel_checks,
            'pixels_compared':pixel_count,'errors':errors,'orphan_files':orphan_files,'temporary_files':temporary_files,
            'checksums':checksums,'classifications':dict(Counter(p.get('classification') for p in manifest.get('panels',[]))),
            'statuses':dict(Counter(p.get('status') for p in manifest.get('panels',[]))),
            'scope':'Exact bytes/pixels and persistence only. This does not establish semantic boundary correctness.'}
    if report_path:
        write_json(report_path,report)
    return report


def run_suite(directory, strips=200, include_interrupts=True, corrupt=False):
    directory=Path(directory)
    if include_interrupts and (os.name=='nt' or not hasattr(signal,'SIGKILL')):
        raise RuntimeError('Deterministic SIGKILL suites require POSIX. Use --no-interrupts for throughput on this OS; native Windows crash testing remains pending.')
    fixture=directory/'fixture'
    if not (fixture/'fixture.json').exists():
        generate_fixture(fixture,strips=strips,corrupt_at=90 if corrupt else None)
    output=directory/'output'
    logs=directory/'measurements'
    results=[]
    preserved={}
    manual_edit=None
    if include_interrupts:
        checkpoints=[('index_30_percent','INDEX',max(1,int(strips*.30)),None),
                     ('index_75','INDEX',75,None),
                     ('export_after_two','EXPORT',2,None),
                     ('export_before_commit','EXPORT',3,'before_commit')]
        for name,stage,current,point in checkpoints:
            environment={'MANGA_TEST_EXIT_STAGE':stage,'MANGA_TEST_EXIT_AT':str(current),'MANGA_TEST_SIGKILL':'1'}
            if point:
                environment['MANGA_TEST_EXIT_POINT']=point
            result=run_process(fixture,output,logs,name,environment)
            results.append(result)
            if result['returncode'] != -signal.SIGKILL:
                raise AssertionError(f'Expected genuine SIGKILL at {stage} {current}; got {result["returncode"]}. See {logs}.')
            if name=='export_after_two':
                from manga_tool.pipeline import load_manifest, update_panel
                saved=load_manifest(output)
                for panel in saved['panels']:
                    if panel.get('file') and (output/panel['file']).exists():
                        preserved[panel['id']]=sha256(output/panel['file'])
                first=next(panel for panel in saved['panels'] if panel.get('file'))
                bounds=list(first['bounds']); bounds[1]+=1
                edited=update_panel(output,first['id'],bounds=bounds)
                corrected=next(panel for panel in edited['panels'] if panel['id']==first['id'])
                preserved[first['id']]=sha256(output/corrected['file'])
                manual_edit={'id':first['id'],'bounds':bounds,'sha256':preserved[first['id']],
                             'kind':'simulated manual coordinate correction; no visual approval'}
    result=run_process(fixture,output,logs,'resume_final' if include_interrupts else 'complete')
    results.append(result)
    if result['returncode']!=0:
        raise AssertionError(f'Stress processing failed. See {logs}.')
    audit=audit_output(fixture,output,directory/'integrity.json')
    if audit['errors']:
        raise AssertionError(audit['errors'])
    if any(audit['checksums'].get(key)!=value for key,value in preserved.items()):
        raise AssertionError('Committed file or manual correction changed after forced interruption')
    if manual_edit:
        from manga_tool.pipeline import load_manifest
        corrected=next(p for p in load_manifest(output)['panels'] if p['id']==manual_edit['id'])
        if corrected['bounds']!=manual_edit['bounds']:
            raise AssertionError('Manual coordinates were reset on resume')
    # A clean rerun of the same output is idempotent; no reset or duplicate IDs.
    before=audit['checksums']
    again=run_process(fixture,output,logs,'idempotent_resume')
    if again['returncode']!=0:
        raise AssertionError('Idempotent resume failed')
    after=audit_output(fixture,output,directory/'integrity_after_resume.json')
    if before!=after['checksums'] or after['errors']:
        raise AssertionError('Checksums changed on idempotent resume')
    corrupt_checks=None
    if corrupt:
        from manga_tool.pipeline import load_manifest
        manifest=load_manifest(output)
        invalid=manifest.get('invalid_sources',[])
        blocked=[j for j in manifest.get('joins',[]) if j.get('blocked')]
        if not any('0090' in str(item) for item in invalid) or not blocked:
            raise AssertionError('Corrupt strip90 was not reported with a blocked join')
        crossed=[p['id'] for p in manifest['panels'] if p.get('file') and any(p['bounds'][1]<j['y']<p['bounds'][3] for j in blocked)]
        if crossed:
            raise AssertionError(f'Export silently crossed missing/corrupt strip: {crossed}')
        corrupt_checks={'corrupt_strip':90,'invalid_sources':invalid,'blocked_join_count':len(blocked),
                        'exports_crossing_blocked_joins':crossed,'production_status':manifest.get('production_status'),
                        'job_status':read_status(output)}
        if manifest.get('production_status')!='REVIEW_REQUIRED':
            raise AssertionError('Corrupt input must block production readiness')
    result={'synthetic':True,'fixture':str(fixture.resolve()),'runs':results,'idempotent_resume':again,
            'audit_passed':True,'source_count':audit['source_count'],'panel_count':audit['panel_count'],
            'pixels_compared':audit['pixels_compared'],'corrupt_at':90 if corrupt else None,
            'manual_edit_preserved':manual_edit,'committed_checksum_count_preserved':len(preserved),'corrupt_checks':corrupt_checks,
            'not_claimed':['real semantic accuracy','universal panel completeness','native Windows/macOS performance']}
    write_json(directory/'suite.json',result)
    return result



def summarize_completed_suite(directory):
    """Derive aggregate measurements without overwriting measured subprocess logs."""
    directory=Path(directory)
    path=directory/'suite.json'
    suite=json.loads(path.read_text())
    runs=suite['runs']
    elapsed=sum(float(run['elapsed_seconds']) for run in runs)
    cpu=sum(float(run.get('sampled_cpu_seconds') or 0) for run in runs)
    peak=max((run.get('peak_sampled_rss_bytes') or 0 for run in runs),default=0)
    fixture=Path(suite['fixture'])
    metadata=json.loads((fixture/'fixture.json').read_text())
    audit=audit_output(fixture,directory/'output',directory/'integrity_final.json')
    if audit['errors']:
        raise AssertionError(audit['errors'])
    from manga_tool.detection import ALGORITHM_VERSION
    algorithm_versions=sorted({run.get('status',{}).get('algorithm_version') for run in runs if run.get('status',{}).get('algorithm_version')})
    result={'synthetic':True,'algorithm_versions_in_measured_attempts':algorithm_versions,'current_detector_version':ALGORITHM_VERSION,'input_strips':metadata['strips'],'dimensions':[WIDTH,STRIP_HEIGHT],
            'input_pixels':metadata['total_pixels'],'valid_sources':audit['source_count'],
            'exported_review_regions':audit['panel_count'],'unexported_regions':audit['unexported_regions'],
            'original_files_audited':len(audit['original_byte_checks']),
            'pixel_comparisons':audit['pixels_compared'],'pixel_mismatch_count':0,
            'processing_seconds_including_restarts':round(elapsed,3),
            'sampled_cpu_seconds_including_restarts':round(cpu,3) if all(run.get('sampled_cpu_seconds') is not None for run in runs) else None,
            'peak_sampled_rss_bytes':peak or None,
            'input_strips_per_processing_second':round(metadata['strips']/elapsed,4),
            'forced_kills':[{'name':run['name'],'returncode':run['returncode'],'stage':run['status'].get('stage'),'current':run['status'].get('current')} for run in runs if run['returncode']==-signal.SIGKILL],
            'manual_edit_preserved':suite.get('manual_edit_preserved'),
            'orphan_file_count':len(audit['orphan_files']),'temporary_file_count':len(audit['temporary_files']),
            'status':read_status(directory/'output'),'verified_region_count':audit['statuses'].get('VERIFIED',0),
            'limits':'Shared-host sampled measurements; total excludes generation, manual edit, integrity audit, and final idempotent re-entry. It includes crashed attempts and repeated work. No real artwork accuracy claim.'}
    if metadata.get('corrupt_at'):
        from manga_tool.pipeline import load_manifest
        m=load_manifest(directory/'output')
        invalid=m.get('invalid_sources',[])
        blocked=[join for join in m.get('joins',[]) if join.get('blocked')]
        crossed=[p['id'] for p in m['panels'] if p.get('file') and any(p['bounds'][1]<j['y']<p['bounds'][3] for j in blocked)]
        assert any('0090' in str(item) for item in invalid), 'Corrupt JPEG90 not reported'
        assert blocked and not crossed, 'Missing-source join not safely blocked'
        result['corrupt_file_check']={'strip':90,'invalid_source_count':len(invalid),'blocked_join_count':len(blocked),'export_crossing_blocked_join_count':len(crossed),'production_status':m.get('production_status')}
    write_json(directory/'summary.json',result)
    return result



def compact_progress_status(status):
    # Full input inventories/checksums are recorded once in freeze.json and final
    # status. Repeating 500 filenames every 0.2s can waste hundreds of MB.
    keys=('stage','status','current','total','elapsed_seconds','algorithm_version',
          'attempt','source_id','region_id','item_id','skipped','error','error_type',
          'control','production_status','cache_hit','source_checksum','resources_now')
    result={key:status[key] for key in keys if key in status}
    if status.get('warnings'):
        result['warning_count']=len(status['warnings'])
        result['warnings_sample']=status['warnings'][:5]
    if 'not_ready' in status:
        result['not_ready']=status['not_ready']
    return result


def python_source_hashes():
    """Frozen application + harness; separate tests and reports may be added."""
    files=list((ROOT/'manga_tool').rglob('*.py'))+[ROOT/'start.py',Path(__file__)]
    return {path.relative_to(ROOT).as_posix():sha256(path)
            for path in sorted(set(files)) if path.is_file()}


def detector_version_from_file():
    tree=ast.parse((ROOT/'manga_tool/detection.py').read_text())
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='ALGORITHM_VERSION' for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Detector ALGORITHM_VERSION is missing')


def validate_full_size_fixture(fixture,strips):
    from PIL import Image
    fixture=Path(fixture).resolve()
    metadata=json.loads((fixture/'fixture.json').read_text())
    assert metadata['synthetic'] is True
    assert metadata['strips']==strips and len(metadata['originals'])==strips
    assert metadata['width']==WIDTH and metadata['height']==STRIP_HEIGHT
    expected_names=set()
    for original in metadata['originals']:
        path=fixture/'input'/original['name']
        assert not path.is_symlink(), 'Do not use symlinked source images'
        assert sha256(path)==original['sha256'], f'Fixture source changed: {path.name}'
        with Image.open(path) as image:
            assert image.size==(WIDTH,STRIP_HEIGHT), f'Tiny/incorrect fixture source: {path.name}'
        expected_names.add(path.name)
    assert {p.name for p in (fixture/'input').iterdir() if p.is_file()}==expected_names
    return {'fixture':str(fixture),'strips':strips,'dimensions':[WIDTH,STRIP_HEIGHT],
            'total_pixels':strips*WIDTH*STRIP_HEIGHT,'fixture_metadata_sha256':sha256(fixture/'fixture.json'),
            'input_hashes':{item['name']:item['sha256'] for item in metadata['originals']}}


def require_fresh_output(directory):
    output=Path(directory)/'output'
    if output.exists() and any(output.iterdir()):
        raise ValueError(f'Final validation requires an empty new output, with no old profiles/state: {output}')
    return output


def protected_state(output):
    """Compare durable meaning and every live original/artifact/projection."""
    from manga_tool.storage import Store
    store=Store(output)
    manifest=store.load_manifest()
    files={}
    for source in manifest.get('sources',[])+manifest.get('invalid_sources',[]):
        if source.get('path'):
            path=Path(output)/source['path']
            files[source['path']]=sha256(path) if path.is_file() else None
    for panel in manifest.get('panels',[]):
        for key in ('file','output_file','reconstructed_file'):
            if panel.get(key):
                path=Path(output)/panel[key]
                files[panel[key]]=sha256(path) if path.is_file() else None
    for name in ('reports/manifest.json','reports/panels.csv'):
        path=Path(output)/name
        files[name]=sha256(path) if path.is_file() else None
    encode=lambda item:hashlib.sha256(json.dumps(item,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'manifest_sha256':encode(manifest),'job_sha256':encode(store.job()),'file_checksums':files}


def _atomic_test_bytes(path,payload):
    path=Path(path)
    temp=path.with_name(path.name+'.harness-restore.tmp')
    with temp.open('wb') as handle:
        handle.write(payload);handle.flush();os.fsync(handle.fileno())
    os.replace(temp,path)


@contextmanager
def temporary_test_damage(path,directory,missing=False):
    """Only synthetic fixtures/test output; always restore exact bytes afterward."""
    path=Path(path)
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    stat=path.stat()
    backup=directory/(path.name+'.backup')
    assert not backup.exists()
    shutil.copy2(path,backup)
    try:
        if missing:
            path.unlink()
        else:
            _atomic_test_bytes(path,b'INTENTIONAL SYNTHETIC HARNESS DAMAGE\n')
        yield
    finally:
        os.replace(backup,path)
        os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))


def completed_job_fault_checks(fixture,output,directory,environment,alternate_fixture=None):
    """Recover named projections; reject canonical/source/request identity damage."""
    from manga_tool.pipeline import load_manifest
    fixture,output,directory=map(Path,(fixture,output,directory))
    logs=directory/'measurements'
    results=[]
    manifest=load_manifest(output)
    first=next(p for p in manifest['panels'] if p.get('file') and p.get('output_file'))
    canonical=output/first['file'];projection=output/first['output_file']
    expected_hash=sha256(canonical)
    _atomic_test_bytes(projection,b'INTENTIONAL DAMAGED NAMED PROJECTION\n')
    result=run_process(fixture,output,logs,'repair_damaged_projection',environment)
    assert result['returncode']==0, result
    assert sha256(projection)==expected_hash and sha256(canonical)==expected_hash
    results.append({'case':'damaged_named_projection','passed':True,'policy':'repair only from valid immutable revision','run':result})

    def reject(name,active_fixture=fixture,extra=None):
        before=protected_state(output)
        result=run_process(active_fixture,output,logs,name,{**environment,**(extra or {})})
        assert result['returncode'] not in (0,3,4), f'Unsafe request/damage was silently accepted: {name}'
        after=protected_state(output)
        assert before==after, f'Rejected {name} changed durable job, manifest or valid outputs'
        stderr=(logs/f'{name}.stderr.log').read_text()[-5000:]
        assert stderr.strip(), 'Failure must have a visible reason'
        results.append({'case':name,'passed':True,'state_unchanged':True,'reason':stderr,'run':result})

    if alternate_fixture:
        reject('reject_other_input',Path(alternate_fixture))
    reject('reject_other_chapter',extra={'MANGA_STRESS_CHAPTER':'OTHER_CHAPTER'})
    reject('reject_changed_options',extra={'MANGA_STRESS_MIN_GUTTER':'24'})
    with temporary_test_damage(canonical,directory/'damage_backups'):
        reject('reject_damaged_canonical')
        assert sha256(projection)==expected_hash, 'Damaged canonical must not overwrite the valid projection'
    copied_source=output/manifest['sources'][0]['path']
    with temporary_test_damage(copied_source,directory/'damage_backups'):
        reject('reject_damaged_copied_original')
    source=fixture/'input'/manifest['sources'][0]['original_name']
    with temporary_test_damage(source,directory/'damage_backups'):
        reject('reject_changed_input_bytes')
    with temporary_test_damage(source,directory/'damage_backups',missing=True):
        reject('reject_missing_input_source')
    after=run_process(fixture,output,logs,'resume_after_fault_restoration',environment)
    assert after['returncode']==0,after
    results.append({'case':'harness_restored_synthetic_damage','passed':True,
                    'note':'The harness restored intentionally changed fixture/canonical bytes. The application did not silently rewrite immutable history.','run':after})
    write_json(directory/'completed_fault_checks.json',results)
    return results


def prepare_source_fault_fixture(source_fixture,directory,kind='corrupt',at=90):
    """Separate small on-disk input copy, never modify the clean shared fixture."""
    source_fixture,directory=map(Path,(source_fixture,directory))
    if directory.exists():
        raise ValueError('Source fault fixture must be new')
    shutil.copytree(source_fixture,directory)
    metadata=json.loads((directory/'fixture.json').read_text())
    item=next(item for item in metadata['originals'] if item['order']==at)
    path=directory/'input'/item['name']
    if kind=='corrupt':
        assert path.suffix=='.jpg'
        path.write_bytes(path.read_bytes()[:113])
        item.update(sha256=sha256(path),bytes=path.stat().st_size,corrupt=True)
        metadata['corrupt_at']=at
    elif kind=='missing':
        path.unlink()
        metadata['originals']=[entry for entry in metadata['originals'] if entry['order']!=at]
        metadata['missing_at']=at
    else:
        raise ValueError('Choose corrupt or missing')
    write_json(directory/'fixture.json',metadata)
    return directory


def check_initial_source_fault(fixture,output,kind,at=90):
    from manga_tool.pipeline import load_manifest
    manifest=load_manifest(output)
    metadata=json.loads((Path(fixture)/'fixture.json').read_text())
    expected_valid=len(metadata['originals'])-(1 if kind=='corrupt' else 0)
    assert len(manifest['sources'])==expected_valid, 'A source fault must not silently drop surviving strips'
    blocked=[join for join in manifest.get('joins',[]) if join.get('blocked')]
    diagnostics=str({key:manifest.get(key) for key in ('invalid_sources','excluded','warnings','preflight_warnings')})
    assert str(at) in diagnostics or f'{at:04d}' in diagnostics, 'Missing/corrupt source must be explicitly reported'
    assert blocked,'Missing/corrupt source must block its unresolved join'
    crossed=[panel['id'] for panel in manifest['panels'] if panel.get('file') and any(panel['bounds'][1]<join['y']<panel['bounds'][3] for join in blocked)]
    assert not crossed, f'Exports crossed an unresolved source gap: {crossed}'
    assert manifest.get('production_status')=='REVIEW_REQUIRED'
    report=audit_output(fixture,output)
    assert not report['errors'],report['errors']
    return {'kind':kind,'source_position':at,'reported':True,'blocked_join_count':len(blocked),
            'exports_crossing_blocked_join_count':0,'valid_sources':len(manifest['sources']),
            'input_files_retained':len(report['original_byte_checks']),'production_status':manifest['production_status'],
            'unexported_review_regions':report['unexported_regions'],'pixel_comparisons':report['pixels_compared']}


def run_final_suite(directory,fixture,strips=200,expected_version='v3.1-regions-2026-11-14.1',interrupts=True,source_faults=False,alternate_fixture=None):
    """Final frozen-build run: fresh state/cache; optional genuine fault sequence."""
    directory,fixture=Path(directory).resolve(),Path(fixture).resolve()
    output=require_fresh_output(directory)
    assert detector_version_from_file()==expected_version,'Wait for the final detector freeze'
    proof=validate_full_size_fixture(fixture,strips)
    frozen=python_source_hashes()
    directory.mkdir(parents=True,exist_ok=True)
    environment={'MANGA_EXPECTED_ALGORITHM':expected_version,'MANGA_EXPECTED_PY_HASHES':json.dumps(frozen,sort_keys=True)}
    write_json(directory/'freeze.json',{'expected_detector':expected_version,'python_hashes':frozen,'input':proof,'old_output_or_cache_reused':False})
    runs=[];preserved={};manual=None
    logs=directory/'measurements'
    if interrupts:
        assert os.name!='nt' and hasattr(signal,'SIGKILL'),'Native Windows crash testing remains pending'
        steps=[('sigkill_index_60',{'MANGA_TEST_EXIT_STAGE':'INDEX','MANGA_TEST_EXIT_AT':'60','MANGA_TEST_SIGKILL':'1'},-signal.SIGKILL,'INDEX',60),
               ('pause_index_65',{'MANGA_STRESS_CONTROL':'pause','MANGA_STRESS_CONTROL_AT':'65'},3,'INDEX',65),
               ('cancel_index_70',{'MANGA_STRESS_CONTROL':'cancel','MANGA_STRESS_CONTROL_AT':'70'},4,'INDEX',70),
               ('sigkill_index_75',{'MANGA_TEST_EXIT_STAGE':'INDEX','MANGA_TEST_EXIT_AT':'75','MANGA_TEST_SIGKILL':'1'},-signal.SIGKILL,'INDEX',75),
               ('sigkill_after_export_2',{'MANGA_TEST_EXIT_STAGE':'EXPORT','MANGA_TEST_EXIT_AT':'2','MANGA_TEST_SIGKILL':'1'},-signal.SIGKILL,'EXPORT',2),
               ('sigkill_before_export_3_commit',{'MANGA_TEST_EXIT_STAGE':'EXPORT','MANGA_TEST_EXIT_AT':'3','MANGA_TEST_EXIT_POINT':'before_commit','MANGA_TEST_SIGKILL':'1'},-signal.SIGKILL,'EXPORT',3)]
        for name,extra,code,stage,current in steps:
            run=run_process(fixture,output,logs,name,{**environment,**extra});runs.append(run)
            assert run['returncode']==code,run
            assert run['status']['stage']==stage and run['status']['current']==current,run['status']
            assert run['status']['algorithm_version']==expected_version
            if code in (3,4):
                assert run['status']['status']==('PAUSED' if code==3 else 'CANCELLED')
            if name=='sigkill_after_export_2':
                from manga_tool.pipeline import load_manifest,update_panel
                saved=load_manifest(output)
                completed=[panel for panel in saved['panels'] if panel.get('file')]
                assert len(completed)>=2,'EXPORT hook must run after persisted commits'
                preserved={p['id']:sha256(output/p['file']) for p in completed}
                first=completed[0];bounds=list(first['bounds']);bounds[1]+=1
                edited=update_panel(output,first['id'],bounds=bounds)
                corrected=next(panel for panel in edited['panels'] if panel['id']==first['id'])
                preserved[first['id']]=sha256(output/corrected['file'])
                manual={'id':first['id'],'bounds':bounds,'sha256':preserved[first['id']],
                        'kind':'simulated manual coordinate correction; no visual verification claim'}
    run=run_process(fixture,output,logs,'complete_resume' if interrupts else 'complete_fresh',environment);runs.append(run)
    assert run['returncode']==0,run
    assert run['status']['stage']=='REVIEW_READY' and run['status']['status']=='REVIEW_REQUIRED',run['status']
    assert run['status']['algorithm_version']==expected_version
    report=audit_output(fixture,output,directory/'integrity_before_faults.json')
    assert not report['errors'],report['errors']
    assert report['source_count']==strips and len(report['original_byte_checks'])==strips
    assert report['statuses'].get('VERIFIED',0)==0,'The harness never verifies artwork'
    assert all(report['checksums'].get(key)==digest for key,digest in preserved.items())
    if manual:
        from manga_tool.pipeline import load_manifest
        corrected=next(p for p in load_manifest(output)['panels'] if p['id']==manual['id'])
        assert corrected['bounds']==manual['bounds']
    faults=completed_job_fault_checks(fixture,output,directory,environment,alternate_fixture=alternate_fixture) if interrupts else []
    again=run_process(fixture,output,logs,'idempotent_reentry',environment)
    assert again['returncode']==0,again
    final=audit_output(fixture,output,directory/'integrity_final.json')
    assert not final['errors'] and final['checksums']==report['checksums']
    assert validate_full_size_fixture(fixture,strips)==proof,'Fixture source bytes changed during the run'
    initial_faults=[]
    if source_faults:
        for kind in ('corrupt','missing'):
            case=directory/(kind+'_90')
            fault_fixture=prepare_source_fault_fixture(fixture,case/'fixture',kind=kind)
            result=run_process(fault_fixture,case/'output',case/'measurements','complete',environment)
            assert result['returncode']==0,result
            assert result['status'].get('algorithm_version')==expected_version
            check=check_initial_source_fault(fault_fixture,case/'output',kind)
            audit_output(fault_fixture,case/'output',case/'integrity_final.json')
            initial_faults.append({'case':kind+'_90','check':check,'run':result})
    assert python_source_hashes()==frozen,'Source changed after freeze'
    from manga_tool.pipeline import load_manifest
    elapsed=sum(run['elapsed_seconds'] for run in runs)
    cpu=sum(run.get('sampled_cpu_seconds') or 0 for run in runs)
    result={'synthetic':True,'expected_algorithm_version':expected_version,'all_attempts_final_version':all(run['status'].get('algorithm_version')==expected_version for run in runs),
            'old_output_or_profile_cache_reused':False,'fixture':str(fixture),'input_proof':proof,
            'python_source_hashes':frozen,'processing_runs':runs,'fault_checks':faults,'initial_source_faults':initial_faults,'idempotent_reentry':again,
            'manual_edit_preserved':manual,'committed_checksums_preserved':preserved,
            'cold_first_attempt_seconds':runs[0]['elapsed_seconds'],
            'retry_processing_seconds':round(sum(run['elapsed_seconds'] for run in runs[1:]),3),
            'processing_seconds_including_interruptions':round(elapsed,3),
            'sampled_cpu_seconds_including_interruptions':round(cpu,3),
            'peak_sampled_rss_bytes':max(run.get('peak_sampled_rss_bytes') or 0 for run in runs),
            'input_strips_per_processing_second':round(strips/elapsed,4),
            'strips':strips,'dimensions':[WIDTH,STRIP_HEIGHT],'input_pixels':strips*WIDTH*STRIP_HEIGHT,
            'exported_review_regions':final['panel_count'],'original_files_compared':len(final['original_byte_checks']),
            'pixels_compared':final['pixels_compared'],'pixel_mismatches':0,'orphan_files':final['orphan_files'],'temporary_files':final['temporary_files'],
            'output_checksums':final['checksums'],'final_manifest_projection_sha256':sha256(output/'reports/manifest.json'),
            'manifest_algorithm_version':load_manifest(output).get('algorithm_version'),
            'verified_region_count':final['statuses'].get('VERIFIED',0),'status':read_status(output),
            'passed':True,'limits':'Shared Linux host, sampled RSS/CPU; processing totals include crash/pause/cancel attempts but exclude harness edits, audits, post-run damage checks and source-fault jobs. Synthetic stress is not a real panel-boundary accuracy benchmark. Native macOS/Windows untested.'}
    write_json(directory/'final_suite.json',result)
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    generate=sub.add_parser('generate'); generate.add_argument('directory'); generate.add_argument('--strips',type=int,default=200); generate.add_argument('--corrupt-at',type=int)
    suite=sub.add_parser('suite'); suite.add_argument('directory'); suite.add_argument('--strips',type=int,default=200); suite.add_argument('--no-interrupts',action='store_true'); suite.add_argument('--corrupt',action='store_true')
    final=sub.add_parser('final-suite'); final.add_argument('directory'); final.add_argument('--fixture',required=True); final.add_argument('--strips',type=int,choices=(200,500),required=True); final.add_argument('--expected-version',default='v3.1-regions-2026-11-14.1'); final.add_argument('--no-interrupts',action='store_true'); final.add_argument('--source-faults',action='store_true'); final.add_argument('--alternate-fixture')
    summary=sub.add_parser('summary'); summary.add_argument('directory')
    audit=sub.add_parser('audit'); audit.add_argument('fixture'); audit.add_argument('output'); audit.add_argument('--report')
    args=parser.parse_args()
    if args.action=='generate':
        result=generate_fixture(args.directory,args.strips,args.corrupt_at)
        print(json.dumps({k:v for k,v in result.items() if k not in ('originals','expected_geometry')},indent=2))
    elif args.action=='final-suite':
        print(json.dumps(run_final_suite(args.directory,args.fixture,args.strips,args.expected_version,not args.no_interrupts,args.source_faults,args.alternate_fixture),indent=2,default=str))
    elif args.action=='summary':
        print(json.dumps(summarize_completed_suite(args.directory),indent=2,default=str))
    elif args.action=='suite':
        print(json.dumps(run_suite(args.directory,args.strips,not args.no_interrupts,args.corrupt),indent=2,default=str))
    else:
        result=audit_output(args.fixture,args.output,args.report)
        print(json.dumps({k:v for k,v in result.items() if k not in ('original_byte_checks','pixel_checks','checksums')},indent=2))
        if result['errors']:
            raise SystemExit(1)


if __name__=='__main__':
    main()
