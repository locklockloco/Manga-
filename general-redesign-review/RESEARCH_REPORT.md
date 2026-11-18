# General-purpose redesign: first-session report

**UNRELEASED. Detector selection is blocked, not accepted. No app version or production release has been created. No algorithmic crop or reference label was approved.**

## Contract and actual scope

The development kit explicitly requires dataset/annotation/export contracts, evaluator integration, an unchanged baseline benchmark and a model feasibility study before replacing or training a detector. That first-session foundation is implemented in the existing app. The complete general-purpose accuracy goal has not yet been met.

The existing importer, virtual chapter mapper, workspace-input identity checks, checksum repair, immutable revisions, review UI and correction history are retained. The default artwork detector remains `art-dialogue-layers-research-6`; its known mistakes have not been disguised as a solved redesign. The R7 ZIP listed by the kit is absent here, so this is an explicitly named current-code baseline, not a purported R7 measurement.

## Frozen sources and independence

The source/split/window dataset hash is `cacfc5ee2192aa93d4de82d2b1c8414a98d55cfee815959899ff9a4c4184f807`. It was frozen before new learned inference or threshold tuning. All 279 native source images have unchanged SHA-256 checksums: CH01 has 194 strips and CH05 has 85. All 85 CH05 bytes were independently matched to embedded resources in the original Safari WebArchive. CH05's final strip has its original shorter height; sources are not resized into uniform tiles.

Fifty systematic source windows were frozen: 30 in CH01, 20 in CH05. They are windows to annotate, not 50 verified artwork instances. All of CH01 and CH05 are development data because both chapters have been used in earlier tuning. No old V3.1 benchmark or selected framing percentage is used as current accuracy evidence.

CH02–CH04 archives and the R7 ZIP named in the kit's inventory were not supplied in this execution environment. Their expected archive hashes and the reserved holdout split are recorded. Those chapters were not inspected. A separate manga/style and its permitted local-processing material are also missing. No unseen-chapter or cross-style evaluation has occurred.

The kit contains zero approved gold annotations. No substitute labels were invented by an assistant, model, proposal bbox or screenshot. Whole-chapter precision, recall and complete-artwork accuracy therefore remain **N/A**, not zero errors or a success percentage.

## Implemented application work

- Generic corpus-plan intake freezes native hashes, source IDs/coordinates, acquisition type, chapter split and review windows before a detector runs. ZIP/CBZ share the existing safe archive reader. Damaged/missing sources block an intake freeze rather than silently shortening a chapter.
- Native-source annotation UI supports complete artwork and ideal-crop rectangles, protected-detail boxes and independent drawing/dialogue/SFX/narration/exterior/intentional-background/uncertainty polygons. It includes zoom, panning, neighboring strips and exact integer source coordinates. Source hashes are rechecked before viewing and committing. Rapid task switches are guarded against stale image responses.
- Named explicit human review and four composition/source checks are required for a verified reference. Drafts never count as gold. Reference review does not approve application artwork. Revision history, restore-as-draft, immutable gold versions, amendments and snapshot recovery are supported.
- The evaluator validates original byte hashes and source maps, distinguishes native polygon retention from bbox proxies, reports merge/split/duplicate/cutoff/excess-area/false-text/review errors and Wilson intervals for instance rates when reviewed references exist. Partial chapters cannot claim export precision. Predicted semantic-mask IoU remains untested because these feasibility candidates output boxes rather than dense drawing masks. It never grants release approval.
- Frozen trials compare the unchanged baseline, a genuine learned whole-panel/bubble model and that model followed by the existing boundary stage. They preserve original coordinates, candidate envelopes, layers/uncertainty, experiment identities and checked per-window checkpoints. They do not silently classify missing detections as absent art.
- A durable multi-chapter queue uses one or two separate CPU processes, independent chapter workspaces, deterministic order and failure isolation. Completed jobs re-enter the existing request/source validation and export repair before reuse. Small reliability tests cover changed completed sources, source isolation and an actual worker exit/restart. A fresh 10+ chapter stress acceptance run is deferred until quality is established, as the kit requests. Combined RAM/disk reservation still needs broader validation.
- Versioned source/crop handoff manifests preserve chapter/source IDs, original byte hashes, native coordinates, parent/child groups, revision links and explicit human review state. Small evidence ZIPs preserve file bytes, verify CRC/SHA, reject oversize native members rather than resize them, and replace completed files atomically. Frozen corpus identity survives storage relocation through a separately checked complete source-location map.

