"""Structural tests; these are not real Chapter 1 visual acceptance."""
from pathlib import Path
from PIL import Image,ImageDraw
from manga_tool.pipeline import process_chapter,load_manifest,update_panel
import pytest


def test_dark_18_strip_span_is_source_task_without_artwork_png(tmp_path):
    src=tmp_path/'source';src.mkdir()
    for i in range(18):
        im=Image.new('RGB',(690,1280),'#253747')
        d=ImageDraw.Draw(im);d.rectangle((60,70,630,1150),outline='#466a80',width=9)
        im.save(src/f'{i:03}.png')
    out=tmp_path/'out'
    m=process_chapter(src,out,generate_contact_sheets=False,include_all=True)
    tasks=[p for p in m['panels'] if p.get('review_task')]
    assert len(tasks)==1
    p=tasks[0]
    assert p['bounds']==[0,0,690,23040]
    assert p['status']=='SOURCE_REVIEW' and p['metadata_only']
    assert len(p['source_regions'])==18
    assert not list((out/'review/revisions').glob('*.png'))
    assert not list((out/'proposals').glob('*.png'))
    assert not list((out/'needs_review').rglob('*.png'))
    assert m['qa']['required_checks']['source_review_tasks']['status']=='REVIEW_REQUIRED'
    assert m['qa']['required_checks']['source_review_tasks']['count']==1
    from manga_tool.pipeline import redetect_panels
    staged=redetect_panels(out,[p['id']])
    assert len(staged['panels'])==1 and not staged['panels'][0].get('children')
    assert not list((out/'review/revisions').glob('*.png'))
    with pytest.raises(ValueError):update_panel(out,p['id'],status='VERIFIED',checked=True,joins_checked=True)
    with pytest.raises(ValueError):update_panel(out,p['id'],bounds=p['bounds'])
    # Human-adjusted geometry exports only the selected original rows and keeps
    # the unresolved range's original bounds in the saved correction record.
    q=update_panel(out,p['id'],bounds=[0,30,690,1200])
    p=next(t for t in q['panels'] if t['id']==p['id'])
    assert not p['metadata_only'] and not p['review_task']
    assert p['source_review_original_bounds']==[0,0,690,23040]
    assert (out/p['file']).exists()
    assert load_manifest(out)['production_status']=='REVIEW_REQUIRED'
