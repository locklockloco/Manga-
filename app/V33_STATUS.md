# V3.3 development status — Chapter 1 acceptance blocked

The supplied report describes genuine real-image defects. This development work
is not a claim that those defects are fixed on real Chapter 1.

## Missing inputs

The current task attachments contain the report text, prior Chapter 1 V3.1 audit,
run summary and screenshots of account/settings pages. The only original chapter
archive available is Episode 5. The Chapter 1 archive (194 original JPEG strips),
evidence ZIP, CH01_A_MAN_ARTWORK_FIRST_REFERENCE.png, A_MAN_comparison.jpg and
text_errors_contact.jpg are NOT available in this workspace.

Therefore the three Chapter 1 window runs, full 194-strip run, original portrait
inspection, eight actual text/SFX regressions and their before/after comparisons
have NOT been executed. No Chapter 1 precision or recall is reported. No frozen
gold annotations were changed. The reference, when supplied, must not be treated
as ground truth or human approval.

## Implemented code

See CHANGELOG_V3_3.md. Main changes: source-only review tasks instead of giant
PNG exports; optional OCR and closed light-envelope analysis for text/balloons;
recoverable metadata filtering before export; geometry-limitation flags; manual
resolution and stage/resource observations. OCR results remain fallible.

The source-span review budget is 7200 pixels (two bounded analysis windows).
This threshold routes uncertain long spans to metadata, not fixed-height crops.
It does not prove a scene boundary. Ordinary tall compositions below this budget
keep natural proportions. Even a legitimate drawing longer than the budget may
need manual selection. Dark/colored low-edge bands are review markers, not
confirmed cuts or automatically selected child panels. Redetection of a task
cannot fabricate giant child alternatives.

Known remaining limitations include diagonal/stylized SFX OCR, interconnected
balloon/art outlines, monochrome drawings vs text, colored/background separation,
short scenes within one strip, overlapping layouts and semantic completeness.
No claim of universal text rejection or clean portrait extraction is made.

## Fresh real Chapter 5 smoke check (not an accuracy benchmark)

Processed ORIGINAL source images 39–42, four 690x1280 JPEGs. Both reconstructed
scenes remained 690x2362 and 690x2033. Their pixels exactly match the preserved
corresponding scene exports; 3,032,550 pixels compared with zero mismatch.
All four input/preserved source checksums match. The contact sheet was inspected
by the assistant; no human approvals were granted. Nothing about this small test
establishes Chapter 1 correctness or whole-chapter precision/recall.

Recorded processing time after request identity validation was 9.64 seconds, with concurrent unit tests on this shared Linux
machine: import .48s, index 1.17s, segmentation 5.92s, export .46s, QA 1.18s,
contact sheets .38s, setup .05s. Process-lifetime peak RSS 147,771,392 bytes;
output workspace 17,110,403 bytes at measurement. OCR child peak is excluded.
This is not a like-for-like comparison to the 194-strip audit. Physical write
amplification has not been measured. Native Windows/macOS checks and fresh
200/500-strip runs are untested for this development version.

## How to run the required real tests when inputs arrive

After installation, run tools/run_ch01_v33.py on a folder of 194 correctly ordered
ORIGINAL strips. It checks dimensions, hashes originals, stages unchanged copies,
runs 1–18, 55–65, 175–179 and full chapter, writes source coordinates and stage
metrics and never grants visual acceptance. The normal CLI accepts the archive;
use preflight to verify genuine source selection and order. Use a new output
folder to retain existing manual corrections and revision history.

Release readiness: NOT release-ready. Real Chapter 1 input and source-inspected
regression review are mandatory outstanding work. No historical V3.1 benchmark
figures are offered as evidence for these development changes.

## Executed automated checks

```text
........................................................................ [ 39%]
...................................................................ssss. [ 79%]
.....................................                                    [100%]
177 passed, 4 skipped, 4 deselected in 174.75s (0:02:54)
```

After the full run, the source-task/redetection test also passed against the final
guard against giant-child redetection. JavaScript syntax and Python compilation
passed. These checks do not replace real Chapter 1 artwork regression tests.
