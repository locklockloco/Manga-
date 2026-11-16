"""Independent coverage and provenance gates, including pale and irregular content."""
from pathlib import Path
import hashlib
import json

import cv2
import numpy as np
from PIL import Image, ImageDraw
import pytest

from manga_tool.qa import run_qa, compare_gold


def workspace(tmp_path, image, panels):
    (tmp_path/'originals').mkdir()
    path=tmp_path/'originals/source.png'
    image.save(path)
    source={'id':'S0001','order':1,'original_name':'strip_001.png','path':'originals/source.png',
            'width':image.width,'height':image.height,'y0':0,'y1':image.height,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mode':image.mode}
    manifest={'sources':[source],'panels':panels,'joins':[],'excluded':[],
              'analysis':{'gutters':[]},'chapter_width':image.width,'total_height':image.height}
    for index,panel in enumerate(panels):
        panel.setdefault('id',f'T_P{index+1:03d}')
        panel.setdefault('status','NEEDS_REVIEW')
        panel.setdefault('classification','illustration')
        panel.setdefault('review',{})
        panel.setdefault('file',f"proposals/{panel['id']}.png")
        dest=tmp_path/panel['file']
        dest.parent.mkdir(parents=True,exist_ok=True)
        region=image.crop(panel['bounds'])
        if panel.get('polygon'):
            region=region.convert('RGBA')
            mask=np.zeros((region.height,region.width),dtype=np.uint8)
            points=np.asarray(panel['polygon'])-panel['bounds'][:2]
            cv2.fillPoly(mask,[points.astype(np.int32)],255)
            alpha=np.asarray(region.getchannel('A'))* (mask>0)
            region.putalpha(Image.fromarray(alpha.astype(np.uint8)))
        region.save(dest)
        panel['sha256']=hashlib.sha256(dest.read_bytes()).hexdigest()
    return manifest


def test_faint_uncovered_ink_is_reported_with_exact_source_coordinates(tmp_path):
    image=Image.new('RGB',(200,160),'white')
    draw=ImageDraw.Draw(image)
    draw.rectangle((10,10,80,80),fill='black')
    draw.rectangle((150,100,158,108),fill=(251,251,251))
    manifest=workspace(tmp_path,image,[{'bounds':[5,5,90,90]}])
    qa=run_qa(tmp_path,manifest)
    assert qa['status']=='REVIEW_REQUIRED'
    assert qa['totals']['uncovered_faint_only_pixels']>=81
    source=qa['per_source'][0]
    component=next(c for c in source['uncovered_components'] if c['source_bounds'][0]<=150<c['source_bounds'][2])
    assert component['source_bounds'][0]<=150 and component['source_bounds'][2]>=159
    assert component['chapter_bounds']==component['source_bounds']
    assert (tmp_path/component['thumbnail']).is_file()
    assert qa['required_checks']['source_coverage']['status']=='REVIEW_REQUIRED'
    assert qa['required_checks']['pixel_fidelity']['status']=='PASS'
    assert manifest['panels'][0]['status']=='NEEDS_REVIEW'


def test_rejected_and_superseded_regions_do_not_cover_source_ink(tmp_path):
    image=Image.new('RGB',(100,100),'black')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100],'status':'REJECTED'},
                                      {'bounds':[0,0,100,100],'status':'SUPERSEDED'}])
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['uncovered_foreground_pixels']==10000
    assert qa['totals']['proposed_crops']==0
    assert qa['totals']['rejected_crops']==1


def test_review_only_parent_covers_pixels_without_claiming_artwork_correctness(tmp_path):
    image=Image.new('RGB',(100,100),'#442233')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100],'classification':'compound_layout',
                                      'hypothesis_group':'g1','hypothesis_role':'parent'}])
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['uncovered_foreground_pixels']==0
    assert qa['totals']['illustration_proposal_foreground_pixels']==0
    assert qa['totals']['review_only_foreground_pixels']==10000
    assert qa['totals']['unresolved_compound_or_alternative_regions']==1
    assert qa['required_checks']['panel_boundaries']['status']=='REVIEW_REQUIRED'
    assert not qa['production_export_enabled']


def test_polygon_counts_only_mask_and_preserves_pixels_inside(tmp_path):
    image=Image.new('RGB',(100,100),'#663344')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100],'polygon':[[0,0],[99,0],[0,99]]}])
    qa=run_qa(tmp_path,manifest)
    assert 4500<qa['totals']['uncovered_foreground_pixels']<5500
    assert qa['required_checks']['pixel_fidelity']['status']=='PASS'
    assert qa['per_region'][0]['pixels_compared']<10000


