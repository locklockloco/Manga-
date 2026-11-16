# Manga Cropping V3.1 — fixes, proof and release assessment

**Assessment: suitable for supervised local review; not an unattended production-cropping release.** Both reproduced integrity bugs are fixed. The seven targeted illustration cases now produce complete independent proposals. Remaining layouts and coverage warnings stay visible; no artwork was automatically approved.

## Reproduced critical bugs

The previous delivered ZIP was independently executed in an isolated folder. It returned CH05 after a different input image/chapter was requested, and failed to repair a deliberately corrupted named PNG. The before/after JSON proof is included in validation/v3_1/reproduction/.

1. **Wrong chapter / stale input:** input identity, chapter, geometry-processing settings, source inventory/content hashes, copied originals and immutable revisions are checked before any completed-job return or migration. SHA checks detect same-size/same-timestamp replacements, additions, deletions and archive modifications. The full source plan is persisted before import. A rejected request leaves the existing job, manifest, edits and artwork unchanged. Operational preflight/contact-sheet controls may differ because they do not change crop geometry.
2. **Corrupted named exports:** synchronization verifies actual revision and destination bytes, not metadata or timestamps. A missing, truncated, damaged or replaced named export is staged from a checksum-valid immutable revision, fsynced, verified and atomically replaced. Metadata commits last; interrupted staging is recovered under the writer lock. Damaged originals or authoritative revisions fail clearly instead of silently rewriting history.

The actual CH05 campfire PNG CH05_P009 was deliberately truncated in the generated workspace and repaired by the completed-job path. All 85 original files, its immutable revision and the durable manifest remained unchanged. Ordinary idempotent re-entry preserved all 121 region hashes and saved corrections.

## Final code validation

- **172 tests passed**, zero failures/errors/skips. Four optional large pytest entry points were deselected; the representative full-size harnesses were executed separately below.
- Focused regression coverage includes 21 new job-identity tests and 13 new projection-repair tests, including genuine process kills during staging/commit and preservation of valid files.
- The final regular suite includes four actual Chapter 5 checks: both egg joins, the two-scene review choice, and intact unapproved primaries for the seven prior misses.
- Browser tests passed rectangle/polygon dragging, source/crop/neighbor inspection, native resolution, risk sorting, split/merge, classification, approval/rejection checks, undo/redo, reload persistence, exact downloads, errors, and preflight pause/resume.
- The wheel built successfully; its runner, identity, pipeline, detector, QA, review backend and JavaScript bytes match final source.
- Linux was tested. Windows and macOS native startup/execution remain **untested**; launchers and setup instructions are included.

## Real artwork changes

The detector distinguishes enclosing scene frames from internal architectural, clothing, face and weapon lines. The seven prior false compound cases now remain whole primary illustration proposals. An outlined floating balloon is kept with the neighboring drawing. True separate/overlapping layouts keep review alternatives. No source artwork was redrawn, repainted, resampled to video resolution, or forced into 16:9.

The previous red subdivisions in the comparison sheets are invalid split hypotheses; the old parent already preserved the pixels. The improvement makes the complete original illustration available as an independent proposal rather than withholding it as a compound. This is not a claim that missing pixels were invented or restored.

- Original egg strips **39–40**: CH05_P058, **690×2362**, natural proportions and original sound effects retained.
- Original egg strips **41–42**: CH05_P059, **690×2033**, kept separate from the first scene by its real white gutter.
- The separate drawings at chapter rows 9128–10968 use CH05_P013/P014 after the same documented review choice used for the prior baseline. The title/credits are separately retained. These choices are not attributed to automatic detection.

All 85 copied JPEGs were compared byte-for-byte with the actual embedded webarchive resources. All 121 saved region PNGs, including inactive alternatives/history, were compared to their exact source rectangles: **60,404,693 pixels, zero mismatches**. All 120 current named projections match their manifest hashes. Pixel identity establishes fidelity, not semantic completeness.

## Unchanged benchmark comparison

The 37 annotated regions contain 35 illustrations plus title and narration. They are the existing source-inspected assistant regression annotations, not an independently human-validated held-out dataset. The detector was developed using these cases. The gold SHA and benchmark function/helper hashes are unchanged; proof is in validation/v3_1/benchmark_frozen_proof.json. Precision is conditional on the reviewed windows, not whole-chapter precision.

| Measurement | Previous automatic | Fixed automatic | Previous with existing review choices | Fixed with the same review choices |
|---|---:|---:|---:|---:|
| Panel precision | 96.30% | 97.06% | 100.00% | 100.00% |
| Panel recall | 74.29% | 94.29% | 80.00% | 100.00% |
| Boundary precision | 91.67% | 92.65% | 95.54% | 95.71% |
| Boundary recall | 70.71% | 90.00% | 76.43% | 95.71% |

Automatic detection retains the two remaining labeled drawings in a review parent with separate child choices. After replaying only the baseline split/title decisions, all 35 labeled illustrations match independent candidates. The seven formerly unresolved cases received no manual relabeling to manufacture that improvement.

