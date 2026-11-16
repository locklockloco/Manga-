# V3 change log

This revision extends the existing Python/Pillow/OpenCV/NumPy/Flask application. It keeps natural artwork proportions and unchanged source files. All automated crops remain proposals; successful export and a heuristic score never grant verification.

## Durable jobs and corrections

- SQLite with WAL and synchronous commits records job stages, source/region checkpoints, errors, source checksums, settings, algorithm version, and review history. JSON/CSV files and named image outputs are repairable projections of that state.
- Source import, indexing, segmentation, export, QA, and review readiness have persisted progress. Pause, cancel, retry, and resume preserve completed work in the same output directory.
- An operating-system writer lock releases after process death. Immutable image revisions and staged atomic replacements protect prior valid crops and correction history.
- Preflight checks estimate disk and memory needs. Image decoding and PNG export remain bounded by source strips rather than a full chapter bitmap.

## Proposals and review

- Region proposals use local spatial evidence and segmentation masks in addition to full-width gutter profiles. Ambiguous groupings retain parent/child alternatives for review.
- Categories distinguish illustrations, embedded text/bubbles, standalone SFX, dialogue/narration, title/credits, compound layouts, and uncertain content. Recoverable review assets retain source coordinates.
- Unverified images are separated from approved artwork. Only an explicitly inspected human-approved illustration belongs in `approved_artwork/`.
- Source coverage, fidelity, boundaries, joins, reading order, classification, and human verification remain separate QA dimensions. Unresolved checks block production readiness.

These changes do not establish perfect segmentation. The measured real-chapter results and unresolved cases must be read alongside the QA/benchmark reports.

## Crash and resource regression checks

Eight focused job tests passed on Linux: the initial six ran in 65.02 seconds, and two added pause/cancel checks ran in 46.43 seconds under concurrent stress load:

- Genuine SIGKILL at the second INDEX checkpoint, followed by same-directory resume and idempotent re-entry.
- Genuine SIGKILL after the second EXPORT checkpoint, a simulated manual coordinate correction, another SIGKILL before the third export commits, and recovery without resetting that saved correction.
- Injected disk-full (`ENOSPC`), permission-denied (`EACCES`), and `MemoryError` export failures. Each job recorded failure, preserved its first committed output and original bytes, then resumed successfully after removing the fault.
- Disk/memory preflight rejection before large allocations.
- Pause and cancel requests acknowledged at a persisted INDEX checkpoint, followed by successful resume with original-byte and output-pixel checks.

Resource faults are injected exceptions; the test does not deliberately fill the real filesystem or exhaust host RAM. Linux OS locks and actual SIGKILL were exercised. Native Windows/macOS execution has not been measured here.

## Reproduce the representative stress tests

```sh
python tools/stress_v3.py suite /path/to/new/stress200 --strips 200
python tools/stress_v3.py suite /path/to/new/stress500 --strips 500 --no-interrupts
python tools/stress_v3.py suite /path/to/new/corrupt200 --strips 200 --no-interrupts --corrupt
```

Use a fresh directory for each complete suite. An interrupted directory is intentionally reused for its resume steps. Each source image is 690 × 1280, with a mixture of JPEG and PNG, full-bleed scenes, two/three drawings per strip, two/four-strip continuations, small gutters, off-white/colored separators, bubbles, narration, and tall drawings. Generation and export allocate individual source strips, not the complete chapter.

The harness stores subprocess logs, 0.2-second RSS/CPU samples, progress, original-byte hashes, streamed output-pixel comparisons, stable IDs, preserved correction coordinates/checksums, and orphan checks. Measurements are observed on a shared managed host; they are not isolated performance guarantees. Synthetic geometry is not a real-manga gold standard.

Long tests are opt-in: `MANGA_RUN_STRESS=1 python -m pytest -q tests/test_jobs_v3.py -m large`. Ordinary job regression tests run without that variable.

## Baseline scope

The specifically named V2 ZIP and audit environment from the improvement brief were not independently reproduced by this harness. The brief's V2 figures are user-supplied audit facts; the prior workspace's older results describe a different run. V3 result counts must come from the current manifests and QA reports, rather than either historical README.

Stress measurements and final aggregate test counts are recorded only after their runs finish. See the generated stress `suite.json` and `integrity*.json` files for the actual measured state.