## Controlled candidate configuration

The learned trial uses **Gunnm92/comic-panel-bubble-detection-yolo13n**, pinned revision `891e15bc042279aab8718861e514805ec2dd23bd`. The 640 input ONNX file is **10,222,514 bytes**, SHA-256 `506242d8ce2239cf572f3951dafb433ddf630b3df5ab6389b7dd0d1de474a465`. The model card declares **Apache-2.0**, two classes (`panel`, `speech_bubble`), and pseudo-labeled bubbles. Full training-data provenance has not been independently audited. Weights were downloaded only after size/license notice; they are not bundled with the app. No PyTorch or external artwork-upload service is used. OpenCV 5.0.0 DNN performs local CPU inference. No generative editing, pixel invention or inpainting occurs.

Model card: https://huggingface.co/Gunnm92/comic-panel-bubble-detection-yolo13n

Input preparation, confidence 0.25 and class-wise NMS IoU 0.45 were fixed before inference; they were not tuned against these cases. Native coordinates are recovered from the actual rounded letterbox scales. This model proposes panel FRAME BOXES; it does not establish complete drawing support. The hybrid uses the existing unchanged artwork-boundary stage, not a newly tuned per-image rule.

Other researched options include SAM 3's promptable segmentation and ShadowB's Manga109-derived panel/balloon/text instance segmentation. Those have different licenses/runtime requirements, were not downloaded as weights or benchmarked here, and are not claimed as successful alternatives. The selected small ONNX trial establishes operational feasibility and exposes frame-only limitations; it does not complete semantic mask-model evaluation or authorize commercial model redistribution.

## Operational timings on identical development windows

The first runs below used separate processes and all 50 fixed windows. Shared Linux host timings are observations, not controlled hardware benchmarks. Peak process RSS excludes OCR children; GPU/VRAM was not measured.

| Approach | Elapsed seconds | Peak process RSS bytes | CH01 geometry proposals | CH05 geometry proposals | Failed windows | Quality precision/recall |
|---|---:|---:|---:|---:|---:|---|
| baseline | 331.00 | 164,904,960 | 171 | 100 | 0 | N/A — no reviewed gold |
| learned | 6.68 | 196,489,216 | 121 | 87 | 0 | N/A — no reviewed gold |
| hybrid | 233.65 | 259,883,008 | 121 | 87 | 0 | N/A — no reviewed gold |

Proposal counts are geometry records, not correct independent illustrations. The raw adapters do not deploy these rectangles into the ordinary artwork gallery. Catch-all source review tasks retain unrecognized source content, but routing everything to review is not automatic extraction success. Automatic-ready fraction and human correction time are unmeasured.

Final code replay read a portable copy of the same 279 originals, checked every source hash, and recomputed all scene geometry on the same 50 windows. Only exact-pixel, engine/settings-checked OCR word caches were reused; no old scene boxes, masks or reference labels were reused. Every final candidate's native output geometry and source map matched the first trial exactly. Warm replay timing is recorded separately and is not substituted for the first-run times above. Re-running completed trials revalidates sources and checkpoints before reuse. The environment restarted after the first trials completed; their checkpoint files and manifests remained intact.

## Actual visual results and failures

All 50 overview comparisons were screened by the assistant, with more detailed original-source checks for the examples below. This is not independent human annotation or an exhaustive full-chapter audit. All outputs remain unreviewed.

