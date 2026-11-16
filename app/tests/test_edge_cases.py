"""Regression cases from an independent pixel-fidelity review."""
from pathlib import Path

import numpy as np
from PIL import Image

from manga_tool.chapter import Chapter
from manga_tool.detection import assess
from manga_tool.importer import import_chapter


def test_png_color_key_transparency_survives_lossless_export(tmp_path):
    source = tmp_path / 'transparent.png'
    image = Image.new('RGB', (280, 400), (10, 20, 30))
    image.putpixel((10, 10), (90, 80, 70))
    image.save(source, transparency=(10, 20, 30))
    output = tmp_path / 'output'
    imported = import_chapter(source, output)
    chapter = Chapter(output, imported['sources'])
    target = output / 'export.png'
    chapter.save_png([0, 0, 280, 400], target)
    with Image.open(source) as original, Image.open(target) as exported:
        np.testing.assert_array_equal(np.asarray(exported.convert('RGBA')), np.asarray(original.convert('RGBA')))


def test_high_bit_depth_png_explicitly_excluded_instead_of_saturated(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    Image.new('RGB', (300, 400), '#345678').save(source / '01_valid.png')
    high = np.full((400, 300), 30000, dtype=np.uint16)
    Image.fromarray(high).save(source / '02_highbit.png')
    imported = import_chapter(source, tmp_path / 'out', include_all=True)
    assert len(imported['sources']) == 1
    excluded = next(item for item in imported['excluded'] if item['original_name'] == '02_highbit.png')
    assert 'bit' in excluded['reason'].lower(), excluded


def test_mixed_width_missing_pixels_are_transparent_and_flagged(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    Image.new('RGB', (100, 100), '#204060').save(source / '1.png')
    Image.new('RGB', (400, 100), '#206040').save(source / '2.png')
    output = tmp_path / 'output'
    imported = import_chapter(source, output, include_all=True)
    chapter = Chapter(output, imported['sources'])
    # No actual pixels from the narrow strip exist in this rectangle.
    image = chapter.render([200, 0, 400, 200]).convert('RGBA')
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((0, 150)) == (32, 96, 64, 255)
    assessment = assess(chapter, [200, 0, 400, 200], [])
    assert any('MIXED_WIDTH' in flag or 'PADDING' in flag for flag in assessment['flags'])


def test_explicit_side_by_side_reading_direction(tmp_path):
    from PIL import ImageDraw
    from manga_tool.pipeline import process_chapter
    image=Image.new('RGB',(690,700),'white')
    d=ImageDraw.Draw(image)
    d.rectangle((30,50,300,640), fill='#172b43')
    d.rectangle((390,50,650,640), fill='#862f27')
    source=tmp_path/'page.png'
    image.save(source)
    for direction in ('ltr','rtl'):
        m=process_chapter(source,tmp_path/direction,reading_direction=direction)
        assert len(m['panels'])==2
        ordered=sorted(m['panels'],key=lambda p:p['order'])
        assert (ordered[0]['bounds'][0] < ordered[1]['bounds'][0]) == (direction=='ltr')
        assert all('SIDE_BY_SIDE_READING_ORDER_REQUIRES_REVIEW' in p['flags'] for p in ordered)


def test_failed_merge_render_keeps_existing_exports(tmp_path,monkeypatch):
    import json
    from PIL import ImageDraw
    from manga_tool.chapter import Chapter
    from manga_tool.pipeline import process_chapter,merge_panels
    import pytest
    image=Image.new('RGB',(690,700),'white')
    d=ImageDraw.Draw(image)
    d.rectangle((30,50,650,250),fill='black')
    d.rectangle((30,420,650,650),fill='black')
    source=tmp_path/'page.png'
    image.save(source)
    output=tmp_path/'out'
    m=process_chapter(source,output)
    before={p['file']:(output/p['file']).read_bytes() for p in m['panels']}
    report=(output/'reports/manifest.json').read_bytes()
    def fail(*args,**kwargs):
        raise OSError('simulated disk full while staging merged PNG')
    monkeypatch.setattr(Chapter,'save_png',fail)
    with pytest.raises(OSError):
        merge_panels(output,[p['id'] for p in m['panels']])
    assert (output/'reports/manifest.json').read_bytes()==report
    assert all((output/path).read_bytes()==raw for path,raw in before.items())


def test_jpeg_decode_is_preserved_in_png_output(tmp_path):
    import numpy as np
    from manga_tool.pipeline import process_chapter
    rng=np.random.default_rng(222)
    image=Image.fromarray(rng.integers(10,210,(300,400,3),dtype=np.uint8))
    source=tmp_path/'source.jpg'
    image.save(source,quality=93)
    output=tmp_path/'out'
    m=process_chapter(source,output)
    with Image.open(source) as decoded, Image.open(output/m['panels'][0]['file']) as actual:
        np.testing.assert_array_equal(np.asarray(decoded.convert('RGB').crop(m['panels'][0]['bounds'])),np.asarray(actual))
