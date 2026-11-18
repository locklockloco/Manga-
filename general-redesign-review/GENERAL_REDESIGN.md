# General-purpose extractor: benchmark foundation, unreleased

This is the first-session implementation requested by the redesign kit, not a new accepted cropper. The default artwork detector remains the existing `art-dialogue-layers-research-6`. No learned model has passed the quality gates or replaced it. The original application and its workspaces remain intact.

## Working additions

- Freeze native source hashes, chapter splits and systematic review windows before inference. Intake accepts Safari WebArchive, ZIP/CBZ, folders and individual supported images. Existing safe extraction, ordering and discontinuity reports are reused.
- Label native sources in a local interface with required-artwork and ideal-crop rectangles, protected-detail boxes and separate artwork/dialogue/text/SFX/background/uncertainty polygons. Save drafts, edit, restore revisions and explicitly commit human reference review. Benchmark labels never approve application artwork.
- Freeze named gold versions with checksums, written amendments and crash-recoverable database snapshots. Edits invalidate chapter-level exhaustive review.
- Compare the unchanged detector with a pinned whole-panel/bubble ONNX model and a model-plus-existing-boundary cascade on identical frozen windows. Development-only trial commands cannot consume holdouts accidentally. Predictions, source review tasks, source hashes, window-edge risks and checkpoints are saved.
- Score only named, human-verified references. Separate merge, split, duplicate, cutoff, excess exterior, false text and review routing. Native polygon retention is distinguished from bbox proxies. Whole-chapter precision remains N/A without explicit exhaustive human labeling. Predicted semantic-mask IoU remains untested because the current candidates return rectangles, not semantic drawing masks.
- Preserve corpus identity when transporting the same original bytes using an external, checksum-validated `source_locations.json`; do not rewrite frozen boxes or labels to move storage paths.
- Queue multiple chapter jobs with one or two isolated processes, durable state, failure isolation and existing per-chapter identity/checksum-repair safeguards. Completed jobs are revalidated on restart, rather than trusted from a queue flag.
- Export versioned source/crop handoff manifests including chapter/source IDs, original hashes, native coordinates, groups, revision links and explicit review state. Evidence packaging uses independently extractable ZIPs below 5 MB, byte checksums and atomic replacement. Oversized members are refused explicitly instead of resized or dropped.

## Install and run

Python 3.10+ on a computer is required. Native Windows/macOS execution has not been tested here. The CLI and Chromium annotation interface were checked on Linux.

```sh
python -m pip install -r requirements.txt
python start.py
```

The existing chapter processing/review interface still opens with `python start.py`. Research commands are part of the same package:

```sh
python -m manga_tool research --help
python -m manga_tool batch --help
```

Create a corpus plan using either raw `input` or an existing `workspace` per chapter:

```json
{"chapters": [
  {"chapter_id":"CH01", "input":"originals/chapter1.zip", "split":"development", "style":"color_webtoon", "review_windows":25},
  {"chapter_id":"CH05", "workspace":"saved_CH05", "split":"development", "style":"color_webtoon", "review_windows":25},
  {"chapter_id":"CH02", "available":false, "split":"holdout", "reason":"Not supplied yet"}
]}
```

Paths are relative to the plan. Raw intake does not run a detector. Review exclusions/order and resolve damaged images before freezing. Use `include_all:true` only for a known chapter-only source folder. Mixed/preassembled sources must be described accurately; they do not prove raw-strip reconstruction.

```sh
python -m manga_tool research init-corpus corpus-plan.json -o research/dataset.json
python -m manga_tool research annotate research/dataset.json
python -m manga_tool research export-labels research/dataset.json -o research/drafts.json
python -m manga_tool research freeze-labels research/dataset.json --version pilot-v1 -o research/pilot-v1.json
```

Draw the complete composition, including all characters, props and intended background. Expand to adjacent original strips when needed. Dialogue may be outside the ideal rectangle. Do not label an assistant crop, automatically filled bbox or an unreviewed mask as verified gold. Freeze refuses zero approved references. A later label version requires `--amendment "reason"`.

The separate `confirm-exhaustive` command requires a named reviewer and `--human-confirm-entire-chapter-inspected`. Do not use it merely because the sampled windows are labeled. Partial references can support recall on those instances only.

