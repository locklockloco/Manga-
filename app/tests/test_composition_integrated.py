"""New composition-specific tests; real manga checks live in test_real_ch01_integrated."""
import numpy as np
from PIL import Image,ImageDraw
from manga_tool.composition import text_group_evidence


def test_missing_ocr_still_routes_sparse_colored_marks_for_review(monkeypatch):
    monkeypatch.setattr('shutil.which',lambda _:None)
    a=np.full((220,350,3),[76,76,84],dtype=np.uint8)
    a[80:110,80:280]=[210,100,50]
    evidence=text_group_evidence(a,2.0)
    assert evidence['method']=='OCR_UNAVAILABLE_GEOMETRY_ONLY'
    assert evidence['sparse_unclassified'] and not evidence['text_only']


def test_dense_art_not_declared_text_without_ocr(monkeypatch):
    monkeypatch.setattr('shutil.which',lambda _:None)
    im=Image.new('RGB',(320,400),'white');d=ImageDraw.Draw(im)
    d.rectangle((10,10,309,389),fill='#32698a');d.ellipse((40,50,280,310),fill='#cba27b')
    evidence=text_group_evidence(np.array(im),4.9)
    assert not evidence['text_only']


def test_explicit_no_vertical_splits_keeps_legacy_geometry_api(monkeypatch):
    from manga_tool import detection
    calls=[]
    def legacy(*args):
        calls.append(args)
        return ['preserved'],[],[]
    monkeypatch.setattr(detection,'legacy_propose',legacy)
    assert detection.propose_compositions('root',[],12,4,False)==(['preserved'],[],[])
    assert calls[0][4] is False
