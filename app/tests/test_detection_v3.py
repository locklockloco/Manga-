"""Geometric V3 regressions; synthetic labels never claim semantic verification."""
import hashlib
from pathlib import Path
import numpy as np
import pytest
from PIL import Image, ImageDraw
from manga_tool.chapter import Chapter
from manga_tool.detection import foreground,index_profiles,join_evidence,propose


def virtual_chapter(tmp_path, image, heights=None, widths=None):
    tmp_path.mkdir(parents=True,exist_ok=True)
    heights=heights or [image.height]
    sources=[]
    top=0
    for i,height in enumerate(heights,1):
        part=image.crop((0,top,image.width,top+height))
        if widths:
            part=part.crop((0,0,widths[i-1],height))
        path=tmp_path/f'{i:03d}.png'
        part.save(path)
        sources.append({'id':f'S{i:04d}','order':i,'path':path.name,'width':part.width,'height':height,
                        'y0':top,'y1':top+height,'mode':'RGB','sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        top+=height
    return sources


def covers(proposal,bounds):
    p=proposal['bounds'];b=bounds
    return p[0]<=b[0] and p[1]<=b[1] and p[2]>=b[2] and p[3]>=b[3]


@pytest.mark.parametrize('gap',[6,12])
def test_short_noisy_paper_gap_has_reversible_separate_children(tmp_path,gap):
    image=Image.new('RGB',(400,500),'white');d=ImageDraw.Draw(image)
    first=[20,20,380,200];second=[20,200+gap,380,470]
    d.rectangle((20,20,379,199),fill='#244a6b');d.rectangle((20,200+gap,379,469),fill='#713e43')
    # Low-amplitude JPEG-like noise never becomes erased output pixels.
    a=np.asarray(image).copy();rng=np.random.default_rng(5)
    a[200:200+gap]=rng.integers(246,256,a[200:200+gap].shape,dtype=np.uint8)
    image=Image.fromarray(a)
    src=virtual_chapter(tmp_path,image)
    proposals,_,_=propose(tmp_path,src)
    assert any(p.get('proposal_role')=='parent' and covers(p,first) and covers(p,second) for p in proposals)
    children=[p for p in proposals if p.get('proposal_role')=='child']
    assert any(covers(p,first) and not covers(p,second) for p in children)
    assert any(covers(p,second) and not covers(p,first) for p in children)
    assert all(not p['recommended_active'] for p in children)


def test_colored_gutter_is_alternative_never_deleted_dark_art(tmp_path):
    image=Image.new('RGB',(400,600),(220,205,235));d=ImageDraw.Draw(image)
    d.rectangle((10,20,389,269),fill='#153047');d.rectangle((10,282,389,570),fill='#572638')
    src=virtual_chapter(tmp_path,image)
    proposals,_,_=propose(tmp_path,src)
    assert any(p.get('proposal_role')=='parent' for p in proposals)
    assert len([p for p in proposals if p.get('proposal_role')=='child'])>=2
    dark=np.full((150,300,3),(20,25,35),dtype=np.uint8)
    assert foreground(dark)[0].all()


def test_three_independent_artworks_in_one_source(tmp_path):
    image=Image.new('RGB',(400,1000),'white');d=ImageDraw.Draw(image)
    expected=[[20,30,380,230],[20,350,380,570],[20,710,380,940]]
    for box,color in zip(expected,('#203a55','#68333a','#2e593f')):
        d.rectangle((box[0],box[1],box[2]-1,box[3]-1),fill=color)
    src=virtual_chapter(tmp_path,image)
    proposals,_,_=propose(tmp_path,src)
    primary=[p for p in proposals if p['proposal_role']=='primary']
    assert len(primary)==3
    assert all(sum(covers(p,b) for p in primary)==1 for b in expected)


def test_staggered_independent_islands_offer_children(tmp_path):
    image=Image.new('RGB',(600,700),'white');d=ImageDraw.Draw(image)
    # No empty whole row or whole column separates these staggered islands.
    boxes=[[20,30,300,280],[330,220,580,580],[20,600,400,680]]
    for box,color in zip(boxes,('#223c55','#6a3a35','#315840')):
        d.rectangle((box[0],box[1],box[2]-1,box[3]-1),fill=color)
    # The third island crosses the apparent vertical corridor; no single
    # whole-height column split can isolate every illustration.
    src=virtual_chapter(tmp_path,image)
    proposals,_,_=propose(tmp_path,src)
    # Pure full-height columns may independently yield clean primary panels;
    # otherwise the local island graph supplies reversible children.
    distinct=[p for p in proposals if p['proposal_role'] in {'primary','child'}]
    assert all(any(covers(p,b) for p in distinct) for b in boxes)
    assert not any(covers(p,boxes[0]) and covers(p,boxes[1]) for p in distinct)


def test_integrated_bubble_sword_and_intentional_white_inside_border(tmp_path):
    image=Image.new('RGB',(500,900),'white');d=ImageDraw.Draw(image)
    d.rectangle((40,160,459,820),outline='black',width=4)
    d.ellipse((80,70,300,240),fill='white',outline='black',width=3)
    d.polygon(((230,220),(280,270),(255,205)),fill='white',outline='black')
    d.line((130,700,450,520),fill='#454b61',width=12)
    d.text((120,120),'Keep the original bubble',fill='black')
    src=virtual_chapter(tmp_path,image)
    proposals,_,_=propose(tmp_path,src)
    assert any(covers(p,[40,70,460,821]) for p in proposals)
    # Empty white interior remains within the uncut parent/primary, not lost.
    assert any(p['proposal_role'] in {'primary','parent'} and p['bounds'][1]<500<p['bounds'][3] for p in proposals)


@pytest.mark.parametrize('count',[2,3,4])
def test_original_rows_cross_arbitrary_number_of_strips(tmp_path,count):
    h=count*240;yy=np.arange(h,dtype=np.uint16)[:,None,None]
    xx=np.arange(320,dtype=np.uint16)[None,:,None]
    image=Image.fromarray(np.concatenate([np.broadcast_to((xx+yy)%140+30,(h,320,1)),
                                         np.broadcast_to((xx*2+yy)%140+30,(h,320,1)),
                                         np.broadcast_to((xx+yy*2)%140+30,(h,320,1))],axis=2).astype(np.uint8))
    src=virtual_chapter(tmp_path,image,[240]*count)
    proposals,_,_=propose(tmp_path,src)
    combined=[p for p in proposals if covers(p,[0,0,320,h])]
    assert combined
    chapter=Chapter(tmp_path,src)
    np.testing.assert_array_equal(np.asarray(chapter.render(combined[0]['bounds'])),np.asarray(image))
    joins=join_evidence(tmp_path,src)
    assert len(joins)==count-1 and all(j['context_rows']>=64 for j in joins)
    assert all(not j['blocked'] and j['operation']=='exact_row_append_no_adjustment' for j in joins)


def test_mixed_width_join_blocked_until_alignment_review(tmp_path):
    image=Image.new('RGB',(400,600),'#203a55')
    src=virtual_chapter(tmp_path,image,[300,300],[400,300])
    joins=join_evidence(tmp_path,src)
    assert joins[0]['blocked'] and joins[0]['assessment']=='alignment_unknown'
    proposals,_,_=propose(tmp_path,src)
    assert all(not(p['bounds'][1]<300<p['bounds'][3]) for p in proposals)


def test_checksum_settings_cache_and_checkpoints(tmp_path):
    image=Image.new('RGB',(320,250),'#203a55')
    src=virtual_chapter(tmp_path,image)
    events=[]
    callback=lambda stage,current,total,**info:events.append((stage,current,total,info))
    _,first=index_profiles(tmp_path,src,checkpoint=callback,algorithm_settings={'gutter':12})
    _,second=index_profiles(tmp_path,src,checkpoint=callback,algorithm_settings={'gutter':12})
    assert not first[0]['cache_hit'] and second[0]['cache_hit']
    assert first[0]['cache_key']==second[0]['cache_key']
    _,different=index_profiles(tmp_path,src,algorithm_settings={'gutter':6})
    assert first[0]['cache_key']!=different[0]['cache_key']
    image.putpixel((100,100),(190,30,20));image.save(tmp_path/src[0]['path'])
    _,changed=index_profiles(tmp_path,src,algorithm_settings={'gutter':12})
    assert first[0]['cache_key']!=changed[0]['cache_key']
    assert changed[0]['warning']=='SOURCE_CHECKSUM_CHANGED'
    assert events[0][0]=='INDEX' and events[0][1:3]==(1,1)


def test_overlapping_borders_keep_parent_and_offer_review_children(tmp_path):
    image=Image.new('RGB',(550,900),'white');d=ImageDraw.Draw(image)
    d.rectangle((40,80,330,460),fill='#223c55',outline='black',width=3)
    d.rectangle((280,420,510,810),fill='#6a3a35',outline='black',width=3)
    d.ellipse((15,25,200,160),fill='white',outline='black',width=3)
    d.text((45,70),'Original speech',fill='black')
    src=virtual_chapter(tmp_path,image)
    proposals,_,_=propose(tmp_path,src)
    parents=[p for p in proposals if p['proposal_role']=='parent']
    children=[p for p in proposals if p['proposal_role']=='child']
    assert any(covers(p,[15,25,511,811]) for p in parents)
    assert len(children)>=2
    assert any('RECTANGULAR_CHILD_MAY_INCLUDE_ADJACENT_ART' in p.get('ambiguity_flags',[]) for p in children)
    assert all(not p['recommended_active'] for p in children)


def test_textlike_and_sparse_bw_art_stay_recoverable_uncertain(tmp_path):
    from manga_tool.detection import classify_region
    image=Image.new('RGB',(400,300),'white');d=ImageDraw.Draw(image)
    d.text((35,60),'CRACK! Keep meaningful narration',fill='black')
    # Sparse original line art is deliberately ambiguous to a geometric router.
    d.line((30,230,200,160,360,230),fill='black',width=2)
    src=virtual_chapter(tmp_path,image)
    suggestion=classify_region(Chapter(tmp_path,src),[0,0,400,300])
    assert suggestion['classification']=='uncertain'
    assert 'illustration' in suggestion['classification_candidates']
    assert 'dialogue_or_narration_only' in suggestion['classification_candidates']
    assert 'standalone_sfx' in suggestion['classification_candidates']
    assert suggestion['evidence']['never_delete_text'] is True
    assert suggestion['evidence']['classification_requires_review'] is True


def test_floating_bubble_does_not_expand_artwork_under_new_policy(tmp_path):
    image=Image.new('RGB',(690,1050),'white');d=ImageDraw.Draw(image)
    d.ellipse((410,40,590,220),fill='white',outline='black',width=3)
    d.text((460,110),'HUFF',fill='black')
    #32 blank original rows separate the floating balloon and filled artwork.
    d.rectangle((0,253,689,1000),fill='#2c4668')
    d.ellipse((180,320,410,560),fill='#c8ab85',outline='black',width=3)
    d.line((30,800,640,600),fill='#dbdad4',width=12)
    src=virtual_chapter(tmp_path,image)
    proposals,_,_=propose(tmp_path,src,min_gutter=12)
    # New explicit user policy supersedes the old mandatory-dialogue rule.
    complete=[p for p in proposals if p['proposal_role']=='primary']
    art=next(p for p in complete if covers(p,[0,253,690,1001]))
    assert art['bounds'][1]>=249
    assert not any(covers(p,[410,40,591,221]) and covers(p,[0,253,690,1001]) for p in proposals)
    assert art.get('status')!='VERIFIED'


def test_diagonal_stripes_in_single_scene_are_not_compound_borders(tmp_path):
    from examples.make_demo import make_demo
    from manga_tool.detection import _local_islands
    root=tmp_path/'fixture';make_demo(root)
    with Image.open(root/'synthetic_chapter.png') as image:
        image=image.convert('RGB')
        src=virtual_chapter(tmp_path/'chapter',image,[1280]*8)
    chapter=Chapter(tmp_path/'chapter',src)
    # Actual synthetic whole scenes contain title bars and diagonal background
    # stripes. Their horizontal bands must never become clipped child scenes.
    assert _local_islands(chapter,[40,119,650,901],4)==[]
    assert _local_islands(chapter,[15,1119,675,2311],4)==[]
