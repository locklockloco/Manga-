"""Risk prioritization separates review obligations from detected defects."""
from copy import deepcopy
import hashlib
import io
import runpy
from pathlib import Path

from PIL import Image

from manga_tool import pipeline
from manga_tool.review import create_app, panel_review_risk, seam_review_summary


def sample():
    source={'id':'S0001','width':320,'height':300,'y0':0,'y1':300}
    join={'id':'S0001--S0002','y':300,'left_source':'S0001','right_source':'S0002',
          'assessment':'probable_continuation','risk':'ordinary_continuation','status':'UNVERIFIED','flags':[]}
    panel={'id':'P1','status':'NEEDS_REVIEW','classification':'illustration',
           'bounds':[0,0,320,600],'flags':['UNVERIFIED_JOIN','AUTOMATIC_BOUNDARIES_REQUIRE_REVIEW','BOUNDARY_INTERSECTS_CONTENT_OR_SOURCE_EDGE','TALL_ARTWORK_PRESERVED'],
           'source_regions':[{'source_id':'S0001','source_bounds':[0,0,320,300]}], 'join_ids':[join['id']]}
    return panel,{'sources':[source],'joins':[join]},join


def test_ordinary_join_and_full_bleed_do_not_raise_defect_priority():
    panel,m,join=sample()
    risk=panel_review_risk(panel,m)
    assert risk['score']==0
    assert risk['level']=='routine'
    assert risk['suspicious_seams']==0
    assert risk['ordinary_unreviewed_seams']==1
    assert risk['human_review']=='pending'
    assert seam_review_summary(join)['human_checked'] is False


def test_suspicious_and_uncertain_joins_remain_separate_from_human_state():
    panel,m,join=sample()
    join['risk']='suspicious'
    risk=panel_review_risk(panel,m)
    assert risk['suspicious_seams']==1
    assert risk['level']=='high'
    assert risk['human_review']=='pending'
    qa={'seam_risks':[{'id':join['id'],'kind':'uncertain_continuation_requires_inspection','priority':'medium','human_checked':False}]}
    join['risk']='uncertain'
    risk=panel_review_risk(panel,m,qa)
    assert risk['uncertain_seams']==1
    assert risk['suspicious_seams']==0
    assert risk['level']=='medium'


def test_unused_adjacency_is_not_a_reconstruction_review_obligation():
    _,_,join=sample()
    qa={'kind':'unused_source_adjacency_not_a_reconstruction_join','requires_human_inspection':False,'human_checked':False}
    result=seam_review_summary(join,qa)
    assert result['severity']=='ordinary'
    assert result['human_checked'] is False
    assert result['requires_human_inspection'] is False


def test_explicit_cutoff_risk_excludes_faint_boundary_observations():
    panel,m,_=sample()
    region={'id':'P1','cutoff_risk':False,'linework_crossings':[{'suspected_cut':True,'crossing_pixels':100}],
            'warnings':['CUT_BOUNDARY_INTERSECTS_SIGNIFICANT_ORIGINAL_LINEWORK']}
    assert 'cutoff' not in panel_review_risk(panel,m,{'per_region':[region]})['categories']
    region['cutoff_risk']=True
    assert 'cutoff' in panel_review_risk(panel,m,{'per_region':[region]})['categories']


def test_merge_unclassified_duplicates_and_coordinates_prioritize_real_evidence():
    panel,m,_=sample()
    parent=deepcopy(panel);parent['classification']='compound_layout'
    assert 'merge' in panel_review_risk(parent,m)['categories']
    uncertain=deepcopy(panel);uncertain['classification']='uncertain'
    assert 'unclassified' in panel_review_risk(uncertain,m)['categories']
    assert 'duplicate' in panel_review_risk(panel,m,{'near_duplicates':[{'ids':['P1','P2']}]})['categories']
    invalid=deepcopy(panel);invalid['source_regions'][0]['source_bounds'][2]=321
    assert 'coordinates' in panel_review_risk(invalid,m)['categories']
    assert panel_review_risk(invalid,m)['score']>panel_review_risk(uncertain,m)['score']


