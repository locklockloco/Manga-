"""Behavior and pixel-integrity tests using explicitly synthetic artwork."""
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from examples.make_demo import make_demo
from manga_tool.pipeline import process_chapter


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic-chapter")
    expected = make_demo(root / "fixtures")
    output = root / "output"
    manifest = process_chapter(root / "fixtures" / "input", output, chapter="SYN")
    with Image.open(root / "fixtures" / "synthetic_chapter.png") as image:
        original = image.convert("RGB").copy()
    return root, output, manifest, expected, original


def active_panels(manifest):
    return [panel for panel in manifest["panels"] if panel.get("status") in {"NEEDS_REVIEW", "VERIFIED"}]


def scene_coverage(panel, scene):
    px0, py0, px1, py1 = panel["bounds"]
    sx0, sy0, sx1, sy1 = scene["bounds"]
    intersection = max(0, min(px1, sx1) - max(px0, sx0)) * max(0, min(py1, sy1) - max(py0, sy0))
    return intersection / ((sx1 - sx0) * (sy1 - sy0))


def test_output_matches_original_pixels(processed):
    _, output, manifest, _, original = processed
    panels = active_panels(manifest)
    assert panels
    for panel in panels:
        bounds = panel["bounds"]
        with Image.open(output / panel["file"]) as image:
            assert image.format == "PNG"
            assert image.size == (bounds[2] - bounds[0], bounds[3] - bounds[1])
            np.testing.assert_array_equal(np.asarray(image.convert("RGB")), np.asarray(original.crop(bounds)))


def test_coordinate_validity_and_complete_artwork(processed):
    _, _, manifest, expected, _ = processed
    panels = active_panels(manifest)
    for panel in panels:
        x0, y0, x1, y1 = panel["bounds"]
        assert 0 <= x0 < x1 <= expected["width"]
        assert 0 <= y0 < y1 <= expected["height"]
        assert panel["source_regions"]
    for scene in expected["scenes"]:
        # Rectangular synthetic art has unambiguous exterior white gutters.
        assert max(scene_coverage(panel, scene) for panel in panels) == 1, scene["name"]


def test_cross_strip_reconstruction_and_natural_aspect_ratio(processed):
    _, _, manifest, expected, _ = processed
    panels = active_panels(manifest)
    for name, minimum in (("two_source_continuation", 2), ("tall_four_source_continuation", 4)):
        scene = next(scene for scene in expected["scenes"] if scene["name"] == name)
        matching = max(panels, key=lambda panel: scene_coverage(panel, scene))
        assert scene_coverage(matching, scene) == 1
        assert len(matching["source_regions"]) >= minimum
        x0, y0, x1, y1 = matching["bounds"]
        assert y1 - y0 > x1 - x0
        assert abs((x1 - x0) / (y1 - y0) - 16 / 9) > 0.1
    # The physical tile seam inside the illustration must not become a crop edge.
    scene = next(scene for scene in expected["scenes"] if scene["name"] == "two_source_continuation")
    matching = max(panels, key=lambda panel: scene_coverage(panel, scene))
    assert matching["bounds"][1] < 1280 < matching["bounds"][3]


def test_gutter_separates_independent_illustrations(processed):
    _, _, manifest, expected, _ = processed
    panels = active_panels(manifest)
    a, b = [next(scene for scene in expected["scenes"] if scene["name"] == name) for name in ("gutter_scene_one", "gutter_scene_two")]
    assert not any(scene_coverage(panel, a) > 0.9 and scene_coverage(panel, b) > 0.9 for panel in panels)


def test_small_panel_and_speech_bubble_not_discarded(processed):
    _, _, manifest, expected, _ = processed
    panels = active_panels(manifest)
    for name in ("small_panel", "single_source_with_bubble", "speech_bubble_scene"):
        scene = next(scene for scene in expected["scenes"] if scene["name"] == name)
        matching = max(panels, key=lambda panel: scene_coverage(panel, scene))
        assert scene_coverage(matching, scene) == 1
        x0, y0, x1, y1 = matching["bounds"]
        sx0, sy0, sx1, sy1 = scene["bounds"]
        assert (x1 - x0) * (y1 - y0) < (sx1 - sx0) * (sy1 - sy0) * 1.8


def test_successful_export_never_means_verified(processed):
    _, output, manifest, _, _ = processed
    for panel in active_panels(manifest):
        assert (output / panel["file"]).is_file()
        assert panel["status"] != "VERIFIED"
    assert (output / "reports" / "manifest.json").is_file()
    assert list((output / "contact_sheets").glob("*.png"))


def test_original_files_are_byte_identical(processed):
    root, output, manifest, _, _ = processed
    for source in manifest["sources"]:
        original = root / "fixtures" / "input" / source["original_name"]
        assert original.read_bytes() == (output / source["path"]).read_bytes()


def test_intentional_white_interior_is_not_an_external_gutter(tmp_path):
    from PIL import ImageDraw
    source = tmp_path / "white_interior.png"
    image = Image.new("RGB", (690, 1280), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 90, 639, 1189), outline="black", width=5)
    draw.ellipse((110, 135, 410, 285), outline="black", width=4)
    draw.text((150, 185), "Original speech remains", fill="black")
    draw.line((105, 1070, 570, 1100), fill="black", width=8)
    image.save(source)
    output = tmp_path / "out"
    manifest = process_chapter(source, output, chapter="WHITE")
    panels = active_panels(manifest)
    complete = [p for p in panels if p["bounds"][0] <= 50 and p["bounds"][1] <= 90 and p["bounds"][2] >= 640 and p["bounds"][3] >= 1190]
    assert complete, "White space enclosed by a real panel border belongs to the illustration"
    with Image.open(output / complete[0]["file"]) as result:
        np.testing.assert_array_equal(np.asarray(result.convert("RGB")), np.asarray(image.crop(complete[0]["bounds"])))


def test_suspected_overlap_never_deletes_original_rows(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    rng = np.random.default_rng(7221)
    first = rng.integers(10, 220, (500, 690, 3), dtype=np.uint8)
    second = rng.integers(10, 220, (500, 690, 3), dtype=np.uint8)
    second[:30] = first[-30:]
    Image.fromarray(first).save(source / "strip_1.png")
    Image.fromarray(second).save(source / "strip_2.png")
    output = tmp_path / "out"
    manifest = process_chapter(source, output, chapter="OVERLAP")
    assert manifest["sources"][-1]["y1"] == 1000
    panels = active_panels(manifest)
    assert len(panels) == 1
    panel = panels[0]
    assert panel["bounds"] == [0, 0, 690, 1000]
    assert "SUSPECTED_OVERLAP_ROWS_UNCHANGED" in panel["flags"]
    assert panel["status"] != "VERIFIED"
    with Image.open(output / panel["file"]) as result:
        np.testing.assert_array_equal(np.asarray(result.convert("RGB")), np.concatenate([first, second], axis=0))
