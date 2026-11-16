# V3.3 development changes — real Chapter 1 acceptance BLOCKED

- Route unresolved source spans to SOURCE_REVIEW metadata BEFORE unbounded
  island analysis, crop assessment or PNG export. No maximum-height chopping.
- Suggest low-edge boundary bands on dark/colored backgrounds as explicitly
  unconfirmed source-coordinate markers, not automatic scene cuts.
- Preserve source IDs and full-range coordinates; render review tasks in the
  local original-image interface. A task cannot be VERIFIED. Explicitly changed
  manual bounds can become a new proposal, with prior range retained.
- Add optional bounded Tesseract OCR (65+ word confidence) and closed light
  envelope evidence to separate some balloon/text analysis from drawing ink.
  Original pixels are not erased. Text-only candidates are metadata-only before
  export, including alternatives. Explicit classification/restoration persists.
- Unrecognized repeated glyph patterns become source-review tasks rather than
  being declared proven text. Dense-art exclusions are guarded; OCR absence,
  failure and timeouts are recorded. No claim of semantic understanding.
- Record explicit rectangular-text-removal geometry limitations. Retain actual
  drawing detail rather than forcing a trim when envelopes are ambiguous.
- Add per-stage wall-time observations, process-lifetime peak RSS and output disk
  size. Peak RSS excludes OCR child peak. Disk size is not total write-I/O bytes.
- Keep job fingerprints, immutable revisions, synchronization repair, WAL state,
  manual history, natural proportions and no-auto-approval behavior.

Unresolved: real CH01 portrait, eight text/SFX cases, real dark scene separation,
diagonal typography, overlapping layouts and 194-strip acceptance. Their original
pixels and reference/evidence ZIP were not available in this workspace. These
changes are NOT a completed V3.3 fix or benchmark. Chapter 1 precision/recall and
before/after artwork cannot honestly be produced without those files.

OCR installation: Tesseract is optional, Apache-2.0 licensed. Install Tesseract
with English trained data (apt: tesseract-ocr and tesseract-ocr-eng; macOS:
brew install tesseract; Windows installation guidance at
https://tesseract-ocr.github.io/tessdoc/Installation.html). No Python dependency
is added. The executable must be on PATH. Missing OCR retains evidence for
review; it does not mean standalone text has been successfully filtered.
English training data may be installed/downloaded by the package manager.