```sh
python -m manga_tool research trial research/dataset.json --candidate baseline --cpu -o runs/baseline
python tools/download_research_model.py models/panel_bubble_640.onnx
python -m manga_tool research trial research/dataset.json --candidate learned --weights models/panel_bubble_640.onnx --cpu -o runs/learned
python -m manga_tool research trial research/dataset.json --candidate hybrid --weights models/panel_bubble_640.onnx --cpu -o runs/hybrid
python -m manga_tool research evaluate research/dataset.json --gold research/pilot-v1.json --pred runs/baseline/predictions.json -o runs/baseline/scorecard.json
```

Model download is separate and explicit: **10,222,514 bytes**, model-card license **Apache-2.0**, author **Gunnm92**, revision `891e15bc042279aab8718861e514805ec2dd23bd`. SHA-256: `506242d8ce2239cf572f3951dafb433ddf630b3df5ab6389b7dd0d1de474a465`. Source: <https://huggingface.co/Gunnm92/comic-panel-bubble-detection-yolo13n>. Its card describes panel and bubble **boxes**, with pseudo-labeled bubbles. Full training-corpus provenance has not been independently audited. No weights are bundled or downloaded at startup. OpenCV DNN CPU inference was tested with OpenCV 5.0.0; GPU and older DNN versions are untested. No PyTorch, remote image service, generative AI or inpainting is used.

Trial reruns validate actual source hashes before reusing checked per-window checkpoints. Changed code/settings require a new experiment folder. Trial windows are not whole chapters: edge-truncated compositions need expanded context, and overlapping predictions must not become a false illustration denominator.

## Multi-chapter queue and handoff

```json
{"chapters":[
  {"chapter_id":"CH01", "input":"/path/chapter1.zip", "options":{"reading_direction":"ltr","generate_contact_sheets":false}},
  {"chapter_id":"CH02", "input":"/path/chapter2.cbz", "options":{"reading_direction":"rtl"}}
]}
```

```sh
python -m manga_tool batch chapter_batch --plan batch-plan.json --run --workers 1
python -m manga_tool batch chapter_batch --control pause
python -m manga_tool batch chapter_batch --run
python -m manga_tool research export-contract chapter_batch/chapters/CH01 -o handoff/CH01
```

Queue inputs are canonical paths. Source byte changes, chapter/settings mismatches and corrupt exports are handled by the existing protected pipeline. Failed jobs do not prevent other chapters from finishing. Retain the entire original workspace (`originals`, `review/state.sqlite3`, immutable revisions and reports) to preserve manual corrections, history and approval on transfer; the JSON handoff alone is not the full editing workspace. Moving a benchmark's storage is supported by `source_locations.json`; this does not silently rebind an application job's original intake identity.

The first-session handoff implements source/crop **contracts**, not a deployed ChatGPT connector or the final redesigned directory/gallery UI. A local script is not a permanently installed ChatGPT feature. Queue concurrency is capped, but a fresh 10+ chapter stress acceptance run and combined resource reservation remain pending. Large native members that exceed a small evidence ZIP budget need a separate explicit transfer method; the packager refuses to sacrifice their resolution.

## Evidence and current decision

The real pilot contains 279 unchanged native strips: CH01 194, CH05 85. All 85 CH05 image bytes matched resources in the original Safari archive. Fifty systematic development windows were frozen before new inference. Baseline, learned and hybrid ran the same 50 windows; no human reference labels were fabricated. All crop-quality precision/recall is N/A.

The learned model can propose separate overlapping fish frames, but also calls RIIING lettering a panel and emits nested clock fragments. The hybrid still retains false SFX/dialogue proposals. This is evidence against making the current model a default replacement, not a verified accuracy comparison. The architecture decision is to keep the protected app, use model proposals for research/annotation, obtain reviewed composition/layer labels, and select or train the scene-support model only after a fair benchmark.

The R7 ZIP and CH02–CH04 source archives listed by the kit were not present in this environment. Those chapters remain reserved and uninspected; another style is also missing. No unseen-chapter or cross-style acceptance score exists. Native Windows/macOS, GPU, semantic mask IoU and release signoff are pending. This work is **not production-ready and not a new numbered release**.
