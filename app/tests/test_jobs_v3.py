"""Crash/resource/job regressions using representative-size synthetic sources.

Long 200/500-strip runs are opt-in with MANGA_RUN_STRESS=1 and are also runnable
through tools/stress_v3.py. Default tests simulate resource errors without filling
the filesystem or exhausting the actual host's memory.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import signal

import pytest

from tools.stress_v3 import audit_output, generate_fixture, run_process, sha256


@pytest.fixture
def small_fixture(tmp_path):
    fixture=tmp_path/'fixture'
    generate_fixture(fixture,strips=4)
    return fixture


def pipeline():
    from manga_tool import pipeline as module
    return module


def committed_files(output):
    """Only filenames already committed in authoritative state are counted."""
    from manga_tool.storage import Store
    manifest=Store(output).load_manifest() or {}
    return {p['id']:(p['file'],sha256(Path(output)/p['file'])) for p in manifest.get('panels',[]) if p.get('file') and (Path(output)/p['file']).exists()}


@pytest.mark.parametrize('error', [OSError(errno.ENOSPC,'injected disk full'),
                                  PermissionError(errno.EACCES,'injected permission denied'),
                                  MemoryError('injected decoder/export memory exhaustion')], ids=['disk_full','permission_denied','low_memory'])
def test_export_fault_preserves_committed_work_and_can_retry(small_fixture,tmp_path,monkeypatch,error):
    from manga_tool.chapter import Chapter
    module=pipeline()
    output=tmp_path/'output'
    original=Chapter.save_png
    calls=0
    def fail_second(self,bounds,path,**options):
        nonlocal calls
        calls+=1
        if calls==2:
            raise error
        return original(self,bounds,path,**options)
    with monkeypatch.context() as patch:
        patch.setattr(Chapter,'save_png',fail_second)
        with pytest.raises((OSError,MemoryError)):
            module.process_chapter(small_fixture/'input',output,chapter='FAULT',generate_contact_sheets=False)
    failed=module.job_status(output)
    assert failed['status']=='FAILED', failed
    assert failed.get('error'), failed
    before=committed_files(output)
    assert before, 'At least the first export must have committed before injected fault'
    originals={str(path.relative_to(output)):sha256(path) for path in (output/'originals').iterdir() if path.is_file()}
    module.process_chapter(small_fixture/'input',output,chapter='FAULT',generate_contact_sheets=False)
    after=committed_files(output)
    assert all(after[key]==value for key,value in before.items())
    assert all(sha256(output/name)==digest for name,digest in originals.items())
    report=audit_output(small_fixture,output)
    assert not report['errors'], report['errors']
    assert module.job_status(output)['status']!='FAILED'


def test_low_memory_and_disk_preflight_refuse_before_allocation(tmp_path,monkeypatch):
    from manga_tool import storage
    baseline={'disk_free_bytes':1024**3,'disk_total_bytes':2*1024**3,
              'available_memory_bytes':8*1024**2,'process_memory_bytes':None,'observed_at':'test'}
    monkeypatch.setattr(storage,'resource_usage',lambda root:baseline)
    with pytest.raises(storage.ResourceLimitError,match='memory'):
        storage.preflight(tmp_path,estimated_memory_bytes=9*1024**2)
    with pytest.raises(storage.ResourceLimitError,match='disk'):
        storage.preflight(tmp_path,estimated_output_bytes=2*1024**3)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name=='nt' or not hasattr(signal,'SIGKILL'),reason='POSIX SIGKILL test; native Windows crash testing pending')
def test_real_sigkill_index_resume_has_no_duplicate_or_lost_outputs(small_fixture,tmp_path):
    output=tmp_path/'output'
    result=run_process(small_fixture,output,tmp_path/'logs','kill_index',
                       {'MANGA_TEST_EXIT_STAGE':'INDEX','MANGA_TEST_EXIT_AT':'2','MANGA_TEST_SIGKILL':'1'})
    assert result['returncode']==-signal.SIGKILL, result
    status=pipeline().job_status(output)
    assert status['stage']=='INDEX' and status['current']==2, status
    result=run_process(small_fixture,output,tmp_path/'logs','resume')
    assert result['returncode']==0, result
    report=audit_output(small_fixture,output)
    assert report['source_count']==4
    assert not report['errors'], report['errors']
    before=report['checksums']
    result=run_process(small_fixture,output,tmp_path/'logs','resume_again')
    assert result['returncode']==0
    assert audit_output(small_fixture,output)['checksums']==before


@pytest.mark.skipif(os.name=='nt' or not hasattr(signal,'SIGKILL'),reason='POSIX SIGKILL test; native Windows crash testing pending')
def test_export_crash_preserves_prior_manual_correction(small_fixture,tmp_path):
    output=tmp_path/'output'
    logs=tmp_path/'logs'
    killed=run_process(small_fixture,output,logs,'kill_after_export',
                       {'MANGA_TEST_EXIT_STAGE':'EXPORT','MANGA_TEST_EXIT_AT':'2','MANGA_TEST_SIGKILL':'1'})
    assert killed['returncode']==-signal.SIGKILL, killed
    module=pipeline()
    manifest=module.load_manifest(output)
    first=next(p for p in manifest['panels'] if p.get('file'))
    # Simulate a user's coordinate correction, without claiming visual approval.
    bounds=list(first['bounds'])
    bounds[1]+=1
    edited=module.update_panel(output,first['id'],bounds=bounds)
    edited_panel=next(p for p in edited['panels'] if p['id']==first['id'])
    saved_hash=sha256(output/edited_panel['file'])
    failed=run_process(small_fixture,output,logs,'kill_before_commit',
                       {'MANGA_TEST_EXIT_STAGE':'EXPORT','MANGA_TEST_EXIT_AT':'3','MANGA_TEST_EXIT_POINT':'before_commit','MANGA_TEST_SIGKILL':'1'})
    assert failed['returncode']==-signal.SIGKILL, failed
    resumed=run_process(small_fixture,output,logs,'resume_export')
    assert resumed['returncode']==0, resumed
    final=module.load_manifest(output)
    result=next(p for p in final['panels'] if p['id']==first['id'])
    assert result['bounds']==bounds
    assert sha256(output/result['file'])==saved_hash
    assert result['status']!='VERIFIED'
    assert not audit_output(small_fixture,output)['errors']


@pytest.mark.large
@pytest.mark.skipif(os.name=='nt' or not hasattr(signal,'SIGKILL'),reason='POSIX SIGKILL stress test')
@pytest.mark.skipif(os.environ.get('MANGA_RUN_STRESS')!='1',reason='Opt-in full-size 200-strip performance/crash run')
def test_representative_200_strip_stress(tmp_path):
    from tools.stress_v3 import run_suite
    result=run_suite(tmp_path/'stress200',strips=200)
    assert result['audit_passed'] and result['source_count']==200


@pytest.mark.large
@pytest.mark.skipif(os.environ.get('MANGA_RUN_STRESS')!='1',reason='Opt-in full-size 200-strip damaged-source run')
def test_representative_corrupt_strip_90(tmp_path):
    from tools.stress_v3 import run_suite
    result=run_suite(tmp_path/'corrupt200',strips=200,include_interrupts=False,corrupt=True)
    assert result['audit_passed']
    manifest=pipeline().load_manifest(tmp_path/'corrupt200'/'output')
    # The source error must be visible; a damaged image must never be accepted
    # as a complete decoded illustration or silently disappear from reporting.
    assert '90' in str(manifest.get('errors',[])+manifest.get('warnings',[])+manifest.get('excluded',[])), manifest.keys()
    assert pipeline().job_status(tmp_path/'corrupt200'/'output')['status']=='REVIEW_REQUIRED'


@pytest.mark.parametrize('action,expected', [('pause','PAUSED'),('cancel','CANCELLED')])
def test_control_request_acknowledged_at_checkpoint_and_resumable(small_fixture,tmp_path,monkeypatch,action,expected):
    from manga_tool import storage
    module=pipeline()
    output=tmp_path/'control'
    checkpoint=storage.Store.checkpoint
    requested=False
    def controlled(self,stage,current,total,**info):
        nonlocal requested
        if stage=='INDEX' and current==2 and not requested:
            requested=True
            self.request_control(action)
        return checkpoint(self,stage,current,total,**info)
    exception=storage.JobPaused if action=='pause' else storage.JobCancelled
    with monkeypatch.context() as patch:
        patch.setattr(storage.Store,'checkpoint',controlled)
        with pytest.raises(exception):
            module.process_chapter(small_fixture/'input',output,chapter='CONTROL',generate_contact_sheets=False)
    assert requested
    assert module.job_status(output)['status']==expected
    assert not module.job_status(output).get('error')
    original_hashes={path.name:sha256(path) for path in (output/'originals').iterdir() if path.is_file()}
    module.process_chapter(small_fixture/'input',output,chapter='CONTROL',generate_contact_sheets=False)
    assert all(sha256(output/'originals'/name)==digest for name,digest in original_hashes.items())
    assert not audit_output(small_fixture,output)['errors']
