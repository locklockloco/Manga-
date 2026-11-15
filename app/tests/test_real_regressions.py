"""Synthetic regressions motivated by inspection of real chapter exports.

No chapter pixels are embedded here. These tests require a review flag for
ambiguous layouts; the conservative detector may keep a composite proposal.
All rectangles and row intervals use original, unscaled source coordinates.
"""
import numpy as np
from PIL import Image, ImageDraw

from manga_tool.chapter import Chapter
from manga_tool.detection import assess, foreground, propose, rgb_array
from manga_tool.importer import import_chapter


def _chapter(tmp_path, image, *, jpeg=False):
    source = tmp_path / ('synthetic.jpg' if jpeg else 'synthetic.png')
    image.save(source, **({'quality': 85} if jpeg else {}))
    root = tmp_path / 'output'
    imported = import_chapter(source, root)
    chapter = Chapter(root, imported['sources'])
    proposals, _, _ = propose(root, imported['sources'])
    return chapter, proposals


def _dense_regions(gap, *, paper=255):
    image = Image.new('RGB', (690, 1000), (paper, paper, paper))
    drawing = ImageDraw.Draw(image)
    # Drawing coordinates are inclusive. The empty rows start at y=491.
    drawing.rectangle((40, 40, 650, 490), fill='#233d54')
    drawing.rectangle((40, 491 + gap, 650, 950), fill='#7c263c')
    return image


def test_six_pixel_nearwhite_jpeg_gutter_is_flagged_with_exact_source_coordinates(tmp_path):
    chapter, proposals = _chapter(tmp_path, _dense_regions(6, paper=253), jpeg=True)
    # JPEG ringing/Sobel responses mean a strict all-zero row test misses this
    # real empty corridor. Keep the proposal together and request visual review.
    with chapter.render([0, 0, 690, 1000]) as original:
        mask, _ = foreground(rgb_array(original))
    assert np.all(mask[491:497].sum(axis=1) > 0), 'Fixture must exercise JPEG residuals'
    merged = next(p for p in proposals if p['bounds'][1] < 491 and p['bounds'][3] > 497)
    result = assess(chapter, merged['bounds'], [])
    assert 'POSSIBLE_INTERNAL_GUTTER' in result['flags'], result
    assert result['source_regions'][0]['chapter_bounds'] == merged['bounds']
    assert result['source_regions'][0]['source_bounds'] == merged['bounds']
    assert result['source_regions'][0]['source_id'] == 'S0001'


def test_sixteen_pixel_internal_white_gutter_prompts_review_without_forced_split(tmp_path):
    chapter, proposals = _chapter(tmp_path, _dense_regions(16))
    merged = next(p for p in proposals if p['bounds'][1] < 491 and p['bounds'][3] > 507)
    result = assess(chapter, merged['bounds'], [])
    assert 'POSSIBLE_INTERNAL_GUTTER' in result['flags'], result
    assert 'AUTOMATIC_BOUNDARIES_REQUIRE_REVIEW' in result['flags']
    assert result['source_regions'][0]['source_bounds'] == merged['bounds']


def test_large_staggered_panel_components_require_layout_review(tmp_path):
    image = Image.new('RGB', (690, 1000), 'white')
    drawing = ImageDraw.Draw(image)
    # The two large components overlap in their y ranges. A single horizontal
    # whitespace cut cannot separate them, and the vertical gap is only 24px.
    drawing.rectangle((40, 40, 310, 650), fill='#233d54')
    drawing.rectangle((335, 350, 650, 950), fill='#7c263c')
    chapter, proposals = _chapter(tmp_path, image)
    parents=[p for p in proposals if p.get('proposal_role')=='parent']
    assert len(parents)==1
    assert len([p for p in proposals if p.get('proposal_role')=='child'])>=2
    result = assess(chapter, parents[0]['bounds'], [])
    assert 'COMPOUND_PANEL_LAYOUT_REQUIRES_REVIEW' in result['flags'], result
    assert result['source_regions'][0]['source_bounds'] == proposals[0]['bounds']


def test_small_integrated_speech_bubble_is_not_a_second_large_panel(tmp_path):
    image = Image.new('RGB', (690, 1000), 'white')
    drawing = ImageDraw.Draw(image)
    # The open white corner is intentional artwork space containing a speech
    # bubble. Its detached ink is much smaller than either panel above.
    drawing.polygon([(170, 70), (650, 70), (650, 950), (40, 950),
                     (40, 180), (170, 180)], fill='#233d54')
    drawing.ellipse((45, 85, 150, 150), fill='white', outline='black', width=3)
    drawing.polygon([(130, 145), (148, 173), (137, 142)], fill='white', outline='black')
    drawing.line((65, 108, 126, 108), fill='black', width=2)
    drawing.line((75, 121, 115, 121), fill='black', width=2)
    chapter, proposals = _chapter(tmp_path, image)
    assert len(proposals) == 1
    bounds = proposals[0]['bounds']
    assert bounds[0] <= 45 and bounds[1] <= 85 and bounds[2] > 150 and bounds[3] > 173
    result = assess(chapter, bounds, [])
    assert 'COMPOUND_PANEL_LAYOUT_REQUIRES_REVIEW' not in result['flags'], result
    with chapter.render(bounds) as proposed:
        actual = np.asarray(proposed)
        original = np.asarray(image.crop(bounds))
        np.testing.assert_array_equal(actual, original)
