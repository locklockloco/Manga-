# V3.3 development work — Chapter 1 input required

See CHANGELOG_V3_3.md. New source-range tasks avoid exporting unresolved giant
scenes as illustrations. Optional Tesseract supplies bounded text/bubble evidence;
OCR is not semantic artwork segmentation and real Chapter 1 acceptance is pending.

Use a NEW output folder. Existing source bytes and saved corrections are not
silently migrated. The new real-run driver is tools/run_ch01_v33.py; it requires
194 correctly ordered original Chapter 1 JPEG strips, validates dimensions and
hashes, runs the three reported windows and full chapter, and saves source maps,
contact sheets and stage/resource metrics. Visual review remains manual.

The reports, metrics and screenshots below describe HISTORICAL releases.
They are not V3.3 accuracy evidence.

# Manga Cropping V3.2 — artwork-first candidate

The current change prioritizes complete illustrated artwork over dialogue. Read
`ARTWORK_FIRST_REPORT.md` for scope, tests and remaining failures. Floating
speech no longer expands a drawing. Confident text-pattern candidates and
explicitly classified standalone text are metadata-only, reversible exclusions.
Ambiguous drawings stay available for review. Human approval is still required.

Use a NEW output folder to apply the new detector. Opening an existing V3.1
workspace preserves its saved proposals/corrections; it does not silently recrop
that workspace. Do not delete your existing output or original files.

CH01_P155 has NOT been visually validated: only the Chapter 1 audit/report was
attached, not the Chapter 1 archive or its original source strips. The giant
Chapter 1 merges are also unresolved. Existing FIX_REPORT/FINAL_VALIDATION,
benchmark numbers and example screenshots below describe V3.1, not V3.2.

## Previous V3.1 release notes and shared installation instructions

This is an incremental repair of the existing V3 application. It retains the CLI, local Flask review, original source images, natural proportions, persistent corrections and source maps. Read `FIX_REPORT.md`, `FIX_RESULTS.json` and `CHANGELOG_V3_1.md` for the measured results and unresolved cases. Historical V3 results are retained under docs/ and validation/.

The two reproduced critical bugs are fixed: completed-job reuse validates input path, chapter, processing settings and content fingerprints before returning anything; output synchronization hashes actual files and repairs damaged named copies atomically from validated immutable revisions. A changed input requires a new workspace. Damaged preserved originals or immutable revisions cause an explicit error, rather than silently rewriting history.

All automatic images remain proposals. No algorithmic score or successful PNG export grants verification, and production readiness remains blocked until required review gates are resolved.

## Install and run

Python 3.10+ (3.11/3.12 recommended). On macOS/Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python start.py
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe start.py
```

After environment activation, `python start.py` is the one-command launcher. Platform launchers are included. Native Windows/macOS execution has not been tested here. Transfer iPad archives using Files/iCloud/AirDrop/USB to the computer, then select the downloaded file. No artwork is uploaded. No model download or generative AI is used.

## CLI and recovery

```sh
python -m manga_tool process chapter.webarchive -o output --chapter CH05 --preflight
python -m manga_tool resume output
python -m manga_tool status output
python -m manga_tool pause output
python -m manga_tool cancel output
python -m manga_tool qa output
python -m manga_tool sync output
python -m manga_tool review output
```

Folders, ZIPs and single JPEG/PNG/WebP images also work. Preflight pauses after import/indexing for source-order review. Resume the SAME output folder with the unchanged original input after a crash, cancellation or pause. Changed chapter IDs, processing options, source bytes, additions or deletions are rejected before any completed return. Contact-sheet generation and preflight are operational controls, not a change to crop geometry. `sync` is a maintenance command that validates immutable revisions and repairs missing/damaged public copies without rerendering source art. It refuses to propagate a corrupt revision. Keep the original input available at its recorded path for processing/resume; saved review work can be opened directly without reprocessing.

 It retains committed images and edits. Resource checks fail explicitly. Corrupt/missing strips remain reported and block joins across missing content. Mixed widths require alignment review; they are not stretched or silently joined.

SQLite WAL/FULL state in `review/state.sqlite3` is authoritative. JSON/CSV, contact sheets and named outputs are repairable projections. Immutable revision images preserve undo history. OS writer locks release on process termination. Keep the whole workspace, including its database and original images, when transferring it.

## Review and output

- `originals/`: unchanged source bytes, including recoverable excluded/damaged resources.
- `proposals/`: unverified illustration candidates.
- `needs_review/`: uncertain text/SFX, dialogue, credits, compounds and inactive alternatives.
- `approved_artwork/`: only explicitly human-verified illustrations. This remains empty on automatic runs.
- `contact_sheets/`: overview and before/after images; refresh after edits.
- `reports/`: separate fidelity, coverage, boundaries, joins, order, classification and human-verification checks.
- `review/`: SQLite, cached profiles, immutable revisions, history and coverage/seam previews.

Adjustable thumbnails, risk/source/category filters, parent/child alternatives, original context, full neighboring strips, and native-resolution viewports support batch review. Routine unchanged source seams and pending human checks are shown separately from actual integrity, cutoff, merge and coverage risks. Drag rectangle bounds or polygon vertices; split horizontally/vertically, merge adjacent regions, classify, flag, reorder and undo/redo. Existing approvals are preserved during redetection; differences are offered as alternatives. A parent can preserve artwork while still being an incorrect combined illustration. Inspect each region before approval; there is no bulk approval shortcut.

Coverage QA includes faint details and exact uncovered source coordinates. Small JPEG halos can trigger warnings; never erase pixels to silence them. Pixel fidelity and coverage cannot establish semantic completeness. Production remains REVIEW_REQUIRED while required checks or benchmark gates are unresolved.

## Tests

```sh
python -m pip install pytest
python -m pytest -q -m "not large"
python -m pip install playwright
python -m playwright install chromium
python tests/browser_v3.py
python tools/stress_v3.py generate data200 --strips 200
python tools/stress_v3.py generate data500 --strips 500
python tools/stress_v3.py final-suite fresh200 --fixture data200 --strips 200 --source-faults --alternate-fixture data500
python tools/stress_v3.py final-suite fresh500 --fixture data500 --strips 500 --no-interrupts --alternate-fixture data200
```

Stress fixtures use actual 690×1280 strips, not tiny stand-ins. Reports disclose detector versions, forced kills, failed attempts, CPU/RSS, pixel audits and remaining errors. Resource-fault tests inject exceptions rather than deliberately filling disk or exhausting RAM.

The real benchmark contains 37 source-inspected assistant annotations (35 illustrations, title and narration). It is provisional and **not independently human-validated**. Precision/recall applies only to explicitly reviewed windows; it is not whole-chapter accuracy. Source checksums bind annotations to the actual archive. Do not turn automatic predictions into gold labels.

The current machine-readable release evidence is FIX_RESULTS.json. To regenerate it for the included Chapter 5 workspace, run `python tools/report_fixed_release.py WORKSPACE --evidence validation/v3_1 --tests validation/v3_1/release-tests.xml --stress200 validation/final_stress/200/final_suite.json --stress500 validation/final_stress/500-cold-final/final_suite.json`. The older report_v3.py helper is retained for historical V3 runs.