- **CH01_WINDOW_002, S0008–S0010:** learned frame boxes include chapter-title/dark exterior ranges and touch the inference window edges. This is not a verified full illustration.
- **CH01_WINDOW_010, S0060–S0062:** the learned model labels isolated **RIIING** as artwork and emits nested/overlapping clock fragments. The hybrid still exports the SFX as an experimental artwork rectangle. The baseline keeps exterior SFX around its clock. Neither is accepted.
- **CH01_WINDOW_011, S0067–S0069:** the baseline merges staggered fish frames; the learned model proposes separate frame boxes. Full composition retention, occlusion and reading order still need reviewed labels. This is a promising visual difference, not a measured success.
- **CH05_WINDOW_010, S0040–S0042:** egg artwork crosses original seams, but the window starts after the beginning of the first scene in S0039. An edge-touching partial crop cannot be counted as complete cross-strip reconstruction. The annotation UI lets the reviewer expand the original range.
- **CH05_WINDOW_020, S0083–S0085:** learned/hybrid frame the portrait more closely but also propose the lower dialogue-only section as a panel. The hybrid does not solve standalone-text rejection, despite a tighter portrait.

`index.html` contains all 50 source-linked comparisons. It shows original context, unchanged baseline, learned frames and hybrid rectangles on a common source-pixel scale. JPEG previews are not native crop proof. The package separately contains all **279 unchanged source JPEGs**, **24 systematic native crop examples**, and **20 additional native failure-review PNGs**. Each case JSON identifies every proposal, chapter-global and per-source bounds, original source hashes, flags and remaining problems. Native PNG values were compared to the corresponding source render; that proves source fidelity only, not complete composition or good segmentation.

## Architecture decision

**Do not adopt the current frame detector or hybrid as the default extractor.** They are fast/feasible proposal or annotation helpers, but the observed standalone-text, nested-frame, window-cutoff and context failures violate the user's requirements. No training, threshold fitting or detector replacement is justified before the approved benchmark exists.

Retain the protected pipeline and independent source-mapped layers. Obtain 30–50 carefully reviewed pilot artwork instances and standalone negatives, then compare whole-scene/mask candidates on the same frozen development definitions. Expand toward at least 200 reviewed instances across multiple chapters/styles; freeze the selected candidate and inspect the previously unseen holdouts once, documenting when a holdout becomes consumed. Only then implement a selected composition-support model/cascade as the default. No winner is declared from these operational runs.

## Current acceptance status

- Original source identity and pixel-preserving extraction: checked on the supplied development evidence, with regression tests; no source bytes modified.
- Human-reviewed pilot labels: **0**; quantitative quality scores **N/A**.
- Exhaustively labeled unseen chapters: **0**.
- Held-out distinct style: **not supplied/tested**.
- Semantic predicted mask IoU: **untested** for these rectangle candidates.
- Full 10+ chapter stress acceptance: **deferred**; small queue reliability tests are not a replacement.
- Native Windows/macOS and GPU startup/inference: **untested**.
- Production readiness: **BLOCKED**. Supervised review remains necessary; the app is not presented as the completed general-purpose extractor.

The next dependency is real human annotation in the supplied native-source UI, plus the missing R7/CH02–CH04 archives and a distinct permitted style. A path or source archive is needed; the kit's JSON inventory is not the image data itself.

## Repository validation

223 passed, 11 skipped, 4 deselected in 196.81s (0:03:16)

Final focused source/annotation/batch/package checks: 24 passed in 3.41s

The 11 skipped cases require other unavailable external fixtures; four large stress entries were deselected. Passing tests establishes engineering behavior, not correct manga crops. The kit starter evaluator is preserved unchanged and its ten synthetic tests passed.

## Final source and UI checks

The final annotation browser test exercised native drag coordinates and rapid CH01/CH05 task changes. The displayed CH05 canvas pixels matched the exact original source rows after storage relocation; no reference labels were saved or approved. All fifty private comparison images loaded. The existing CH01 workspace also exported a 213-record source/crop contract with zero approved artwork, preserving source hashes and immutable history; this is an export-contract check, not a new crop-quality score.

## Supplementary complete egg-join regression

A separately frozen supplementary window covers the actual native CH05 S0039–S0042. It does not replace or change any of the 50 pilot windows or labels. The unchanged baseline returns two source-exact rectangles: `[0,48810,690,51182]` from S0039/S0040 and `[0,51514,690,53551]` from S0041/S0042. Learned/hybrid also return two cross-strip boxes but move the first top boundary downward and retain SFX; their completeness is unreviewed. Native PNGs, the full original-context comparison, source maps and all predictions are in `supplementary_join/`. This demonstrates the tested source joins and preserved pixels, not a human-approved scene-quality score.
