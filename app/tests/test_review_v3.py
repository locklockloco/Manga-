"""Review API regression checks use exact, authored source pixels, never gold claims."""
import hashlib
import io
from pathlib import Path
import threading
import time

import pytest
from PIL import Image, ImageDraw

from manga_tool import pipeline
from manga_tool.review import create_app


def make_review_workspace(root):
    root = Path(root)
    for folder in ('originals', 'review', 'proposals', 'approved_artwork', 'reports', 'contact_sheets', 'reconstructed'):
        (root/folder).mkdir(parents=True, exist_ok=True)
    full = Image.new('RGB', (320, 600), 'white')
    draw = ImageDraw.Draw(full)
    draw.rectangle((25, 25, 294, 574), fill='#476a93')
    draw.line((40, 40, 280, 560), fill='#e0c475', width=12)
    draw.ellipse((55, 70, 170, 130), fill='white', outline='black', width=2)
    sources = []
    for n in range(2):
        path = root/'originals'/f'strip{n+1}.png'
        full.crop((0, n*300, 320, (n+1)*300)).save(path)
        sources.append({'id':f'S{n+1:04d}', 'order':n+1, 'original_name':path.name,
                        'origin':str(path), 'path':str(path.relative_to(root)), 'width':320, 'height':300,
                        'y0':n*300, 'y1':(n+1)*300, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'mode':'RGB'})
    panel = {'id':'UI_P001', 'bounds':[20, 20, 300, 580], 'status':'NEEDS_REVIEW',
             'classification':'illustration', 'revision':1, 'review':{}, 'confidence':.5,
             'flags':['AUTOMATIC_BOUNDARIES_REQUIRE_REVIEW','UNVERIFIED_JOIN'], 'join_ids':['S0001--S0002']}
    manifest = {'version':3, 'chapter':'UI', 'chapter_width':320, 'total_height':600,
                'sources':sources, 'panels':[panel], 'joins':[{'id':'S0001--S0002', 'y':300, 'status':'UNVERIFIED'}],
                'options':{'reading_direction':'ltr'}, 'history':[], 'warnings':[], 'excluded':[]}
    pipeline.export_panel(root, manifest, panel)
    pipeline.save_manifest(root, manifest, operation='checkpoint')
    return root


@pytest.fixture
def workspace(tmp_path):
    return make_review_workspace(tmp_path/'review-output')


def test_originals_previews_native_window_and_download(workspace):
    client=create_app(workspace).test_client()
    for route in ('/', '/api/manifest', '/api/source/S0001', '/api/panels/UI_P001',
                  '/api/preview/UI_P001/context', '/api/preview/UI_P001/crop', '/api/download/UI_P001'):
        assert client.get(route).status_code==200, route
    response=client.get('/api/viewport?x0=20&y0=250&x1=300&y1=350')
    with Image.open(io.BytesIO(response.data)) as native:
        assert native.size==(280,100)
    assert client.get('/api/viewport?x0=-1&y0=0&x1=20&y1=20').status_code==400
    assert 'UI_P001.png' in client.get('/api/download/UI_P001').headers['Content-Disposition']


def test_no_automatic_or_bulk_approval(workspace):
    client=create_app(workspace).test_client()
    assert client.post('/api/panels/UI_P001',json={'status':'VERIFIED'}).status_code==400
    assert client.post('/api/panels/UI_P001',json={'status':'VERIFIED','checked':True}).status_code==400
    response=client.post('/api/panels/UI_P001',json={'status':'VERIFIED','checked':True,'joins_checked':True,'note':'API interaction fixture'})
    assert response.status_code==200, response.json
    p=response.json['panels'][0]
    assert p['review']['reviewer']=='user'
    assert p['output_file'].startswith('approved_artwork/')
    assert client.post('/api/panels/ALL',json={'status':'VERIFIED','checked':True,'joins_checked':True}).status_code==400


def test_v3_classification_and_polygon_persistence(workspace):
    client=create_app(workspace).test_client()
    for classification in ('standalone_sfx','dialogue_or_narration_only','title_or_credits','compound_layout','uncertain','embedded_sfx_or_bubble','illustration'):
        r=client.post('/api/classify',json={'id':'UI_P001','classification':classification})
        assert r.status_code==200,r.json
        assert r.json['panels'][0]['classification']==classification
        assert r.json['panels'][0]['status']==('IGNORED' if classification in {'standalone_sfx','dialogue_or_narration_only'} else 'NEEDS_REVIEW')
    polygon=[[20,20],[300,20],[280,580],[20,580]]
    r=client.post('/api/panels/UI_P001',json={'bounds':[20,20,300,580],'polygon':polygon})
    assert r.status_code==200,r.json
    assert r.json['panels'][0]['polygon']==polygon
    preview=client.get('/api/preview/UI_P001/crop')
    with Image.open(io.BytesIO(preview.data)) as image:
        assert image.mode=='RGBA'
    assert create_app(workspace).test_client().get('/api/manifest').json['panels'][0]['polygon']==polygon
    assert client.post('/api/panels/UI_P001',json={'polygon':[]}).status_code==200


