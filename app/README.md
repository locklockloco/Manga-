# Manga Cropping — integrated Chapter 1 build

**Working supervised-review application; NOT ready for unattended extraction.**
The actual 194 original Chapter 1 strips were tested. Read [the measured report](INTEGRATED_REPORT.md) and [changelog](CHANGELOG_INTEGRATED.md). Earlier V3.1–V3.3 reports are historical, not current accuracy evidence.

## Start

Install Python 3.10 or newer on a computer. Extract the application ZIP first.

macOS/Linux:
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

After activating the environment, **`python start.py`** opens the local interface. Platform launchers are included; native Windows/macOS execution is untested. Transfer files from iPad Files using iCloud Drive, AirDrop or USB, then select them on the computer. ZIP files must be downloaded and extracted; the ChatGPT archive preview is not the running application.

Optional typography helper: install Tesseract with English data (`brew install tesseract` on macOS, `apt install tesseract-ocr tesseract-ocr-eng` on Debian/Ubuntu, or a Windows Tesseract distribution on PATH). Tesseract is Apache-2.0; its English traineddata must be installed separately. No model weights or external services are downloaded by this app. OCR is optional and fallible; without it, geometry still routes sparse ambiguous material for review. No generative AI, inpainting or reconstructed missing pixels.

## Process and review

```sh
python -m manga_tool process chapter.webarchive -o CH01_integrated --chapter CH01
python -m manga_tool review CH01_integrated
python -m manga_tool pause CH01_integrated
python -m manga_tool cancel CH01_integrated
python -m manga_tool resume CH01_integrated
python -m manga_tool status CH01_integrated
python -m manga_tool qa CH01_integrated
python -m manga_tool sync CH01_integrated
```

Folders, ZIP archives and individual JPEG/PNG/WebP files also work. Choose the actual original-strip folder, not a folder containing proof thumbnails and old cropped outputs. Reading order and source IDs remain traceable. `--include-all` explicitly bypasses image relevance filtering. `--preflight` pauses for source-order inspection; `--no-sheets` defers sheet generation. `--no-vertical-splits` retains the legacy corridor detector to keep side-by-side sections together. The new graph learns exterior colors; `--min-gutter` chiefly affects retained corridor/fallback detection, not a guaranteed graph split height.

**Use a new output folder for the new detector.** Existing workspaces preserve their saved geometry and corrections; they are not silently recropped or migrated. Changed input paths, chapter, geometry settings or source fingerprints are rejected before completed-job reuse. Keep the original input at its recorded location for resume. Keep the entire live output workspace when moving it: originals, review/state.sqlite3, revision images and supporting files.

`originals/` contains unchanged source files. `proposals/` and `needs_review/` contain unapproved candidates. Sparse ambiguous/text content can remain source-location metadata with no crop PNG. `approved_artwork/` receives only explicitly human-verified illustrations and is empty after automatic runs. `reports/` has source coordinates, checksums, coverage and review state; `contact_sheets/` and `review/` support correction. Source pixels are preserved and artwork keeps its natural aspect ratio.

Inspect full source context, the crop and neighboring strips. Edit bounds, split/merge, ignore text, restore it, and undo/redo without restarting the chapter. Bulk Ignore selected text is reversible per item, with no bulk approval. A text metadata record may still contain actual monochrome art: classify or restore it when needed. Generic seam flags do not prove a damaged join. Successful export/pixel fidelity does not prove complete illustration boundaries.

## Tests

```sh
python -m pip install pytest
python -m pytest -q -m "not large"
```

Actual-source regressions require the supplied evidence and freshly run outputs:
```sh
python tools/run_ch01_v33.py --help
MANGA_CH01_EVIDENCE=/path/to/evidence MANGA_CH01_RESULTS=/path/to/results python -m pytest -q tests/test_real_ch01_integrated.py
```
The evidence folder contains SOURCES/CH01_ORIGINALS and CH01_SHA256.csv; the results folder contains opening_output, text_output, portrait_output and full_output. These real tests skip when private local fixtures are absent; skipped tests are not passes. Dedicated large-batch and UI evidence is described in the integrated report.
