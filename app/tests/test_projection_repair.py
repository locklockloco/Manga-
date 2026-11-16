"""Projected exports are rebuildable; immutable revisions and originals are not."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image

from manga_tool import pipeline


def fixture(root):
    (root / 'review/revisions').mkdir(parents=True)
    (root / 'originals').mkdir()
    (root / 'originals/source.png').write_bytes(b'unchanged original fixture bytes')
    (root / 'review/history.json').write_text('{"decisions":["manual correction preserved"]}')
    panels = []
    for index, color in enumerate(('#123456', '#456789'), 1):
        relative = f'review/revisions/CH01_P{index:03d}_r0001.png'
        Image.new('RGB', (90 + index, 80 + index), color).save(root / relative)
        panels.append({'id': f'CH01_P{index:03d}', 'status': 'NEEDS_REVIEW',
                       'classification': 'illustration', 'file': relative,
                       'sha256': pipeline.digest(root / relative),
                       'output_file': f'proposals/CH01_P{index:03d}.png'})
    manifest = {'panels': panels, 'history': [{'operation': 'existing_review'}]}
    pipeline.sync_outputs(root, manifest)
    return manifest


def protected(root):
    files = [*root.joinpath('originals').glob('*'), *root.joinpath('review/revisions').glob('*'),
             root / 'review/history.json']
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def assert_intact(root, manifest, snapshot):
    for panel in manifest['panels']:
        assert pipeline.digest(root / panel['output_file']) == panel['sha256']
        with Image.open(root / panel['output_file']) as image:
            image.verify()
    assert protected(root) == snapshot
    assert not list(root.rglob('*.tmp'))
    assert json.loads((root / 'review/projections.json').read_text()) == {
        panel['output_file']: panel['sha256'] for panel in manifest['panels']}


@pytest.mark.parametrize('damage', ['missing', 'truncated', 'same_size_wrong_bytes', 'replaced_png'])
def test_existing_marker_and_timestamps_never_skip_actual_projection_hash(tmp_path, damage):
    manifest = fixture(tmp_path)
    snapshot = protected(tmp_path)
    panel = manifest['panels'][0]
    path = tmp_path / panel['output_file']
    old_stat = path.stat()
    raw = path.read_bytes()
    marker = (tmp_path / 'review/projections.json').read_bytes()
    if damage == 'missing':
        path.unlink()
    elif damage == 'truncated':
        path.write_bytes(raw[:13])
    elif damage == 'same_size_wrong_bytes':
        path.write_bytes(bytes([value ^ 0xA5 for value in raw]))
    else:
        Image.new('RGB', (91, 81), '#ab8877').save(path)
    if path.exists():
        os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    assert (tmp_path / 'review/projections.json').read_bytes() == marker
    repaired = pipeline.sync_outputs(tmp_path, manifest)
    assert repaired['repaired'] == 1
    assert_intact(tmp_path, manifest, snapshot)


def test_corrupt_revision_errors_before_replacing_existing_valid_projection(tmp_path):
    manifest = fixture(tmp_path)
    panel = manifest['panels'][0]
    good_projection = (tmp_path / panel['output_file']).read_bytes()
    old_marker = (tmp_path / 'review/projections.json').read_bytes()
    revision = tmp_path / panel['file']
    revision.write_bytes(b'corrupted immutable revision')
    with pytest.raises(ValueError, match='Immutable revision checksum mismatch'):
        pipeline.sync_outputs(tmp_path, manifest)
    assert (tmp_path / panel['output_file']).read_bytes() == good_projection
    assert revision.read_bytes() == b'corrupted immutable revision'
    assert (tmp_path / 'review/projections.json').read_bytes() == old_marker


@pytest.mark.parametrize('failure', ['copy', 'replace', 'marker'])
def test_failed_projection_commit_recovers_without_damaging_revisions(tmp_path, monkeypatch, failure):
    manifest = fixture(tmp_path)
    snapshot = protected(tmp_path)
    target = tmp_path / manifest['panels'][0]['output_file']
    target.write_bytes(b'damaged projection before retry')
    old_marker = (tmp_path / 'review/projections.json').read_bytes()
    real_copy = pipeline.shutil.copyfile
    real_replace = pipeline.os.replace
    if failure == 'copy':
        def fail_copy(src, dst):
            Path(dst).write_bytes(b'partial staging bytes')
            raise OSError('injected copy failure')
        monkeypatch.setattr(pipeline.shutil, 'copyfile', fail_copy)
    else:
        def fail_replace(src, dst):
            is_marker = Path(dst).name == 'projections.json'
            if (failure == 'marker' and is_marker) or (failure == 'replace' and Path(dst) == target):
                raise OSError('injected commit failure')
            return real_replace(src, dst)
        monkeypatch.setattr(pipeline.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='injected'):
        pipeline.sync_outputs(tmp_path, manifest)
    assert protected(tmp_path) == snapshot
    assert (tmp_path / 'review/projections.json').read_bytes() == old_marker
    assert not list(tmp_path.rglob('*.tmp'))
    monkeypatch.setattr(pipeline.shutil, 'copyfile', real_copy)
    monkeypatch.setattr(pipeline.os, 'replace', real_replace)
    pipeline.sync_outputs(tmp_path, manifest)
    assert_intact(tmp_path, manifest, snapshot)


def test_corrupt_copy_cannot_overwrite_an_existing_destination(tmp_path, monkeypatch):
    manifest = fixture(tmp_path)
    snapshot = protected(tmp_path)
    target = tmp_path / manifest['panels'][0]['output_file']
    target.write_bytes(b'destination retained until a validated replacement exists')
    before = target.read_bytes()
    def copy_wrong_bytes(src, dst):
        Path(dst).write_bytes(b'silently incomplete filesystem copy')
    monkeypatch.setattr(pipeline.shutil, 'copyfile', copy_wrong_bytes)
    with pytest.raises(ValueError, match='Projection staging checksum mismatch'):
        pipeline.sync_outputs(tmp_path, manifest)
    assert target.read_bytes() == before
    assert protected(tmp_path) == snapshot
    assert not list(tmp_path.rglob('*.tmp'))


@pytest.mark.parametrize('failure', ['during_copy', 'after_replace'])
def test_killed_writer_recovers_staging_and_metadata_on_next_sync(tmp_path, failure):
    manifest = fixture(tmp_path)
    snapshot = protected(tmp_path)
    target = tmp_path / manifest['panels'][0]['output_file']
    target.write_bytes(b'broken destination')
    manifest_file = tmp_path / 'review/test_manifest.json'
    manifest_file.write_text(json.dumps(manifest))
    code = '''import json,os,sys
from pathlib import Path
from manga_tool import pipeline
root=Path(sys.argv[1]); failure=sys.argv[2]
manifest=json.loads((root/'review/test_manifest.json').read_text())
real_replace=pipeline.os.replace
if failure=='during_copy':
    def kill_copy(src,dst):
        with open(dst,'wb') as handle:
            handle.write(b'partial PNG from killed writer')
            handle.flush();os.fsync(handle.fileno())
        os._exit(47)
    pipeline.shutil.copyfile=kill_copy
else:
    def kill_replace(src,dst):
        result=real_replace(src,dst)
        if Path(dst).parent.name=='proposals':os._exit(47)
        return result
    pipeline.os.replace=kill_replace
pipeline.sync_outputs(root,manifest)
'''
    process = subprocess.run([sys.executable, '-c', code, str(tmp_path), failure],
                             cwd=Path(pipeline.__file__).resolve().parents[1], check=False)
    assert process.returncode == 47
    assert protected(tmp_path) == snapshot
    if failure == 'during_copy':
        assert list(tmp_path.rglob('*.tmp'))
    # Also recover the legacy fixed-name marker staging file and a review-category
    # projection stage. An unrelated user temp must not be treated as ours.
    (tmp_path / 'review/projections.json.tmp').write_bytes(b'old marker stage')
    stale = tmp_path / ('needs_review/uncertain/CH01_P999.png.' + 'a' * 32 + '.tmp')
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b'partial rejected projection')
    pipeline.sync_outputs(tmp_path, manifest)
    assert_intact(tmp_path, manifest, snapshot)


def test_corrupt_projection_marker_is_rebuilt_from_manifest_and_real_hashes(tmp_path):
    manifest = fixture(tmp_path)
    snapshot = protected(tmp_path)
    (tmp_path / 'review/projections.json').write_bytes(b'{bad JSON')
    (tmp_path / manifest['panels'][0]['output_file']).unlink()
    pipeline.sync_outputs(tmp_path, manifest)
    assert_intact(tmp_path, manifest, snapshot)


def test_projection_metadata_cannot_delete_originals_or_revision_history(tmp_path):
    manifest = fixture(tmp_path)
    snapshot = protected(tmp_path)
    (tmp_path / 'review/projections.json').write_text(json.dumps({
        'originals/source.png': 'stale', manifest['panels'][0]['file']: 'stale'}))
    pipeline.sync_outputs(tmp_path, manifest)
    assert_intact(tmp_path, manifest, snapshot)
