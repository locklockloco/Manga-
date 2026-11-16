"""Review persistence and pixel integrity; checkboxes simulate reviewer input only."""
import shutil

import numpy as np
from PIL import Image
import pytest

from examples.make_demo import make_demo
from manga_tool.pipeline import (load_manifest, merge_panels, process_chapter,
                                 reprocess_panels, split_panel, update_panel)


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    root = tmp_path_factory.mktemp("correction-seed")
    make_demo(root / "fixture")
    output = root / "out"
    process_chapter(root / "fixture" / "input", output, chapter="TEST")
    return output


@pytest.fixture
def chapter(seed, tmp_path):
    output = tmp_path / "chapter"
    shutil.copytree(seed, output)
    return output


def by_id(manifest, panel_id):
    return next(p for p in manifest["panels"] if p["id"] == panel_id)


def test_verification_requires_artwork_and_join_declarations(chapter):
    initial = load_manifest(chapter)
    single, multi = initial["panels"][:2]
    with pytest.raises(ValueError, match="visually checked"):
        update_panel(chapter, single["id"], status="VERIFIED")
    with pytest.raises(ValueError, match="joins"):
        update_panel(chapter, multi["id"], status="VERIFIED", checked=True)
    assert by_id(load_manifest(chapter), multi["id"])["status"] == "NEEDS_REVIEW"
    checked = update_panel(chapter, multi["id"], status="VERIFIED", checked=True, joins_checked=True)
    panel = by_id(checked, multi["id"])
    assert panel["status"] == "VERIFIED"
    assert (chapter / panel["reconstructed_file"]).read_bytes() == (chapter / panel["file"]).read_bytes()
    assert all(j["status"] == "VISUALLY_CHECKED" for j in checked["joins"] if j["id"] in panel["join_ids"])


def test_adjustment_invalidates_previous_verification(chapter):
    initial = load_manifest(chapter)
    single = initial["panels"][0]
    update_panel(chapter, single["id"], status="VERIFIED", checked=True)
    bounds = single["bounds"].copy()
    bounds[0] += 1
    changed = update_panel(chapter, single["id"], bounds=bounds)
    panel = by_id(changed, single["id"])
    assert panel["status"] == "NEEDS_REVIEW"
    assert panel["review"] == {}
    assert by_id(load_manifest(chapter), single["id"])["bounds"] == bounds
    with pytest.raises(ValueError):
        update_panel(chapter, single["id"], bounds=[0, 0, 100000, 10])
    assert by_id(load_manifest(chapter), single["id"])["bounds"] == bounds


def test_split_then_merge_preserves_pixels_and_stable_ids(chapter):
    initial = load_manifest(chapter)
    panel = initial["panels"][1]
    with Image.open(chapter / panel["file"]) as image:
        before = np.asarray(image.convert("RGB")).copy()
    split = split_panel(chapter, panel["id"], 1500)
    bottom = next(p for p in split["panels"] if p.get("derived_from") == panel["id"])
    assert bottom["id"].startswith(panel["id"] + "_s")
    assert all({k:by_id(split,p["id"]).get(k) for k in ("sha256","file","metadata_only","bounds","source_regions")} == {k:p.get(k) for k in ("sha256","file","metadata_only","bounds","source_regions")} for p in initial["panels"] if p["id"] != panel["id"])
    merged = merge_panels(chapter, [panel["id"], bottom["id"]])
    result = by_id(merged, panel["id"])
    assert result["bounds"] == panel["bounds"]
    assert result["status"] == "NEEDS_REVIEW"
    assert by_id(merged, bottom["id"])["status"] == "MERGED"
    with Image.open(chapter / result["file"]) as image:
        np.testing.assert_array_equal(np.asarray(image.convert("RGB")), before)
    persisted = load_manifest(chapter)
    assert [h["operation"] for h in persisted["history"]][-2:] == ["split", "merge"]
    later=[p for p in initial["panels"] if p.get("file") and p["bounds"][1]>panel["bounds"][1]]
    for p in later:
        assert by_id(persisted,p["id"])["bounds"]==p["bounds"]
        assert by_id(persisted,panel["id"])["order"]<by_id(persisted,p["id"])["order"]


def test_reprocess_only_selected_retains_saved_bounds(chapter):
    initial = load_manifest(chapter)
    kept, target = initial["panels"][:2]
    update_panel(chapter, kept["id"], status="VERIFIED", checked=True)
    changed_bounds = target["bounds"].copy()
    changed_bounds[0] += 1
    update_panel(chapter, target["id"], bounds=changed_bounds)
    unselected_path = chapter / kept["file"]
    unchanged_mtime = unselected_path.stat().st_mtime_ns
    m = reprocess_panels(chapter, [target["id"]])
    assert by_id(m, target["id"])["bounds"] == changed_bounds
    assert by_id(m, target["id"])["status"] == "NEEDS_REVIEW"
    assert by_id(m, kept["id"])["status"] == "VERIFIED"
    assert unselected_path.stat().st_mtime_ns == unchanged_mtime
    assert by_id(load_manifest(chapter), target["id"])["bounds"] == changed_bounds
