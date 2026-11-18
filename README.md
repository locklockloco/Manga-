# General-purpose manga extractor — unreleased research branch

This is the benchmark/annotation/model-feasibility foundation requested by the redesign kit. **Not production-ready. No new numbered release, no accepted detector replacement, no crop approval, and no merge into main.**

[Download the research application source ZIP](general-redesign-review/Manga_General_App_Research_Source.zip) — 353,161 bytes, below 5 MB.

[Read the measured report](general-redesign-review/RESEARCH_REPORT.md) · [Install and workflow](general-redesign-review/GENERAL_REDESIGN.md) · [Blocked quality gates](general-redesign-review/release_gates.json)

The source preserves the existing importer, source mapping, identity checks, checksum repair, immutable history and review UI. It adds source-frozen corpus intake, native reference labeling, evaluator integration, local CPU panel-model trials, a bounded multi-chapter queue and handoff contracts. Model weights are optional, separate and explicitly documented.

Real data: 279 unchanged native source strips across Chapters 1 and 5; 50 fixed development windows compared with the unchanged detector, a learned panel/bubble model and a hybrid. No human-approved gold exists, so quality precision/recall is N/A. The model still proposes standalone RIIING/SFX and nested clock fragments; it was not installed as the default cropper. Chapters 2–4, the R7 ZIP named by the kit, and a distinct style were not present in the task environment. Holdout acceptance remains pending.

The private native-art evidence is provided separately in chat as 32 ordinary ZIPs, all below 5 MB, including originals, comparisons, native failures, exact source maps and the complete S0039–S0042 join check. No new chapter artwork or model weights are added to this public research branch. Report-relative evidence paths describe that private extracted folder.

Extract the research source ZIP on a computer, install requirements and use `python -m manga_tool research --help` or `python start.py`. Read GENERAL_REDESIGN.md before running the experiment. Linux CLI/Chromium were checked; native Windows/macOS and GPU are untested.

SHA-256 of source ZIP: `a2557002caebf86fb34ea77dba76dbd73ea2b3bcca47631c3e3e2db02df20caa`.
