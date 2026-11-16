"""QC urgency is evidence-based and independent of human approval state."""
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw

from manga_tool.chapter import Chapter
from manga_tool.qa import run_qa


def _workspace(root, images, bounds, classifications=None):
    sources=[]
    y=0
    (root/'originals').mkdir()
    for i,image in enumerate(images,1):
        relative=f'originals/strip_{i:03d}.png'
        image.save(root/relative)
        sources.append({'id':f'S{i:04d}','order':i,'path':relative,'original_name':f'strip_{i:03d}.png',
                        'width':image.width,'height':image.height,'y0':y,'y1':y+image.height,
                        'sha256':hashlib.sha256((root/relative).read_bytes()).hexdigest(),'mode':image.mode})
        y+=image.height
    reader=Chapter(root,sources)
    panels=[]
    for i,b in enumerate(bounds):
        panel={'id':f'P{i+1:03d}','bounds':b,'classification':(classifications or ['illustration']*len(bounds))[i],
               'status':'NEEDS_REVIEW','file':f'proposals/P{i+1:03d}.png','source_regions':reader.regions(b),'review':{}}
        reader.save_png(b,root/panel['file'])
        panel['sha256']=hashlib.sha256((root/panel['file']).read_bytes()).hexdigest()
        panels.append(panel)
    return {'sources':sources,'panels':panels,'joins':[],'analysis':{'gutters':[]},'excluded':[]}


def _join(index, assessment='probable_continuation', flags=None):
    return {'id':f'S{index:04d}--S{index+1:04d}','left_source':f'S{index:04d}',
            'right_source':f'S{index+1:04d}','y':index*100,'assessment':assessment,
            'flags':flags or [],'status':'UNVERIFIED','operation':'exact_row_append_no_adjustment',
            'border_rgb_mae':4,'seam_gradient_ratio':1.2,'context_rgb_mae':20}


def test_ordinary_seam_is_unreviewed_without_being_suspicious(tmp_path):
    manifest=_workspace(tmp_path,[Image.new('RGB',(100,100),'#334455') for _ in range(2)],[[0,0,100,200]])
    manifest['joins']=[_join(1)]
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['suspicious_seams']==0
    assert qa['totals']['ordinary_unreviewed_seams']==1
    assert qa['totals']['unreviewed_seams']==1
    seam=qa['seam_risks'][0]
    assert seam['kind']=='ordinary_exact_row_append_unreviewed'
    assert seam['priority']=='low'
    assert seam['used_by_regions']==['P001']
    assert seam['human_checked'] is False
    assert qa['required_checks']['joining']['status']=='REVIEW_REQUIRED'
    assert qa['required_checks']['human_verification']['status']=='REVIEW_REQUIRED'
    assert manifest['joins'][0]['status']=='UNVERIFIED'


def test_uncertain_and_actual_integrity_risk_are_separate_counts(tmp_path):
    manifest=_workspace(tmp_path,[Image.new('RGB',(100,100),'#334455') for _ in range(4)],[[0,0,100,400]])
    manifest['joins']=[_join(1),_join(2,'uncertain'),_join(3,flags=['SUSPECTED_OVERLAP_ROWS_UNCHANGED'])]
    manifest['joins'][2]['possible_overlap_rows']=12
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['ordinary_unreviewed_seams']==1
    assert qa['totals']['uncertain_seams']==1
    assert qa['totals']['suspicious_seams']==1
    assert [s['priority'] for s in qa['seam_risks']]==['low','medium','high']
    assert all(s['requires_human_inspection'] for s in qa['seam_risks'])


def test_unused_source_adjacency_is_not_an_unreviewed_export_join(tmp_path):
    manifest=_workspace(tmp_path,[Image.new('RGB',(100,100),'#334455') for _ in range(2)],[[0,0,100,90],[0,110,100,200]])
    manifest['joins']=[_join(1,'uncertain')]
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['suspicious_seams']==0
    assert qa['totals']['unreviewed_seams']==0
    assert qa['totals']['unused_source_adjacencies']==1
    assert qa['seam_risks'][0]['used_by_regions']==[]
    assert not qa['seam_risks'][0]['requires_human_inspection']
    assert qa['seam_risks'][0]['human_checked'] is False
    assert qa['required_checks']['joining']['status']=='PASS'
    assert qa['required_checks']['source_coverage']['status']=='REVIEW_REQUIRED'


def test_unused_width_risk_remains_a_preflight_warning(tmp_path):
    manifest=_workspace(tmp_path,[Image.new('RGB',(100,100),'#334455') for _ in range(2)],[[0,0,100,90],[0,110,100,200]])
    manifest['joins']=[_join(1,flags=['WIDTH_MISMATCH','ALIGNMENT_UNKNOWN'])]
    manifest['joins'][0]['blocked']=True
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['suspicious_seams']==0
    assert qa['totals']['preflight_source_adjacency_risks']==1
    assert qa['seam_risks'][0]['kind']=='preflight_source_adjacency_risk'
    assert qa['seam_risks'][0]['human_checked'] is False


