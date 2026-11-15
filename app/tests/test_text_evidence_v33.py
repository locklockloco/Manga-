"""OCR evidence guards; controlled rasters do not certify manga semantics."""
import numpy as np
from manga_tool.text_evidence import inspect


def test_missing_ocr_keeps_all_analysis_ink(monkeypatch):
    from manga_tool.detection import foreground
    monkeypatch.setattr('manga_tool.text_evidence.shutil.which',lambda _:None)
    rgb=np.full((100,400,3),255,dtype=np.uint8);rgb[20:50,40:90]=0
    mask,e=inspect(rgb)
    assert np.array_equal(mask,foreground(rgb)[0])
    assert e['reason']=='OCR_NOT_INSTALLED'


def test_ocr_does_not_discard_integrated_action_art(monkeypatch):
    import subprocess
    from manga_tool.detection import foreground
    rgb=np.full((300,400,3),255,dtype=np.uint8)
    rng=np.random.default_rng(3);rgb[80:270,40:360]=rng.integers(0,220,(190,320,3),dtype=np.uint8)
    data=b'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t100\t110\t90\t40\t99\tCRASH\n'
    monkeypatch.setattr('manga_tool.text_evidence.shutil.which',lambda _:True)
    monkeypatch.setattr('manga_tool.text_evidence.subprocess.run',lambda *a,**k:subprocess.CompletedProcess(a,0,data,b''))
    mask,e=inspect(rgb)
    assert not e['standalone_text'] and not e['balloons']
    assert np.array_equal(mask,foreground(rgb)[0])
