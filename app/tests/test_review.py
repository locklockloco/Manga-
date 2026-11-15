"""API safety and review behavior; optional browser smoke was also exercised manually."""
import io

import pytest
from PIL import Image, ImageDraw

from manga_tool.pipeline import process_chapter, load_manifest
from manga_tool.review import create_app


@pytest.fixture
def review_workspace(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    # One continuous illustration crossing a real source boundary.
    for n in range(2):
        image = Image.new('RGB', (320, 350), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 0 if n else 30, 299, 319 if n else 349), fill='#5a76b0')
        draw.line((40, 0, 280, 349), fill='black', width=5)
        image.save(source / f'strip_{n+1}.png')
    out = tmp_path / 'output'
    m = process_chapter(source, out, chapter='UI', include_all=True)
    return out, m


def test_review_assets_and_bounded_preview(review_workspace):
    out, m = review_workspace
    client = create_app(out).test_client()
    assert client.get('/').status_code == 200
    assert client.get('/static/app.js').status_code == 200
    panel = m['panels'][0]
    assert client.get('/api/manifest').json['chapter'] == 'UI'
    response = client.get('/api/preview/' + panel['id'] + '/context')
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.data)) as preview:
        assert max(preview.size) <= 1600
        assert preview.width * preview.height <= 4_000_000
    assert client.get('/api/download/' + panel['id']).status_code == 200


def test_loopback_and_origin_protection():
    client = create_app().test_client()
    assert client.get('/api/state', base_url='http://evil.example').status_code == 403
    assert client.post('/api/open', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403


def test_explicit_artwork_and_join_checks(review_workspace):
    out, m = review_workspace
    client = create_app(out).test_client()
    p = next(p for p in m['panels'] if len(p['source_regions']) > 1)
    url = '/api/panels/' + p['id']
    assert client.post(url, json={'status': 'VERIFIED'}).status_code == 400
    assert client.post(url, json={'status': 'VERIFIED', 'checked': True}).status_code == 400
    assert load_manifest(out)['panels'][0]['status'] == 'NEEDS_REVIEW'
    assert client.post(url, json={'status': 'VERIFIED', 'checked': True, 'joins_checked': True}).status_code == 200
    assert client.get('/api/manifest').json['panels'][0]['status'] == 'VERIFIED'
    b = p['bounds']
    assert client.post(url, json={'bounds': b}).status_code == 200
    assert client.get('/api/manifest').json['panels'][0]['status'] == 'NEEDS_REVIEW'


def test_invalid_crop_and_empty_reprocess(review_workspace):
    out, m = review_workspace
    client = create_app(out).test_client()
    assert client.post('/api/panels/' + m['panels'][0]['id'], json={'bounds': [-1, 0, 100, 100]}).status_code == 400
    assert client.post('/api/reprocess', json={'ids': []}).status_code == 400


def test_classification_preserves_pixels_and_downloads(review_workspace):
    out, m = review_workspace
    client = create_app(out).test_client()
    panel = m['panels'][0]
    original_hash = panel['sha256']
    for classification, folder in [('text_only', 'dialogue_or_narration_only'), ('title_card', 'title_or_credits'),
                                   ('compound_layout', 'compound_layout'), ('illustration', None)]:
        response = client.post('/api/classify', json={'id': panel['id'], 'classification': classification,
                                                     'note': 'Classification API test'})
        assert response.status_code == 200, response.json
        result = next(p for p in response.json['panels'] if p['id'] == panel['id'])
        assert result['classification'] == {'text_only':'dialogue_or_narration_only','title_card':'title_or_credits'}.get(classification,classification)
        ignored=classification=='text_only'
        assert result['status'] == ('IGNORED' if ignored else 'NEEDS_REVIEW')
        assert result['sha256'] == original_hash
        assert result['file'].startswith('review/revisions/')
        assert (out/result['file']).exists()  # immutable history survives omission
        if ignored:
            assert result['metadata_only'] and 'output_file' not in result
            assert not (out/'proposals'/f"{panel['id']}.png").exists()
        elif folder:
            assert result['output_file'].startswith('needs_review/' + folder + '/')
        else:
            assert result['output_file'].startswith('proposals/')
        assert client.get('/api/download/' + panel['id']).status_code == 200
    assert client.post('/api/classify', json={'id': panel['id'], 'classification': 'unknown'}).status_code == 400


def test_contact_sheet_refresh_clears_stale_flag(review_workspace):
    out, m = review_workspace
    client = create_app(out).test_client()
    panel = m['panels'][0]
    response = client.post('/api/panels/' + panel['id'], json={'bounds': panel['bounds']})
    assert response.status_code == 200
    assert response.json['contact_sheets_stale'] is True
    refreshed = client.post('/api/sheets', json={})
    assert refreshed.status_code == 200
    assert refreshed.json['contact_sheets_stale'] is False
    assert load_manifest(out)['contact_sheets_stale'] is False