Severe geometric cutoff proxies in the reviewed set: **0**. Gold regions unrepresented anywhere: **0**. False VERIFIED claims: **0**. Boundary scores use the unchanged 4-pixel edge tolerance and IoU matching; they do not prove every object is complete on unseen chapters. Passing this scoped regression does not enable production export.

## Fresh final-detector large batches

All full-size strips were 690×1280. Both initial outputs were empty, with no old profile/crop caches. Existing synthetic source bytes were reused; OS file caches were not flushed. Every attempt used v3.1-regions-2026-11-14.1 with frozen processing hashes.

| Run | Original files checked | Review exports incl. alternatives | Exported pixels compared | Processing wall / sampled CPU | Sampled peak RSS |
|---|---:|---:|---:|---|---:|
| 200 with interruptions/resume | 200 | 134 | 208,828,688 | 600.313s / 427.590s | 148.49 MiB |
| 500 uninterrupted fresh run | 500 | 334 | 524,630,830 | 1260.652s / 1067.050s | 167.84 MiB |

Both audits found zero pixel mismatches, orphan files, staging leftovers, or VERIFIED regions. SQLite, JSON, CSV, source rectangles and actual immutable/public output hashes agree. Pixel totals include overlapping alternatives, not unique covered-source pixels. Shared-host sampled timings are not isolated speed guarantees; contact sheets were disabled for stress measurements. See FINAL_VALIDATION.md for exact scope and all attempt records.

- The 200-strip run survived SIGKILL at index 60 and 75, pause at 65, cancel at 70, a kill after export 2, a saved manual boundary edit, and a kill before export 3 committed. Source bytes and committed/corrected hashes survived.
- Post-completion tests repaired a damaged public PNG and rejected changed inputs, chapter/settings, missing input, corrupted originals and invalid revisions without changing saved work.
- Separate missing/corrupt position-90 jobs each retained 199 valid strips and blocked the missing-content join; no export crossed it. The corrupt job retained all 200 raw files, including the damaged bytes. Each compared 208,011,763 exported pixels with zero mismatches.
- Main-200 processing and its fault checks finished before an environment restart. The corrupt subjob resumed; its partial timing is disclosed rather than called cold. An earlier interrupted 500 attempt is excluded from the new successful cold 500 measurement.
- All eight processing modules remain identical to the validated build. A later review.py **display-only provenance fix** prevents legacy assistant inspection from being labeled human inspection; final API/browser/full-suite tests cover it. It does not change the detector or processing results.

## Practical QA and review

The real chapter now reports **0 actual suspicious seams**, **4 uncertain used joins**, **54 ordinary unreviewed used joins**, and **26 unused source adjacencies**. Ordinary exact row appends are not integrity alarms, and none is automatically human-verified.
- Possible interior cutoffs, coordinate errors, accidental merges, duplicates, unclassified content and substantial uncovered regions receive priority. Native source edges and faint halos remain visible as lower-priority evidence.
- Three-pane review shows full original context, the proposed crop and neighboring full source strips, with native zoom and exact original-byte downloads. Existing crop edits and review tools remain available.

## Remaining failures and limits

- **6 compound/review groups remain unresolved** outside approved artwork. They include both true overlapping frames and additional false compound hypotheses beyond the labeled regression set.
- CH05_P004: a retained standalone HERE? dialogue fragment still has a compound hypothesis; it needs text classification/context review.
- CH05_P019: staggered frames overlap; rectangular isolation can include another drawing or remove original content.
- CH05_P044: boy/thought-bubble composition still has false internal alternatives; the intact parent remains available for review.
- CH05_P073: overlapping/occluded panel composition needs manual layout decisions.
- CH05_P105: boy/stairs/SFX composition still has internal split hypotheses; complete original remains retained.
- CH05_P115: diagonal gutter and inset overlap require irregular-layout review rather than a destructive horizontal cut.
- Coverage surfaces **31,143 uncovered foreground/edge pixels** in **4,804 components: 0 strong, 38 standard, 10,077 faint-only and 21,028 edge-only. 19 components remain unclassified; 4,785 have compression/faint-detail evidence. These warnings retain exact source coordinates and thumbnails; they are not all declared unimportant noise.
- No missing substantial illustration was confirmed by the coverage detector, but this is not a guarantee of semantic completeness. Source coverage remains a review gate.
- Text/SFX classification remains weak and recoverable; not every text fragment is semantically understood. No OCR erases text.
- All 103 active regions remain unapproved. approved_artwork is empty. Human inspection and unresolved QA gates keep overall status REVIEW_REQUIRED.

## Files and use

Install requirements, then run `python start.py`. Open the full review workspace to resume corrections. Use `python -m manga_tool sync WORKSPACE` to check/repair public copies from valid immutable revisions; use `qa` for the complete QA report. Processing/resume requires the unchanged recorded input, while saved review can be opened directly.

Start with contact_sheets/recall_before_after_01.png through _04.png and repaired_examples_01.png. Source maps are in recall_source_maps.json and reports/manifest.json / panels.csv. Raw automatic and equivalent-review benchmark reports are both retained. FIX_RESULTS.json contains the machine-readable release evidence; validation/final_stress/ contains the exhaustive batch reports.
