"""Artwork priority checks; synthetic scenes are not CH01 visual acceptance."""
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from manga_tool.artwork import analyze


def scene():
    rgb=np.full((500,690,3),255,dtype=np.uint8)
    rng=np.random.default_rng(1)
    rgb[80:460,310:670]=rng.integers(30,220,(380,360,3),dtype=np.uint8)
    return rgb


def test_external_dialogue_does_not_expand_complete_artwork():
    rgb=scene()
    # Five separate equal solid glyph-like shapes, deliberately off to left.
    for x in range(15,215,40):
        rgb[160:180,x:x+14]=0
    bound,evidence=analyze(rgb)
    assert 300<=bound[0]<=310 and bound[1]<=80 and bound[2]>=670 and bound[3]>=460
    assert not evidence['speech_preservation_required']
    crop=rgb[bound[1]:bound[3],bound[0]:bound[2]]
    assert np.array_equal(rgb[80:460,310:670],crop[80-bound[1]:460-bound[1],310-bound[0]:670-bound[0]])


def test_small_detached_monochrome_weapon_is_preserved():
    rgb=scene();rgb[390:394,20:309]=0
    bound,_=analyze(rgb)
    assert bound[0]<=20 and bound[2]>=670 and bound[3]>=460


def test_multiple_characters_composition_stays_whole():
    rgb=scene();rgb[100:300,30:140]=rgb[100:300,350:460]
    bound,_=analyze(rgb)
    assert bound[0]<=30 and bound[2]>=670


def test_monochrome_unknown_drawing_not_discarded():
    rgb=np.full((300,300,3),255,dtype=np.uint8)
    im=Image.fromarray(rgb);d=ImageDraw.Draw(im)
    d.ellipse((50,30,180,160),outline='black',width=3)
    d.line((110,160,100,280),fill='black',width=3)
    bound,evidence=analyze(np.array(im))
    assert not evidence['standalone_text']


def test_unrecognized_glyph_pattern_is_review_task_not_proven_text():
    rgb=np.full((100,400,3),255,dtype=np.uint8)
    for x in range(20,340,40):rgb[30:50,x:x+10]=0
    bound,evidence=analyze(rgb)
    assert bound is None and evidence.get('review_task') and not evidence['standalone_text']


def test_explicit_text_classification_has_no_png_and_is_reversible(tmp_path):
    from manga_tool.pipeline import process_chapter,classify_panel,load_manifest
    from pathlib import Path
    source=tmp_path/'source.png';Image.fromarray(scene()).save(source)
    root=tmp_path/'out'
    m=process_chapter(source,root,include_all=True,generate_contact_sheets=False)
    p=next(p for p in m['panels'] if p.get('file'))
    original=source.read_bytes();revision=root/p['file'];revision_bytes=revision.read_bytes()
    classify_panel(root,p['id'],'dialogue_or_narration_only')
    q=next(q for q in load_manifest(root)['panels'] if q['id']==p['id'])
    assert q['metadata_only'] and q['status']=='IGNORED'
    from manga_tool.review import create_app
    client=create_app(root).test_client()
    assert client.get('/api/panels/'+p['id']).status_code==200
    assert client.get('/api/preview/'+p['id']+'/crop').status_code==200
    assert not list((root/'proposals').glob('*.png'))
    assert source.read_bytes()==original and revision.read_bytes()==revision_bytes
    classify_panel(root,p['id'],'illustration')
    q=next(q for q in load_manifest(root)['panels'] if q['id']==p['id'])
    assert not q['metadata_only'] and (root/q['output_file']).exists()