def test_split_merge_undo_redo_flag_and_errors(workspace):
    client=create_app(workspace).test_client()
    r=client.post('/api/split',json={'id':'UI_P001','y':160,'axis':'x'})
    assert r.status_code==200,r.json
    panels=[p for p in r.json['panels'] if p['status']=='NEEDS_REVIEW']
    assert len(panels)==2
    ids=[p['id'] for p in panels]
    assert client.post('/api/merge',json={'ids':ids}).status_code==200
    assert client.post('/api/undo',json={}).status_code==200
    assert client.post('/api/redo',json={}).status_code==200
    assert client.post('/api/flag',json={'id':'UI_P001','note':'check sword edge'}).status_code==200
    m=client.get('/api/manifest').json
    assert 'USER_FLAGGED' in next(p for p in m['panels'] if p['id']=='UI_P001')['flags']
    assert client.post('/api/split',json={'id':'UI_P001','y':10000,'axis':'y'}).status_code==400
    assert client.get('/api/download/not-a-region').status_code==400
    assert client.get('/api/state',base_url='http://evil.example').status_code==403
    assert client.post('/api/flag',json={},headers={'Origin':'https://evil.example'}).status_code==403


def test_pause_control_is_available_while_worker_runs(workspace, monkeypatch):
    gate=threading.Event()
    started=threading.Event()
    def fake_process(*args, **kwargs):
        started.set()
        gate.wait(2)
    def fake_control(root,action):
        return {'stage':'EXPORT','status':'PAUSED' if action=='pause' else 'CANCELLED','current':75,'total':200}
    monkeypatch.setattr(pipeline,'process_chapter',fake_process)
    monkeypatch.setattr(pipeline,'job_status',lambda root:{'stage':'EXPORT','status':'RUNNING','current':75,'total':200},raising=False)
    monkeypatch.setattr(pipeline,'job_control',fake_control,raising=False)
    client=create_app(workspace).test_client()
    try:
        r=client.post('/api/import',json={'input':str(workspace/'originals'),'output':str(workspace)})
        assert r.status_code==202
        assert started.wait(1)
        assert client.get('/api/job').json['current']==75
        assert client.post('/api/job/control',json={'action':'pause'}).status_code==200
        assert client.post('/api/job/control',json={'action':'cancel'}).status_code==200
        assert client.post('/api/panels/UI_P001',json={'status':'REJECTED'}).status_code==409
    finally:
        gate.set()


def test_real_import_preflight_then_resume(tmp_path):
    source=tmp_path/'input'
    source.mkdir()
    for n in range(2):
        image=Image.new('RGB',(320,350),'white')
        ImageDraw.Draw(image).rectangle((30,30 if n==0 else 0,289,349 if n==0 else 319),fill='#4c74aa')
        image.save(source/f'strip{n+1}.png')
    output=tmp_path/'preflight-output'
    client=create_app().test_client()
    assert client.post('/api/import',json={'input':str(source),'output':str(output),'preflight':True,'include_all':True}).status_code==202
    def stopped():
        for _ in range(600):
            job=client.get('/api/job').json
            if not job.get('running'):
                return job
            time.sleep(.025)
        pytest.fail('Local fixture job did not stop within fifteen seconds')
    job=stopped()
    assert job['status']=='PAUSED',job
    manifest=client.get('/api/manifest').json
    assert len(manifest['sources'])==2
    assert not manifest['panels']
    ids=[s['id'] for s in manifest['sources']]
    assert client.post('/api/reorder/sources',json={'ids':ids}).status_code==200
    assert client.post('/api/job/control',json={'action':'resume'}).status_code==202
    job=stopped()
    assert job['status'] in {'REVIEW_REQUIRED','REVIEW_READY'},job
    result=client.get('/api/manifest').json
    assert result['panels']
    assert [s['id'] for s in result['sources']]==ids
    assert not any(p['status']=='VERIFIED' for p in result['panels'])