def test_faint_halo_is_retained_without_being_a_significant_cutoff(tmp_path):
    image=Image.new('RGB',(200,120),'white')
    draw=ImageDraw.Draw(image)
    draw.rectangle((0,20,199,99),fill='#223344')
    draw.line((0,19,199,19),fill=(251,251,251))
    manifest=_workspace(tmp_path,[image],[[0,20,200,100]])
    qa=run_qa(tmp_path,manifest)
    region=qa['per_region'][0]
    assert region['cutoff_risk'] is False
    assert not region['linework_crossings']
    assert any(o['faint_or_edge_crossing_pixels']>0 for o in region['boundary_observations'])
    assert qa['totals']['uncovered_foreground_pixels']>0
    components=qa['per_source'][0]['uncovered_components']
    assert components
    assert all(c['risk_type']=='compression_or_faint_detail_requires_review' for c in components)
    assert all(c['priority']=='low' and c['requires_review'] for c in components)
    assert all((tmp_path/c['thumbnail']).is_file() for c in components)
    assert qa['required_checks']['source_coverage']['status']=='REVIEW_REQUIRED'


def test_full_bleed_source_edge_is_informational(tmp_path):
    manifest=_workspace(tmp_path,[Image.new('RGB',(100,100),'#223344')],[[0,0,100,100]])
    qa=run_qa(tmp_path,manifest)
    region=qa['per_region'][0]
    assert not region['cutoff_risk']
    assert region['priority']=='low'
    assert sum(o['native_source_edge_content_pixels'] for o in region['boundary_observations'])>0
    assert qa['required_checks']['pixel_fidelity']['status']=='PASS'
    assert qa['required_checks']['human_verification']['status']=='REVIEW_REQUIRED'


def test_actual_interior_ink_cut_gets_high_priority(tmp_path):
    image=Image.new('RGB',(200,140),'white')
    ImageDraw.Draw(image).rectangle((0,10,199,129),fill='#223344')
    manifest=_workspace(tmp_path,[image],[[0,50,200,100]])
    qa=run_qa(tmp_path,manifest)
    region=qa['per_region'][0]
    assert region['cutoff_risk']
    assert region['risk_type']=='suspected_illustration_cutoff'
    assert region['priority']=='high'
    assert all(o['strong_crossing_pixels']>0 for o in region['linework_crossings'])
    assert qa['review_queue'][0]['kind']=='suspected_illustration_cutoff'
    assert qa['required_checks']['pixel_fidelity']['status']=='PASS'


def test_substantial_missing_content_and_sparse_text_are_not_same_warning(tmp_path):
    image=Image.new('RGB',(320,240),'white')
    draw=ImageDraw.Draw(image)
    draw.rectangle((10,10,70,70),fill='#334455')
    draw.rectangle((180,100,280,200),fill='#902040')
    draw.text((90,20),'WAIT!',fill='black')
    manifest=_workspace(tmp_path,[image],[[5,5,76,76]])
    qa=run_qa(tmp_path,manifest)
    components=qa['per_source'][0]['uncovered_components']
    kinds={c['risk_type'] for c in components}
    assert 'meaningful_uncovered_artwork' in kinds
    assert 'unclassified_text_or_sparse_art' in kinds
    major=[c for c in components if c['risk_type']=='meaningful_uncovered_artwork']
    assert all(c['priority']=='high' for c in major)
    assert all(c['evidence']['semantic_classification_certified'] is False for c in components)
    assert qa['totals']['meaningful_uncovered_artwork_candidates']>=1
    assert qa['totals']['unclassified_uncovered_content_components']>=1


def test_retained_text_is_not_reported_as_missing_artwork(tmp_path):
    image=Image.new('RGB',(200,120),'white')
    ImageDraw.Draw(image).text((20,30),'STORY DIALOGUE',fill='black')
    manifest=_workspace(tmp_path,[image],[[0,0,200,120]],['dialogue_or_narration_only'])
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['uncovered_foreground_pixels']==0
    assert qa['totals']['retained_text_regions']==1
    assert qa['per_region'][0]['risk_type']=='retained_text_classification_review'
    assert qa['totals']['approved_artwork']==0
    assert qa['required_checks']['classification']['status']=='REVIEW_REQUIRED'


def test_coordinates_outrank_faint_detail_without_changing_pixel_fidelity(tmp_path):
    image=Image.new('RGB',(100,100),'white')
    ImageDraw.Draw(image).rectangle((20,20,80,80),fill='black')
    manifest=_workspace(tmp_path,[image],[[10,10,90,90]])
    manifest['panels'][0]['source_regions'][0]['source_bounds']=[0,0,80,80]
    qa=run_qa(tmp_path,manifest)
    assert qa['required_checks']['pixel_fidelity']['status']=='PASS'
    assert qa['required_checks']['source_coordinate_mapping']['status']=='FAIL'
    assert qa['per_region'][0]['risk_type']=='export_or_coordinate_integrity_failure'
    assert qa['review_queue'][0]['priority']=='blocker'
    assert qa['review_queue'][0]['priority_rank']==0


def test_human_join_check_can_be_observed_without_automatic_approval(tmp_path):
    manifest=_workspace(tmp_path,[Image.new('RGB',(100,100),'#334455') for _ in range(2)],[[0,0,100,200]])
    manifest['joins']=[_join(1)]
    panel=manifest['panels'][0]
    panel['status']='VERIFIED'
    panel['join_ids']=[manifest['joins'][0]['id']]
    panel['review']={'method':'explicit_human_visual_review','reviewer':'user','artwork_checked':True,'joins_checked':True}
    qa=run_qa(tmp_path,manifest)
    assert qa['seam_risks'][0]['human_checked'] is True
    assert qa['totals']['unreviewed_seams']==0
    assert qa['required_checks']['joining']['status']=='PASS'
    assert qa['production_export_enabled'] is False  # Other independent release gates remain unresolved.
