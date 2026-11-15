"""Detector recall fixes protect whole compositions; gold annotations stay frozen."""
import hashlib
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from manga_tool.chapter import Chapter
from manga_tool.detection import (_single_enclosing_frame,_local_islands,_outlined_balloon,
                                  join_evidence,propose,rgb_array)


def sources_for(tmp_path,image,heights=None):
    tmp_path.mkdir(parents=True,exist_ok=True)
    heights=heights or [image.height]
    sources=[];y=0
    for index,height in enumerate(heights,1):
        path=tmp_path/f'{index}.png';image.crop((0,y,image.width,y+height)).save(path)
        sources.append({'id':f'S{index:04d}','order':index,'path':path.name,'mode':'RGB',
                        'width':image.width,'height':height,'y0':y,'y1':y+height,
                        'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        y+=height
    return sources


def contains(p,box):
    b=p['bounds'];return b[0]<=box[0] and b[1]<=box[1] and b[2]>=box[2] and b[3]>=box[3]


def framed_composition(width=500,height=760):
    image=Image.new('RGB',(width,height),'white');d=ImageDraw.Draw(image)
    d.rectangle((8,8,width-9,height-9),fill='#7a6550',outline='black',width=4)
    # Orthogonal cracks/glasses/stair lines inside the original drawing are not
    # distinct scene frames, even when many artificial rectangles can be found.
    for x in range(70,width-30,75):
        d.line((x,12,x,height-12),fill='#271f20',width=3)
    for y in range(90,height-30,95):
        d.line((12,y,width-12,y),fill='#241d1b',width=3)
    d.ellipse((width//3,120,width*2//3,330),fill='#c8aa84',outline='black',width=4)
    d.polygon(((width//2,300),(width//4,650),(width*3//4,650)),fill='#273f56')
    d.line((40,630,width-35,430),fill='#c9d6db',width=11)
    return image


def test_interior_wall_and_weapon_rectangles_are_one_primary(tmp_path):
    image=framed_composition();src=sources_for(tmp_path,image)
    assert _single_enclosing_frame(np.asarray(image)) is not None
    assert _local_islands(Chapter(tmp_path,src),[0,0,image.width,image.height],4)==[]
    proposals,_,_=propose(tmp_path,src,min_gutter=12)
    full=next(p for p in proposals if contains(p,[8,8,image.width-8,image.height-8]))
    assert full['proposal_role']=='primary' and not full.get('group_key')
    assert full['recommended_classification']=='illustration'
    assert full['composition_evidence']['requires_review']
    assert full.get('status')!='VERIFIED'


def test_protruding_balloon_does_not_turn_portrait_into_three_frames(tmp_path):
    image=framed_composition(500,900);d=ImageDraw.Draw(image)
    d.ellipse((0,0,300,230),fill='white',outline='black',width=4)
    d.text((60,90),'Keep the original words',fill='black')
    src=sources_for(tmp_path,image,[450,450])
    proposals,_,_=propose(tmp_path,src,min_gutter=12)
    full=next(p for p in proposals if contains(p,[0,0,492,892]))
    assert full['proposal_role']=='primary' and not full.get('group_key')
    with Chapter(tmp_path,src).render(full['bounds']) as actual:
        np.testing.assert_array_equal(np.asarray(actual),np.asarray(image.crop(full['bounds'])))


def test_closed_floating_balloon_does_not_expand_artwork(tmp_path):
    image=Image.new('RGB',(690,1050),'white');d=ImageDraw.Draw(image)
    d.polygon(((490,20),(530,65),(600,85),(570,150),(540,205),(460,180),(420,135),(440,80)),fill='white',outline='black',width=3)
    d.text((470,110),'HUFF',fill='black')
    d.rectangle((0,238,689,1000),fill='#374b5a')
    d.ellipse((150,360,410,640),fill='#c4a581',outline='black',width=3)
    src=sources_for(tmp_path,image)
    proposals,_,_=propose(tmp_path,src,min_gutter=12)
    art=next(p for p in proposals if contains(p,[0,238,690,1001]))
    assert art['proposal_role']=='primary' and not art.get('group_key')
    assert art['bounds'][1]>=234
    assert not any(contains(p,[420,20,601,206]) and contains(p,[0,238,690,1001]) for p in proposals)


def test_plain_text_above_art_is_not_asserted_to_be_a_balloon(tmp_path):
    image=Image.new('RGB',(500,700),'white');d=ImageDraw.Draw(image)
    d.text((300,20),'CRACK CRACK',fill='black',stroke_width=1)
    d.rectangle((0,75,499,650),fill='#384e68')
    src=sources_for(tmp_path,image)
    proposals,_,_=propose(tmp_path,src,min_gutter=12)
    text_proposals=[p for p in proposals if p['bounds'][1]<50]
    assert text_proposals and all(not p.get('recommended_classification') for p in text_proposals)
    assert all(p.get('status')!='VERIFIED' for p in proposals)


def test_true_overlapping_frames_remain_unresolved(tmp_path):
    image=Image.new('RGB',(550,900),'white');d=ImageDraw.Draw(image)
    d.rectangle((40,80,330,460),fill='#223c55',outline='black',width=3)
    d.rectangle((280,420,510,810),fill='#6a3a35',outline='black',width=3)
    d.ellipse((15,25,200,160),fill='white',outline='black',width=3)
    d.text((45,70),'Original speech',fill='black')
    src=sources_for(tmp_path,image)
    assert _single_enclosing_frame(np.asarray(image)) is None
    proposals,_,_=propose(tmp_path,src,min_gutter=12)
    assert any(p['proposal_role']=='parent' and not p.get('recommended_classification') for p in proposals)
    assert len([p for p in proposals if p['proposal_role']=='child'])>=2


def test_page_border_does_not_hide_two_separate_inner_drawings(tmp_path):
    image=Image.new('RGB',(500,850),'white');d=ImageDraw.Draw(image)
    d.rectangle((8,8,491,841),outline='black',width=3)
    d.rectangle((30,40,469,360),fill='#30465d')
    d.rectangle((30,405,469,800),fill='#6c3d48')
    assert _single_enclosing_frame(np.asarray(image)) is None
    src=sources_for(tmp_path,image)
    proposals,_,_=propose(tmp_path,src,min_gutter=12)
    assert not any(p.get('composition_evidence',{}).get('type')=='single_enclosing_frame_with_attached_text' for p in proposals)


def test_join_risk_distinguishes_ordinary_rows_from_overlap(tmp_path):
    rng=np.random.default_rng(9)
    h,w=600,320
    yy=np.arange(h)[:,None];xx=np.arange(w)[None,:]
    rgb=np.stack([np.broadcast_to((xx+yy)%140+30,(h,w)),np.broadcast_to((2*xx+yy)%140+30,(h,w)),np.broadcast_to((xx+2*yy)%140+30,(h,w))],2).astype(np.uint8)
    image=Image.fromarray(rgb)
    src=sources_for(tmp_path/'normal',image,[300,300])
    join=join_evidence(tmp_path/'normal',src)[0]
    assert join['risk']=='ordinary_continuation'
    assert not join['risk_requires_review'] and join['status']=='UNVERIFIED'
    altered=rgb.copy();altered[300:325]=altered[275:300]
    src2=sources_for(tmp_path/'overlap',Image.fromarray(altered),[300,300])
    suspicious=join_evidence(tmp_path/'overlap',src2)[0]
    assert suspicious['risk']=='suspicious' and suspicious['risk_requires_review']
    assert 'SUSPECTED_OVERLAP_ROWS_UNCHANGED' in suspicious['flags']


def test_frozen_gold_file_is_unchanged():
    gold=Path(__file__).resolve().parents[1]/'benchmarks/leviathan5_gold.json'
    if gold.exists():
        assert hashlib.sha256(gold.read_bytes()).hexdigest()=='c08988c70e5e5fa629226deaa31e21fe2abca367fd90a556f9a2719940a86c52'