def test_source_corruption_is_failure_even_if_export_exists(tmp_path):
    image=Image.new('RGB',(100,100),'black')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100]}])
    (tmp_path/'originals/source.png').write_bytes(b'not an image')
    qa=run_qa(tmp_path,manifest)
    assert qa['status']=='FAILED'
    assert qa['required_checks']['source_integrity']['status']=='FAIL'
    assert qa['totals']['source_integrity_errors']==1
    assert qa['required_checks']['pixel_fidelity']['status']=='FAIL'


def test_changed_pixel_and_broken_png_are_not_success(tmp_path):
    image=Image.new('RGB',(120,100),'#112233')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,60,100]},{'bounds':[60,0,120,100]}])
    a=tmp_path/manifest['panels'][0]['file']
    with Image.open(a) as region:
        bad=region.copy()
    bad.putpixel((10,10),(255,255,0))
    bad.save(a)
    b=tmp_path/manifest['panels'][1]['file']
    b.write_bytes(b.read_bytes()[:50])
    qa=run_qa(tmp_path,manifest)
    assert qa['status']=='FAILED'
    assert qa['totals']['broken_or_missing_files']==2
    assert qa['per_region'][0]['mismatched_pixels']==1
    assert 'SOURCE_PIXEL_MISMATCH' in qa['per_region'][0]['errors']
    assert qa['per_region'][1]['errors']


def test_perceptual_duplicates_not_only_byte_hashes(tmp_path):
    image=Image.new('RGB',(200,100),'#404050')
    draw=ImageDraw.Draw(image)
    for offset in (0,100):
        draw.rectangle((offset+10,10,offset+80,80),outline='white',width=3)
        draw.line((offset+15,20,offset+70,70),fill='#aaaaaa',width=3)
    # These pictures differ by one pixel but remain perceptually equivalent.
    image.putpixel((150,50),(65,65,81))
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100]},{'bounds':[100,0,200,100]}])
    assert manifest['panels'][0]['sha256']!=manifest['panels'][1]['sha256']
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['duplicate_groups']==1
    assert not qa['near_duplicates'][0]['exact_bytes']
    assert qa['near_duplicates'][0]['status']=='REVIEW_REQUIRED'


def test_assistant_verified_is_not_human_approved_artwork(tmp_path):
    image=Image.new('RGB',(100,100),'black')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100],'status':'VERIFIED',
        'review':{'artwork_checked':True,'method':'assistant_visual_inspection','reviewer':'assistant'}}])
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['approved_artwork']==0
    assert qa['totals']['needs_review']==1
    assert qa['required_checks']['human_verification']['status']=='REVIEW_REQUIRED'
    assert not qa['production_export_enabled']


def test_nonart_never_qualifies_for_approved_artwork(tmp_path):
    image=Image.new('RGB',(100,100),'black')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100],'status':'VERIFIED',
        'classification':'title_or_credits','file':'approved_artwork/title.png',
        'review':{'artwork_checked':True,'method':'explicit_human_visual_review','reviewer':'user'}}])
    qa=run_qa(tmp_path,manifest)
    assert qa['status']=='FAILED'
    assert qa['totals']['approved_artwork']==0
    assert 'UNVERIFIED_CONTENT_IN_APPROVED_ARTWORK' in qa['per_region'][0]['errors']


def test_gold_requires_manual_labels_and_reports_actual_geometric_errors(tmp_path):
    image=Image.new('RGB',(200,120),'#334455')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,70,100]}])
    run_qa(tmp_path,manifest)
    goldfile=tmp_path/'gold.json'
    goldfile.write_text(json.dumps({'regions':[{'id':'real1','bounds':[0,0,100,100]}]}))
    assert compare_gold(tmp_path,manifest,goldfile)['status']=='NOT_EVALUATED'
    goldfile.write_text(json.dumps({'annotation_method':'manual_visual_inspection',
         'regions':[{'id':'real1','bounds':[0,0,100,100],'reviewed':True},
                    {'id':'real2','bounds':[140,0,200,100],'reviewed':True}]}))
    report=compare_gold(tmp_path,manifest,goldfile)
    assert report['status']=='EVALUATED'
    assert report['severe_cutoff_count']==1
    assert report['missed_artwork']==['real2']
    assert not report['acceptance_gates_pass']
    assert report['production_export_enabled'] is False