def test_neighboring_sources_and_original_bytes_are_read_only(tmp_path):
    make_workspace=runpy.run_path(str(Path(__file__).with_name('test_review_v3.py')))['make_review_workspace']
    root=make_workspace(tmp_path/'review')
    m=pipeline.load_manifest(root)
    p=m['panels'][0]
    # A crop in only the first strip still exposes the following original strip.
    p['bounds']=[20,20,300,280]
    p['join_ids']=[]
    pipeline.export_panel(root,m,p)
    pipeline.save_manifest(root,m,operation='checkpoint')
    before=(root/'reports/manifest.json').read_bytes()
    client=create_app(root).test_client()
    info=client.get('/api/panels/UI_P001').json
    assert [s['id'] for s in info['neighboring_sources']]==['S0001','S0002']
    assert info['risk']['human_review']=='pending'
    source=m['sources'][0]
    original=client.get('/api/original/S0001')
    assert hashlib.sha256(original.data).hexdigest()==source['sha256']
    native=client.get('/api/source/S0001?native=1')
    with Image.open(io.BytesIO(native.data)) as image:
        assert image.size==(source['width'],source['height'])
    queue=client.get('/api/risk').json
    assert queue['regions'][0]['id']=='UI_P001'
    assert (root/'reports/manifest.json').read_bytes()==before



def test_explicit_qa_false_overrides_legacy_checked_status_and_review():
    _,_,join=sample()
    join.update(status='VISUALLY_CHECKED',review={'reviewer':'user','method':'explicit_human_visual_review','joins_checked':True})
    result=seam_review_summary(join,{'human_checked':False,'requires_human_inspection':True})
    assert result['human_checked'] is False
    assert result['human_review']=='pending'
    assert seam_review_summary(join,{'human_checked':True})['human_checked'] is True


def test_join_status_alone_cannot_establish_human_inspection():
    _,_,join=sample()
    for status in ('HUMAN_VERIFIED','VERIFIED','VISUALLY_CHECKED'):
        join['status']=status
        assert seam_review_summary(join)['human_checked'] is False
    join['review']={'reviewer':'assistant','method':'assistant_visual_inspection','checked':True}
    assert seam_review_summary(join)['human_checked'] is False
    join['review']={'reviewer':'human','method':'explicit_human_visual_review','checked':True}
    assert seam_review_summary(join)['human_checked'] is True
    join['review']['joins_checked']=False
    assert seam_review_summary(join)['human_checked'] is False


def test_panel_human_label_requires_explicit_human_review_method():
    panel,m,_=sample()
    panel['status']='VERIFIED'
    panel['review']={'reviewer':'user','artwork_checked':True}
    assert panel_review_risk(panel,m)['human_review']=='pending'
    panel['review']['method']='assistant_visual_inspection'
    assert panel_review_risk(panel,m)['human_review']=='pending'
    panel['review']['method']='explicit_human_visual_review'
    assert panel_review_risk(panel,m)['human_review']=='checked'
    panel['classification']='title_or_credits'
    assert panel_review_risk(panel,m)['human_review']=='checked'

