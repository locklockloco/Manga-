"""Actual Chapter 1 evidence tests; never substitute synthetic assets.

Set MANGA_CH01_EVIDENCE and MANGA_CH01_RESULTS. Geometry assertions concern
reported failures, not independently human-approved exact segmentation gold.
"""
import os,json,csv,hashlib
from pathlib import Path
import pytest

E=Path(os.environ.get('MANGA_CH01_EVIDENCE','/nonexistent'))
R=Path(os.environ.get('MANGA_CH01_RESULTS','/nonexistent'))
pytestmark=pytest.mark.skipif(not (E/'SOURCES/CH01_SHA256.csv').exists() or not (R/'portrait_output/reports/manifest.json').exists(),reason='Requires actual supplied original Chapter 1 evidence and executed real outputs')


def manifest(name):return json.loads((R/(name+'_output')/'reports/manifest.json').read_text())


def test_all_194_original_sources_match_supplied_hashes():
    rows=list(csv.DictReader((E/'SOURCES/CH01_SHA256.csv').open()))
    assert [r['filename'] for r in rows]==[f'S{i:04d}.jpg' for i in range(1,195)]
    for r in rows:
        p=E/'SOURCES/CH01_ORIGINALS'/r['filename']
        assert p.stat().st_size==int(r['bytes'])
        assert hashlib.sha256(p.read_bytes()).hexdigest()==r['sha256']


def test_real_dark_opening_has_separate_native_artwork_exports():
    m=manifest('opening');art=[p for p in m['panels'] if p.get('file')]
    assert len(art)>=10
    assert all(p['bounds'][3]-p['bounds'][1]<10000 for p in art)
    assert any(len(p['source_regions'])>=3 for p in art)
    assert not any(p['status']=='VERIFIED' for p in m['panels'])


def test_portrait_is_tighter_and_retains_original_character_frame():
    m=manifest('portrait')
    # Required visible original frame, not the supplied manual-reference bbox.
    roi=[156,2007,549,2668]
    candidates=[p for p in m['panels'] if p.get('file') and p['bounds'][0]<=roi[0] and p['bounds'][1]<=roi[1] and p['bounds'][2]>=roi[2] and p['bounds'][3]>=roi[3]]
    assert candidates
    p=min(candidates,key=lambda p:(p['bounds'][2]-p['bounds'][0])*(p['bounds'][3]-p['bounds'][1]))
    assert p['dimensions'][0]<450 and p['dimensions'][1]<=721
    assert len(p['source_regions'])==2


def test_reported_text_negatives_have_no_public_or_revision_png():
    m=manifest('text');old=json.loads((E/'EVIDENCE/V33_text_manifest.json').read_text())
    for id in ['CH01_P002','CH01_P003','CH01_P004','CH01_P010','CH01_P014','CH01_P015','CH01_P026','CH01_P031','CH01_P032']:
        q=next(p for p in old['panels'] if p['id']==id);a=q['bounds']
        for p in m['panels']:
            if not p.get('file'):continue
            b=p['bounds'];area=(b[2]-b[0])*(b[3]-b[1])
            inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
            assert inter/area<.9,(id,p['id'])
    assert not any(p.get('proposal_role')=='child' and p.get('file') for p in m['panels'])


def test_real_full_run_integrity_and_no_automatic_approval():
    path=R/'full_output/reports/manifest.json'
    if not path.exists() or not (R/'full_output/reports/performance.json').exists():pytest.skip('Full real run not yet completed')
    m=manifest('full');qa=json.loads((R/'full_output/reports/qa.json').read_text())
    assert len(m['sources'])==194
    assert not any(p['status']=='VERIFIED' for p in m['panels'])
    assert qa['required_checks']['pixel_fidelity']['status']=='PASS'
    assert qa['required_checks']['source_integrity']['status']=='PASS'


@pytest.mark.parametrize('action,status',[('pause','PAUSED'),('cancel','CANCELLED')])
def test_actual_portrait_classification_control_resumes_without_source_changes(tmp_path,monkeypatch,action,status):
    import shutil
    from manga_tool.pipeline import process_chapter,job_status
    from manga_tool.storage import Store,JobPaused,JobCancelled
    inputs=tmp_path/'originals';inputs.mkdir()
    for i in range(175,180):shutil.copyfile(E/f'SOURCES/CH01_ORIGINALS/S{i:04d}.jpg',inputs/f'S{i:04d}.jpg')
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.iterdir()}
    checkpoint=Store.checkpoint;requested=False
    def controlled(self,stage,current,total,**info):
        nonlocal requested
        if stage=='CLASSIFY' and current==1 and not requested:
            requested=True;self.request_control(action)
        return checkpoint(self,stage,current,total,**info)
    output=tmp_path/'out'
    with monkeypatch.context() as patch:
        patch.setattr(Store,'checkpoint',controlled)
        with pytest.raises(JobPaused if action=='pause' else JobCancelled):
            process_chapter(inputs,output,include_all=True,generate_contact_sheets=False)
    assert requested and job_status(output)['status']==status
    m=process_chapter(inputs,output,include_all=True,generate_contact_sheets=False)
    assert m['panels'] and not any(p['status']=='VERIFIED' for p in m['panels'])
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==before[p.name] for p in inputs.iterdir())
