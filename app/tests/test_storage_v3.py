"""Durability/locking tests: failures must leave the prior manifest usable."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from manga_tool.storage import (Store, writer_lock, WriterBusy, JobPaused,
                                JobCancelled, ResourceLimitError, preflight)


def manifest(root, revision):
    path = root / 'proposals' / f'P001_r{revision:04d}.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f'complete immutable revision {revision}'.encode())
    return {'version': 3, 'revision': revision, 'sources': [],
            'panels': [{'id': 'P001', 'file': str(path.relative_to(root)), 'revision': revision}]}


def test_snapshots_undo_redo_and_reopen(tmp_path):
    store = Store(tmp_path)
    first, second, third = [manifest(tmp_path, n) for n in (1, 2, 3)]
    a = store.commit_manifest(first, 'initial')
    b = store.commit_manifest(second, 'adjust_crop', before=first)
    assert b > a
    assert Store(tmp_path).load_manifest() == second
    assert store.undo() == first
    assert Store(tmp_path).load_manifest() == first
    assert store.redo() == second
    assert store.undo() == first
    store.commit_manifest(third, 'new_branch')
    with pytest.raises(ValueError, match='redo'):
        store.redo()
    assert first['panels'][0]['file'] in store.referenced_files()
    assert second['panels'][0]['file'] in store.referenced_files()
    assert third['panels'][0]['file'] in store.referenced_files()
    assert sum(s['current'] for s in store.snapshots()) == 1


def test_checkpoint_does_not_add_hundreds_of_undo_snapshots(tmp_path):
    store = Store(tmp_path)
    for index in range(1, 41):
        store.commit_manifest(manifest(tmp_path, index), 'checkpoint')
    assert store.snapshots() == []
    assert Store(tmp_path).load_manifest()['revision'] == 40
    store.commit_manifest(manifest(tmp_path, 41), 'job_complete')
    assert len(store.snapshots()) == 1
    store.commit_manifest(manifest(tmp_path, 42), 'adjust_crop')
    assert store.undo()['revision'] == 41


def test_failed_transaction_keeps_last_valid_output(tmp_path, monkeypatch):
    store = Store(tmp_path)
    first, second = manifest(tmp_path, 1), manifest(tmp_path, 2)
    store.commit_manifest(first)
    original = Store._event
    def fail(db, name, details):
        if name == 'commit':
            raise OSError('simulated power loss before manifest commit')
        original(db, name, details)
    monkeypatch.setattr(Store, '_event', staticmethod(fail))
    with pytest.raises(OSError, match='power loss'):
        store.commit_manifest(second)
    assert Store(tmp_path).load_manifest() == first
    assert (tmp_path / first['panels'][0]['file']).is_file()
    assert len(store.snapshots()) == 1


def test_missing_staged_file_rejects_manifest_commit(tmp_path):
    store = Store(tmp_path)
    first = manifest(tmp_path, 1)
    store.commit_manifest(first)
    missing = {'panels': [{'file': 'proposals/incomplete.png'}]}
    with pytest.raises(ValueError, match='missing file'):
        store.commit_manifest(missing)
    assert store.load_manifest() == first


def _run_python(script, *args, **kwargs):
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[1])
    env['PYTHONPATH'] = package_root + os.pathsep + env.get('PYTHONPATH', '')
    return subprocess.Popen([sys.executable, '-c', script, *map(str,args)], env=env, **kwargs)


def test_sudden_exit_with_uncommitted_sqlite_transaction(tmp_path):
    store = Store(tmp_path)
    first = manifest(tmp_path, 1)
    store.commit_manifest(first)
    script = '''
import json, os, sqlite3, sys
from manga_tool.storage import Store
s=Store(sys.argv[1])
db=sqlite3.connect(s.path)
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE state SET value=? WHERE key='current_manifest'", (json.dumps({'broken':True}),))
os._exit(27)
'''
    process = _run_python(script, tmp_path)
    assert process.wait(timeout=10) == 27
    assert Store(tmp_path).load_manifest() == first
    with sqlite3.connect(store.path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_competing_writer_rejected_then_killed_lock_recovers(tmp_path):
    ready = tmp_path / 'ready'
    script = '''
import pathlib, sys, time
from manga_tool.storage import writer_lock
with writer_lock(sys.argv[1]):
    pathlib.Path(sys.argv[2]).write_text('locked')
    time.sleep(60)
'''
    process = _run_python(script, tmp_path, ready)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists(), 'Lock subprocess did not start'
        with pytest.raises(WriterBusy):
            with writer_lock(tmp_path):
                pass
        process.kill()
        process.wait(timeout=10)
        # Remaining diagnostic text cannot keep a dead writer locked.
        assert (tmp_path / 'review' / 'writer.lock').exists()
        with writer_lock(tmp_path):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_readers_and_controls_work_during_os_writer_lock(tmp_path):
    store = Store(tmp_path)
    expected = manifest(tmp_path, 1)
    store.commit_manifest(expected)
    with writer_lock(tmp_path):
        assert Store(tmp_path).load_manifest() == expected
        store.request_control('pause')
        with pytest.raises(JobPaused):
            store.checkpoint('INDEX', 75, 200, current_source='S0075')
    job = Store(tmp_path).job()
    assert job['status'] == 'PAUSED'
    assert job['stage'] == 'INDEX' and job['current'] == 75
    assert job['current_source'] == 'S0075'
    store.request_control('resume')
    assert store.checkpoint('INDEX', 76, 200)['status'] == 'RUNNING'
    store.request_control('cancel')
    with pytest.raises(JobCancelled):
        store.checkpoint('INDEX', 77, 200)
    assert store.job()['status'] == 'CANCELLED'


def test_source_region_checkpoints_upsert_without_duplicates(tmp_path):
    store = Store(tmp_path)
    store.item('source', 'S0090', 'FAILED', checksum='abc', error='Corrupt JPEG')
    store.item('source', 'S0090', 'INDEXED', error=None)
    rows = Store(tmp_path).items('source')
    assert len(rows) == 1
    assert rows[0]['status'] == 'INDEXED'
    assert rows[0]['checksum'] == 'abc'
    assert rows[0]['error'] is None
    store.item('region', 'P001', 'EXPORTED', panel={'file': 'proposals/P001_r1.png'})
    assert 'proposals/P001_r1.png' in store.referenced_files()


def test_resource_estimates_fail_before_processing(tmp_path):
    assert preflight(tmp_path, reserve_bytes=0)['passed']
    with pytest.raises(ResourceLimitError, match='disk'):
        preflight(tmp_path, estimated_output_bytes=2**63)
    with pytest.raises(ResourceLimitError, match='memory'):
        preflight(tmp_path, estimated_memory_bytes=1024, max_memory_bytes=100)


def test_sqlite_wal_and_full_sync(tmp_path):
    store = Store(tmp_path)
    with store._connection() as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert db.execute('PRAGMA synchronous').fetchone()[0] == 2