def test_stream_decoder_handles_pillow_png_filters_and_source_alpha(tmp_path):
    rng=np.random.default_rng(7)
    array=rng.integers(0,255,(120,160,4),dtype=np.uint8)
    image=Image.fromarray(array,'RGBA')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,160,120]}])
    qa=run_qa(tmp_path,manifest)
    assert qa['required_checks']['pixel_fidelity']['status']=='PASS'
    assert qa['per_region'][0]['pixels_compared']==160*120
    assert qa['resources']['maximum_cached_bytes_observed']<=64*1024*1024
    assert qa['resources']['full_chapter_bitmap_allocated'] is False


def test_stale_source_coordinate_map_is_independent_failed_gate(tmp_path):
    image=Image.new('RGB',(100,100),'black')
    manifest=workspace(tmp_path,image,[{'bounds':[10,10,90,90]}])
    manifest['panels'][0]['source_regions']=[{'source_id':'S0001',
        'source_bounds':[0,0,80,80],'chapter_bounds':[0,0,80,80]}]
    qa=run_qa(tmp_path,manifest)
    assert qa['required_checks']['pixel_fidelity']['status']=='PASS'
    assert qa['required_checks']['source_coordinate_mapping']['status']=='FAIL'
    assert qa['status']=='FAILED'


def test_partial_gold_does_not_label_unannotated_chapter_predictions_false_positive(tmp_path):
    image=Image.new('RGB',(200,100),'black')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100]},{'bounds':[100,0,200,100]}])
    goldfile=tmp_path/'gold.json'
    goldfile.write_text(json.dumps({'annotation_method':'manual_visual_inspection',
         'reviewer':'assistant','provisional':True,
         'regions':[{'id':'left','bounds':[0,0,100,100],'reviewed':True}]}))
    report=compare_gold(tmp_path,manifest,goldfile)
    assert report['panel_precision']==1
    assert report['chapter_predictions_not_scored']==1
    assert report['evaluation_scope']=='reviewed_region_windows_only'
    assert 'not whole-chapter' in report['precision_scope']


def test_gold_from_another_source_cannot_produce_a_score(tmp_path):
    image=Image.new('RGB',(100,100),'black')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100]}])
    goldfile=tmp_path/'gold.json'
    goldfile.write_text(json.dumps({'annotation_method':'manual_visual_inspection',
        'source_checksums':{'S0001':'wrong-original-hash'},
        'regions':[{'id':'left','bounds':[0,0,100,100],'reviewed':True}]}))
    report=compare_gold(tmp_path,manifest,goldfile)
    assert report['status']=='NOT_EVALUATED'
    assert 'checksums' in report['reason']
    assert 'panel_precision' not in report


def test_slanted_polygon_matches_actual_export_rasterization(tmp_path):
    from manga_tool.chapter import Chapter
    image=Image.new('RGB',(100,100),'#662244')
    manifest=workspace(tmp_path,image,[{'bounds':[0,0,100,100]}])
    panel=manifest['panels'][0]
    panel['polygon']=[[2,3],[87,16],[54,91],[5,66]]
    panel['source_regions']=Chapter(tmp_path,manifest['sources']).regions(panel['bounds'])
    target=tmp_path/panel['file']
    Chapter(tmp_path,manifest['sources']).save_png(panel['bounds'],target,polygon=panel['polygon'])
    panel['sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
    qa=run_qa(tmp_path,manifest)
    assert qa['required_checks']['pixel_fidelity']['status']=='PASS',qa['per_region'][0]['errors']
    with Image.open(target) as actual:
        expected_foreground=(np.asarray(actual.getchannel('A'))>0).sum()
    assert qa['totals']['illustration_proposal_foreground_pixels']==expected_foreground


def test_explicitly_chosen_child_is_not_still_an_unresolved_parent_group(tmp_path):
    image=Image.new('RGB',(100,100),'#663355')
    manifest=workspace(tmp_path,image,[{'id':'parent','bounds':[0,0,100,100],
        'status':'SUPERSEDED','active':False,'classification':'compound_layout',
        'group_key':'g1','selected_children':['child']},
        {'id':'child','bounds':[0,0,100,100],'parent_id':'parent','group_key':'g1'}])
    qa=run_qa(tmp_path,manifest)
    assert qa['totals']['illustration_proposal_foreground_pixels']==10000
    assert qa['totals']['unresolved_compound_or_alternative_regions']==0
    assert qa['required_checks']['human_verification']['status']=='REVIEW_REQUIRED'
