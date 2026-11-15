"""Crash/resume, provenance and ordering safeguards for the V3 importer."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest
from PIL import Image

from manga_tool import importer


def make_strips(folder, count, *, size=(180, 160)):
    folder.mkdir()
    for index in range(1, count + 1):
        Image.new('RGB', size, (index % 220, index * 3 % 220, index * 7 % 220)).save(folder / f'strip{index:03d}.png')


def test_forced_exit_after_75_of_200_resumes_without_revalidating_copied_sources(tmp_path, monkeypatch):
    source = tmp_path / 'input'
    make_strips(source, 200)
    output = tmp_path / 'output'
    code = '''import os,sys
from manga_tool.importer import import_chapter
def checkpoint(stage,current,total,**details):
    if current == 75:
        os._exit(23)
import_chapter(sys.argv[1],sys.argv[2],checkpoint=checkpoint)
'''
    process = subprocess.run([sys.executable, '-c', code, str(source), str(output)],
                             cwd=Path(importer.__file__).resolve().parents[1], check=False)
    assert process.returncode == 23
    assert len(list((output / 'originals').glob('*.png'))) == 75
    originals = {p.name: p.read_bytes() for p in (output / 'originals').glob('*.png')}
    original_mtimes = {p.name: p.stat().st_mtime_ns for p in (output / 'originals').glob('*.png')}
    validated = []
    actual_validate = importer._validate_image
    def validate(candidate):
        validated.append(candidate.name)
        actual_validate(candidate)
    monkeypatch.setattr(importer, '_validate_image', validate)
    progress = []
    result = importer.import_chapter(source, output, checkpoint=lambda *args, **kw: progress.append((args, kw)))
    assert len(validated) == 125
    assert validated[0] == 'strip076.png'
    assert result['cache_hits'] == 75
    assert len(result['sources']) == 200
    assert len({s['id'] for s in result['sources']}) == 200
    assert all((output / 'originals' / name).read_bytes() == raw for name, raw in originals.items())
    assert all((output / 'originals' / name).stat().st_mtime_ns == stamp for name, stamp in original_mtimes.items())
    assert progress[74][1]['status'] == 'CACHED'
    assert progress[75][1]['status'] == 'IMPORTED'
    assert all(item[0][0] == 'IMPORT' and item[0][2] == 200 for item in progress)


def test_damaged_strip_90_preserved_reported_and_join_blocked(tmp_path):
    source = tmp_path / 'input'
    make_strips(source, 100)
    damaged = source / 'strip090.png'
    damaged.write_bytes(b'corrupt original bytes retained exactly')
    output = tmp_path / 'output'
    result = importer.import_chapter(source, output)
    assert len(result['sources']) == 99
    assert len(result['invalid_sources']) == 1
    bad = result['invalid_sources'][0]
    assert bad['id'] == 'S0090'
    assert (output / bad['path']).read_bytes() == damaged.read_bytes()
    after = next(s for s in result['sources'] if s['id'] == 'S0091')
    assert after['join_before_status'] == 'BLOCKED'
    assert any('S0090' in issue.get('source_ids', []) for issue in after['discontinuity_before'])
    assert result['height'] == 99 * 160  # no fabricated replacement raster
    assert result['errors'] and result['status'] == 'REVIEW_REQUIRED'
    assert importer.preflight(output)['invalid_sources'][0]['id'] == 'S0090'


def test_missing_numbers_duplicates_mixed_width_and_stable_manual_reorder(tmp_path):
    source = tmp_path / 'input'
    make_strips(source, 4)
    (source / 'strip002.png').unlink()
    (source / 'other').mkdir()
    Image.new('RGB', (190, 160), '#123456').save(source / 'other' / 'strip003.png')
    output = tmp_path / 'output'
    result = importer.import_chapter(source, output)
    codes = {warning['code'] for warning in result['preflight_warnings']}
    assert {'MISSING_NUMERIC_STRIPS', 'DUPLICATE_NUMERIC_STRIP', 'MIXED_WIDTH_ALIGNMENT_UNKNOWN'} <= codes
    ids = [s['id'] for s in result['sources']]
    paths = {s['id']: s['path'] for s in result['sources']}
    reordered = importer.reorder_sources(output, list(reversed(ids)))
    assert [s['id'] for s in reordered] == list(reversed(ids))
    assert [s['order'] for s in reordered] == list(range(1, len(ids) + 1))
    assert all(s['path'] == paths[s['id']] for s in reordered)
    assert all(a['y1'] == b['y0'] for a, b in zip(reordered, reordered[1:]))
    resumed = importer.import_chapter(source, output)
    assert [s['id'] for s in resumed['sources']] == list(reversed(ids))
    assert importer.preflight(output)['order_reviewed'] is True
    with pytest.raises(ValueError, match='every valid source ID'):
        importer.reorder_sources(output, [ids[0]] * len(ids))
    with pytest.raises(ValueError):
        importer.reorder_sources(output, [{}])


def test_html_filename_conflict_and_missing_embedded_image_block_continuity(tmp_path):
    source = tmp_path / 'input'
    make_strips(source, 3)
    base = 'https://example.test/chapter/'
    resources = [{'WebResourceURL': base + p.name, 'WebResourceMIMEType': 'image/png',
                  'WebResourceData': p.read_bytes()} for p in source.glob('*.png')]
    markup = b'<img class="_imageList" src="strip003.png"><img class="_imageList" src="strip004.png"><img class="_imageList" src="strip001.png"><img class="_imageList" src="strip002.png">'
    archive = tmp_path / 'chapter.webarchive'
    archive.write_bytes(plistlib.dumps({'WebMainResource': {'WebResourceURL': base, 'WebResourceData': markup},
                                       'WebSubresources': resources}))
    result = importer.import_chapter(archive, tmp_path / 'out')
    assert 'HTML_FILENAME_ORDER_CONFLICT' in {w['code'] for w in result['preflight_warnings']}
    assert result['invalid_sources'][0]['missing'] is True
    after = next(s for s in result['sources'] if s['original_name'] == 'strip001.png')
    assert after['join_before_status'] == 'BLOCKED'
    assert any(w['code'] == 'INVALID_OR_MISSING_SOURCE_BETWEEN' for w in after['discontinuity_before'])


def test_original_commit_failure_never_leaves_partial_file_and_resume_succeeds(tmp_path, monkeypatch):
    source = tmp_path / 'input'
    make_strips(source, 2)
    output = tmp_path / 'output'
    real_replace = importer.os.replace
    def fail_original_commit(src, dst):
        if Path(dst).parent.name == 'originals':
            raise OSError('simulated sudden commit failure')
        return real_replace(src, dst)
    monkeypatch.setattr(importer.os, 'replace', fail_original_commit)
    with pytest.raises(OSError, match='sudden commit failure'):
        importer.import_chapter(source, output)
    assert not list((output / 'originals').iterdir())
    monkeypatch.setattr(importer.os, 'replace', real_replace)
    assert len(importer.import_chapter(source, output)['sources']) == 2


def test_low_memory_is_reported_and_transient_validation_can_retry(tmp_path, monkeypatch):
    source = tmp_path / 'input'
    make_strips(source, 1)
    output = tmp_path / 'output'
    monkeypatch.setattr(importer, 'available_memory_bytes', lambda: 1024)
    with pytest.raises(ValueError, match='No chapter images'):
        importer.import_chapter(source, output)
    report = json.loads((output / 'reports/import.json').read_text())
    assert 'memory' in report['errors'][0]['error'].lower()
    monkeypatch.setattr(importer, 'available_memory_bytes', lambda: 2_000_000_000)
    assert len(importer.import_chapter(source, output)['sources']) == 1


def test_added_source_does_not_change_existing_ids(tmp_path):
    source = tmp_path / 'input'
    make_strips(source, 2)
    output = tmp_path / 'output'
    before = importer.import_chapter(source, output)
    ids = {s['original_name']: s['id'] for s in before['sources']}
    Image.new('RGB', (180, 160), '#456789').save(source / 'strip000.png')
    after = importer.import_chapter(source, output)
    assert all(s['id'] == ids[s['original_name']] for s in after['sources'] if s['original_name'] in ids)


def test_stale_original_staging_file_is_removed_on_resume(tmp_path):
    source = tmp_path / 'input'
    make_strips(source, 1)
    output = tmp_path / 'output'
    originals = output / 'originals'
    originals.mkdir(parents=True)
    stale = originals / '.S0001_strip001.pngdeadwriter.tmp'
    stale.write_bytes(b'partial data left by interrupted atomic write')
    result = importer.import_chapter(source, output)
    assert not stale.exists()
    assert (output / result['sources'][0]['path']).read_bytes() == (source / 'strip001.png').read_bytes()


def test_preflight_reads_canonical_state_when_derived_report_is_stale(tmp_path):
    source = tmp_path / 'input'
    make_strips(source, 2)
    output = tmp_path / 'output'
    result = importer.import_chapter(source, output)
    old_derived = (output / 'reports/preflight.json').read_bytes()
    reversed_ids = [s['id'] for s in reversed(result['sources'])]
    importer.reorder_sources(output, reversed_ids)
    (output / 'reports/preflight.json').write_bytes(old_derived)
    assert importer.preflight(output)['source_order'] == reversed_ids


def test_webtoon_upload_timestamps_do_not_hide_missing_chapter_numbers(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    for timestamp, number in [('17390035339991854', 1), ('17390035341343706', 3)]:
        Image.new('RGB', (180, 160), '#345678').save(source / f'{timestamp}_Chapter_05_{number:04d}.png')
    result = importer.import_chapter(source, tmp_path / 'out')
    missing = next(w for w in result['preflight_warnings'] if w['code'] == 'MISSING_NUMERIC_STRIPS')
    assert missing['missing_numbers'] == [2]
    assert result['sources'][1]['join_before_status'] == 'BLOCKED'


def test_excluded_icons_do_not_create_mixed_width_chapter_warning(tmp_path):
    source = tmp_path / 'input'
    make_strips(source, 2)
    Image.new('RGB', (20, 20), 'white').save(source / 'icon.png')
    result = importer.import_chapter(source, tmp_path / 'out')
    assert len(result['sources']) == 2
    assert 'MIXED_WIDTH_ALIGNMENT_UNKNOWN' not in {w['code'] for w in result['preflight_warnings']}
