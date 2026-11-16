from hashlib import sha256
from io import BytesIO
from pathlib import Path
import plistlib
import zipfile

import pytest
from PIL import Image

from manga_tool.importer import import_chapter, natural_key


def raster(size=(690, 1280), color='red', fmt='PNG'):
    stream = BytesIO()
    Image.new('RGB', size, color).save(stream, format=fmt)
    return stream.getvalue()


def test_natural_order_and_exact_original_bytes(tmp_path):
    folder = tmp_path / 'input'
    folder.mkdir()
    for name, color in [('CH05_10.jpg', 'red'), ('CH05_2.jpg', 'blue'), ('CH05_1.jpg', 'green')]:
        (folder / name).write_bytes(raster(color=color, fmt='JPEG'))
    result = import_chapter(folder, tmp_path / 'output')
    assert [s['original_name'] for s in result['sources']] == ['CH05_1.jpg', 'CH05_2.jpg', 'CH05_10.jpg']
    for source in result['sources']:
        actual = (tmp_path / 'output' / source['path']).read_bytes()
        assert actual == (folder / source['original_name']).read_bytes()
        assert sha256(actual).hexdigest() == source['sha256']
    assert [(s['y0'], s['y1']) for s in result['sources']] == [(0, 1280), (1280, 2560), (2560, 3840)]


def test_zip_order_and_traversal_rejection(tmp_path):
    source = tmp_path / 'chapter.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('chapter/strip10.png', raster())
        archive.writestr('chapter/strip2.png', raster())
        archive.writestr('chapter/icon.png', raster((20, 20)))
    result = import_chapter(source, tmp_path / 'output')
    assert [s['original_name'] for s in result['sources']] == ['strip2.png', 'strip10.png']
    assert result['excluded'][0]['original_name'] == 'icon.png'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('../escape.png', raster())
    with pytest.raises(ValueError, match='Unsafe ZIP'):
        import_chapter(source, tmp_path / 'other')
    assert not (tmp_path / 'escape.png').exists()


def test_safari_dom_order_and_lazy_image_filter(tmp_path):
    base = 'https://example.test/episode/'
    markup = b'<html><img src="logo.png"><img class="_imageList" data-url="40.png" src="spacer.png"><img class="_imageList" data-src="39.png"></html>'
    resources = []
    for name, size in [('39.png', (690,1280)), ('logo.png', (400,200)), ('40.png',(690,1280)), ('spacer.png',(1,1))]:
        resources.append({'WebResourceURL':base+name, 'WebResourceMIMEType':'image/png', 'WebResourceData':raster(size)})
    source = tmp_path / 'episode.webarchive'
    source.write_bytes(plistlib.dumps({'WebMainResource':{'WebResourceURL':base, 'WebResourceData':markup, 'WebResourceMIMEType':'text/html'}, 'WebSubresources':resources}))
    result = import_chapter(source, tmp_path / 'output')
    assert [s['original_name'] for s in result['sources']] == ['40.png', '39.png']
    assert len(result['excluded']) == 2
    assert any('HTML image order' in w for w in result['warnings'])


def test_include_all_is_explicit_and_single_image_not_filtered(tmp_path):
    image = tmp_path / 'small.png'
    image.write_bytes(raster((20,20)))
    assert len(import_chapter(image, tmp_path / 'single')['sources']) == 1
    folder = tmp_path / 'folder'
    folder.mkdir()
    (folder / 'icon.png').write_bytes(raster((20,20)))
    with pytest.raises(ValueError, match='No chapter images'):
        import_chapter(folder, tmp_path / 'filtered')
    assert len(import_chapter(folder, tmp_path / 'all', include_all=True)['sources']) == 1


def test_no_overwrite_and_nested_output_not_reimported(tmp_path):
    folder = tmp_path / 'input'
    folder.mkdir()
    (folder / '1.png').write_bytes(raster())
    output = folder / 'results'
    result = import_chapter(folder, output)
    repeated = import_chapter(folder, output)
    assert len(repeated['sources']) == 1
    (folder / '1.png').write_bytes(raster(color='blue'))
    with pytest.raises(ValueError, match='Refusing to overwrite'):
        import_chapter(folder, output)


def test_mixed_widths_and_invalid_rasters_reported(tmp_path):
    folder = tmp_path / 'input'
    folder.mkdir()
    (folder / '1.png').write_bytes(raster((690,1000)))
    (folder / '2.png').write_bytes(raster((700,1000)))
    (folder / '3.jpg').write_bytes(b'not a raster')
    result = import_chapter(folder, tmp_path / 'output')
    assert len(result['sources']) == 2
    assert len(result['excluded']) == 1
    assert any('Mixed source widths' in w for w in result['warnings'])


def test_safari_srcset_prefers_full_resolution_over_embedded_src(tmp_path):
    base = 'https://example.test/episode/'
    markup = b'<html><img class="_imageList" src="thumbnail.png" srcset="medium.png 690w, full.png 1380w"><img class="_imageList" data-original="original.png" src="thumbnail2.png" data-srcset="candidate.png 2000w"></html>'
    resources = []
    for name, width in [('thumbnail.png', 320), ('medium.png', 690), ('full.png', 1380),
                        ('original.png', 1380), ('thumbnail2.png', 320), ('candidate.png', 2000)]:
        resources.append({'WebResourceURL': base + name, 'WebResourceMIMEType': 'image/png',
                          'WebResourceData': raster((width, 1280))})
    source = tmp_path / 'episode.webarchive'
    source.write_bytes(plistlib.dumps({'WebMainResource': {'WebResourceURL': base,
                                   'WebResourceData': markup, 'WebResourceMIMEType': 'text/html'},
                                   'WebSubresources': resources}))
    result = import_chapter(source, tmp_path / 'output')
    assert [s['original_name'] for s in result['sources']] == ['full.png', 'original.png']
    assert [s['width'] for s in result['sources']] == [1380, 1380]


def test_excluded_import_report_persisted_on_failure(tmp_path):
    import json
    folder = tmp_path / 'input'
    folder.mkdir()
    (folder / 'icon.png').write_bytes(raster((20, 20)))
    (folder / 'corrupt.jpg').write_bytes(b'bad image')
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='reports/import.json'):
        import_chapter(folder, output)
    report = json.loads((output / 'reports' / 'import.json').read_text(encoding='utf-8'))
    assert report['sources'] == []
    assert len(report['excluded']) == 2
    assert all(c['reason'] for c in report['excluded'])
    assert report['warnings']
    assert report['width'] == report['height'] == 0