def run_neighbor_browser_smoke(screenshot=None):
    """Optional executable smoke: PYTHONPATH=. python tests/test_review_risk.py --browser."""
    import tempfile
    from threading import Thread
    from werkzeug.serving import make_server, WSGIRequestHandler
    from playwright.sync_api import sync_playwright
    class Quiet(WSGIRequestHandler):
        def log_request(self,*args,**kwargs):
            pass
    make_workspace=runpy.run_path(str(Path(__file__).with_name('test_review_v3.py')))['make_review_workspace']
    with tempfile.TemporaryDirectory(prefix='review-neighbors-') as tmp:
        root=make_workspace(Path(tmp)/'output')
        m=pipeline.load_manifest(root)
        m['joins'][0].update(left_source='S0001',right_source='S0002',risk='ordinary_continuation',assessment='probable_continuation')
        parent=deepcopy(m['panels'][0]);parent.update(id='UI_P002',classification='compound_layout')
        pipeline.export_panel(root,m,parent);m['panels'].append(parent)
        m['qa']={'status':'REVIEW_REQUIRED','production_export_enabled':False,'seam_risks':[{
            'id':'S0001--S0002','kind':'ordinary_exact_row_append_unreviewed','human_checked':False,
            'requires_human_inspection':True,'seam_y':300,'priority':'low','priority_rank':85}],
            'totals':{'suspicious_seams':0,'ordinary_unreviewed_seams':1,'uncertain_seams':0}}
        pipeline.save_manifest(root,m,operation='checkpoint')
        server=make_server('127.0.0.1',0,create_app(root),threaded=True,request_handler=Quiet)
        Thread(target=server.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1600,'height':1200},bypass_csp=True)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.wait_for_function("document.querySelectorAll('.panel-item').length===2 && state.risks.UI_P002?.score>0")
                assert page.locator('.panel-item').first.locator('strong').inner_text()=='UI_P002'
                assert page.evaluate("qaPriority({error:'SOURCE_CHECKSUM_CHANGED'}) > qaPriority({priority_rank:70,pixels:900000})")
                page.locator('#riskFilter').select_option('seam')
                assert page.locator('.panel-item').count()==0
                page.locator('#riskFilter').select_option('merge')
                assert page.locator('.panel-item').count()==1
                assert page.locator('.panel-item strong').inner_text()=='UI_P002'
                page.locator('#riskFilter').select_option('')
                page.locator('.panel-item').filter(has_text='UI_P001').click()
                page.wait_for_function("state.panel.id==='UI_P001' && document.getElementById('neighborSource').options.length===2")
                assert 'Routine inspection' in page.locator('#regionRiskSummary').inner_text()
                assert 'Human review: pending' in page.locator('#regionRiskSummary').inner_text()
                page.locator('#neighborSource').select_option('S0002')
                page.wait_for_function("document.getElementById('neighborImage').complete && document.getElementById('neighborImage').naturalWidth>0 && document.getElementById('neighborImage').src.includes('S0002')")
                assert 'Crop uses source pixels [20,0,300,280]' in page.locator('#neighborInfo').inner_text()
                page.evaluate('state.manifest.qa_stale=true;loadNeighbor()')
                assert 'previous QA; refresh required' in page.locator('#neighborSeam').inner_text()
                page.evaluate('state.manifest.qa_stale=false;loadNeighbor()')
                page.locator('#neighborNative').click()
                page.wait_for_function("document.getElementById('neighborImage').src.includes('native=1') && document.getElementById('neighborImage').complete")
                assert page.locator('#neighborImage').evaluate('(im)=>[im.naturalWidth,im.naturalHeight]')==[320,300]
                from urllib.parse import urljoin
                response=page.request.get(urljoin(page.url,page.locator('#neighborOriginal').get_attribute('href')))
                assert hashlib.sha256(response.body()).hexdigest()==m['sources'][1]['sha256']
                page.locator('#neighborPrev').click()
                assert page.locator('#neighborSource').input_value()=='S0001'
                page.locator('#showNeighbors').uncheck();assert page.locator('#neighborPane').is_hidden()
                page.locator('#showNeighbors').check();assert page.locator('#neighborPane').is_visible()
                if screenshot:
                    Path(screenshot).parent.mkdir(parents=True,exist_ok=True)
                    page.locator('#fitFull').check()
                    page.wait_for_function("state.image?.complete && document.getElementById('cropImage').complete && document.getElementById('neighborImage').complete")
                    page.screenshot(path=str(screenshot),full_page=True)
                assert not errors,errors
                browser.close()
        finally:
            server.shutdown()
    print('PASS: actual-risk sorting, suspicious/ordinary seam separation, merge filter, neighboring strip selection, exact native dimensions/original bytes, optional third pane; zero JS errors.')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser',action='store_true')
    parser.add_argument('--screenshot')
    args=parser.parse_args()
    if not args.browser:
        parser.error('Use --browser, or run this file with pytest.')
    run_neighbor_browser_smoke(args.screenshot)
