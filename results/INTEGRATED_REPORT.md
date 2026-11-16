# Chapter 1 integrated improvement — 2026-11-16

**Readiness: NOT READY for unattended extraction.** This is a working, substantially improved supervised-review build, not a claim that all crops are correct. Automatic runs approve nothing. Two confirmed overlapping/compound layouts still merge, and additional layouts require review. The actual Chapter 1 sources are now present and tested; earlier release documents saying otherwise are historical.

## What changed in place

The supplied V3.3 application remains the base. Its importers, immutable revisions, checksum repair, workspace identity, source maps, SQLite checkpoints, review corrections and undo/redo remain. New chapter jobs use a streaming 2D composition graph rather than requiring white horizontal rows. Exterior colors are learned from spatially uniform chapter rows; structures are connected through shared pixels in overlapping analysis windows. Source-file seams do not define panel cuts. Missing content and mismatched widths block automatic stitching. Native output pixels are copied from the originals; downsampling is analysis only.

Small islands contained inside a composition do not become word/object alternatives. Detached colored fragments require contextual evidence before attachment. Sparse lettering and ambiguous neutral ink become recoverable source-location tasks rather than artwork PNGs. Optional Tesseract adds bounded typography evidence; without it, conservative geometry checks still route sparse material to review. This is not semantic recognition of all characters, objects or words. Monochrome/flat chapters may use the retained legacy detector, whose limitations remain.

Portrait trimming uses the printed frame, balloon-hole evidence and the complete painted-component envelope. It does not select a face or person box. Floating speech does not enlarge the crop. A rectangular crop cannot remove a balloon overlapping original artwork without losing pixels; that geometry limitation remains visible and manually adjustable.

PNG export now uses reversible Sub filtering with a single-row buffer. The change improves lossless storage; it does not change native pixels. Output synchronization hashes the current public file and repairs it atomically from a validated immutable revision. Changed input identity, chapter, settings and source fingerprints are checked before completed-job reuse; those protections were retained and regression tested.

The local review UI retains source/crop/neighbors, native views, boundary editing, split/merge, reversible classification and history. A new **Ignore selected text** action applies reversible classifications individually. It is sequential, not one atomic multi-item transaction: after interruption inspect which selections completed. No bulk approval was added.

## Real source verification and before/after

All six supplied ZIPs were extracted with internal paths preserved. All **194 ordered S0001–S0194 JPEGs** were present, each 690×1280, and matched the supplied byte count and SHA-256 before and after testing. Reuploaded parts matched the earlier contents; no source was replaced or recompressed.

Baseline is the actual supplied V3.3 package run on these same sources, not a historical V3.1 benchmark.

| Real window | Supplied V3.3 | Integrated result | Interpretation |
|---|---:|---:|---|
| S0001–S0018 | 1 giant source task, 0 artwork PNGs | 19 records: 16 artwork proposals, 3 source tasks | Dark scenes now separate; the last scene reaches outside the 18-strip test window. Sixteen is not a verified-complete count. |
| S0055–S0065 | 40 records, 31 PNGs | 15 records, 10 artwork PNGs, 5 metadata records | In the frozen nine documented negatives, exported standalone negatives fell from **7 to 0**, including narration, fragments and diagonal RIIING. |
| S0175–S0179 portrait | 488×721, bounds [68,1951,556,2672] | **395×665**, [156,2005,551,2670] | 25.34% less rectangular area. Original hair/head, eyes, glasses, ear/neck and printed context were visually compared. Balloon still overlaps inside the printed frame; no clean-removal claim. |
| Full S0001–S0194 | 176 records, 162 PNGs | **288 records: 213 artwork proposals, 75 source-review records** | More outputs do not prove higher panel accuracy. None is human approved. |

Full run compared **118,891,504 exported crop pixels** to the original raster with zero mismatches; all 213 public and immutable crop checksums matched. Deliberately corrupting one real public export caused synchronization to repair exactly that file. Wrong-input and wrong-chapter requests against that completed job were rejected without changing its manifest. Approved artwork remains empty.

The four supplied Chapter 5 holdout strips were tested after tuning: 39–40 and 41–42 remained **two separate cross-strip egg scenes**, at 690×2376 and 690×2044. Both preserved the original protected illustration ranges. This two-scene holdout is not a whole-chapter score.

All 213 final thumbnails were inspected across seven overview pages; selected problematic crops and their original strips were also inspected at native resolution. Thumbnail review is not a native-resolution human audit of every illustration.

## Remaining failures and review obligations

Confirmed automatic compound merges include **S0067–S0068 fish displays** and **S0144–S0145 children/weapon scenes**. These remain NEEDS_REVIEW, with source maps. The separate `assisted_corrections` examples demonstrate reversible assistant-applied splits, are unapproved, and are **not automatic successes**. Overlapping fish frames impose a rectangular geometry limitation: the upper crop includes a strip of the lower overlaid frame. Occluded original pixels are not invented.

Contact inspection also identifies compound layouts needing closer source inspection at S0022–S0023, S0070–S0071 and S0104–S0106. Closely related views may be combined; neutral gaps, dialogue and some outside whitespace remain. Sparse genuine drawings can be routed to source review rather than exported. The 75 source tasks are not 75 proven missing illustrations or 75 proven text regions. Restore/classify them individually. The isolated pink M at S0189 is metadata only, while the lettering inside the underwater scene remains original artwork.

Ordinary unchanged source seams retain informational join records and generic review flags; their presence does not establish a bad join. Risk routing and source continuity are still heuristic and cannot reliably rank all semantic cutoffs. Broad review flags remain noisy. No claim is made that every important body part or background was recognized automatically.

The frozen reference contains coarse source-content windows, not independent exact panel segmentation masks. It was not altered to improve scores. **Whole-chapter illustration recall/precision are NOT ESTABLISHED.** The nine-case negative result and selected source-inspected geometric results are reported separately; pixel fidelity does not prove illustration completeness.

## Timing and resource measurements

Linux, shared host, warm/cold cache and concurrent tests were not controlled; these are observed timings, not a speedup claim.

| Final run stage, full 194 | Seconds |
|---|---:|
| Setup/import | 5.40 |
| Index | 69.67 |
| Segment/classify | 143.40 |
| Export | 54.56 |
| Pixel/source QA | 57.49 |
| Contact sheets | 28.80 |
| Total measured processing | **359.32** |

Baseline full processing was 394.21 s. Final three windows took 28.50, 19.48 and 12.54 s. External wall time for all four final runs was 420.78 s. Sampled process-tree peak RSS was 153,088,000 bytes (~146 MiB, sampling may miss spikes); OS single-child high-water RSS was 175,235,072 bytes (~167 MiB). Observed parent write counter reached 1,632,714,752 bytes across all four runs, excluding independently attributed OCR I/O. Full workspace bytes at measurement: 522,889,639; native artwork PNG bytes: 149,863,540.

A progress-only CLASSIFY checkpoint hook was added after that timed full run, then exercised on real portrait pause/cancel/resume. Full 194-source discovery was replayed on the delivered detector to compare all saved geometry/metadata. All 288 bounds/metadata records matched on replay (68.74 s). See `final_geometry_replay.json`; measured export timing is not falsely attributed to the later checkpoint hook.

## Tests and recovery evidence

The main repository run passed **184 tests**, with 4 skipped and 4 large-test entry points deselected because the dedicated batch runner was used. After the classification checkpoint addition, **7 actual-source tests passed**, including pause/cancel/resume during classification. The delivered focused run passed **10 tests** (the 7 actual-source checks plus 3 composition/compatibility checks), preserving the no-vertical-splits switch. Historical detector API tests exercise the retained legacy API; they are not evidence of new detector segmentation accuracy.

Actual Chromium tests on a copy of the real portrait workspace passed boundary edits, reversible ignore/restore, reload, visible split/merge, undo/redo and bulk ignore, with no JavaScript errors. That real test edited boundary fields; it does not establish native drag behavior on Windows/macOS.

Separate full-resolution **200- and 500-strip synthetic** runs verified source hashes and pixel integrity. The 200-strip run injected process exit 86, saved a manual correction, resumed and retained the correction. Both batches repaired deliberately damaged public PNGs. Their recorded elapsed totals include repeated QA/recovery (315.00 s and 794.26 s) and are not real manga accuracy evidence. Final detector discovery was replayed over both batches (24.50/60.88 s), with all 200/500 proposals matching prior geometry except the deliberately preserved manual bound. See `final_batch_discovery.json`. Final encoder verification across all 700 native crops compared **525,841,200 pixels**, zero mismatches. Encoder-only elapsed times were 12.15/27.85 s; these are not end-to-end batch timings. The synthetic compression ratio must not be applied to real artwork.

Repository safety regressions cover different input paths, changed chapter/settings/source bytes, missing/corrupt/truncated PNGs, interrupted projection writes, damaged immutable revisions, crash restart, missing/corrupt input images, disk/resource failures and preserved edits. Actual native Windows and macOS startup are **UNTESTED**; Linux CLI and local Chromium were tested.

To conserve the constrained test disk after validation, replaceable public projections from historical baseline/intermediate and synthetic batch workspaces were removed with storage notes; original sources and immutable revisions were preserved. The delivered final Chapter 1 gallery and all final source maps remain intact. Downloadable gallery/report packages are review evidence, not a movable live SQLite workspace.
