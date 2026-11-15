# V3.2 artwork-first candidate — scope and remaining work

## Implemented

- Removed the automatic floating-text/bubble attachment operation from new
  segmentation. A separated speech balloon is not a reason to expand art.
- Added bounded component analysis that proposes exterior trimming around
  multitone artwork. It retains all unexplained marks (including small detached
  black drawing details) rather than zooming to the largest character/face.
- Retains every substantive artwork island: multiple characters/objects share
  the full composition. No aspect-ratio conversion, resampling of exports,
  inpainting or source modification was added.
- Repeated aligned low-palette glyph-pattern candidates are recorded as ignored
  text metadata instead of being exported. This is a conservative geometric
  heuristic, NOT OCR or semantic proof. It may miss stylized lettering and may
  mistake repeated simple graphics for text; ignored locations remain reviewable.
- Explicitly classifying an existing panel as standalone SFX or dialogue/narration
  removes its public PNG from the artwork queue. Original files and old immutable
  revisions survive. Reclassifying it as illustration restores a review proposal.
- Review UI exposes ignored text through a filter, hides its PNG download action,
  and no longer requires intact speech bubbles for artwork approval.
- Source coordinates, earlier proposal bounds, trim evidence, and original
  unmodified input bytes remain available. No automatic human approvals.

## Tests and scope

See attached artwork-priority-tests.log and artwork-priority-ui-tests.log.
New tests cover exterior dialogue, complete multicharacter composition,
small detached monochrome details, uncertain monochrome art, standalone glyph
patterns, and reversible exclusion with immutable revision/source preservation.
The two old bubble-attachment tests were deliberately changed because the user's
new policy supersedes mandatory speech preservation; their artwork fixtures stay
unchanged. Frozen Chapter 5 benchmark labels were not modified.

The 200/500-strip results and V3.1 benchmark numbers elsewhere in this package
are HISTORICAL. They are not fresh V3.2 performance or segmentation measurements.
Native Windows/macOS and a full V3.2 large-batch rerun were not performed.

## What is NOT established

- CH01_P155: NOT TESTED. The two new attachments are the Chapter 1 audit and run
  summary. Neither contains the original Chapter 1 pixels, source strips or the
  P155 crop. The only chapter archive currently available is Chapter 5. Cannot
  verify the man's face or produce an honest real before/after without Chapter 1.
- A scan of bounded active Chapter 5 crops yielded no >=8% area reductions under
  the conservative component trim. No changed real-artwork example is presented
  as proof of improvement. The primary tested change is removal of mandatory
  bubble attachment, verified with controlled fixtures.
- Chapter 1 giant multi-scene crops, staggered boundaries, uncertain text/art
  semantics, and the audit's enumerated incomplete-child cases remain unresolved
  on real Chapter 1 input. Oversized candidates now receive a review flag; they
  are not claimed to be segmented correctly.
- Palette/component geometry cannot guarantee complete artistic composition or
  identify every speech bubble. Ambiguous content remains for review. Speech
  touching/embedded in real art is not erased or painted over.

## Applying and reviewing

Install with the README commands. Process input into a NEW output folder; saved
V3.1 outputs are not silently resegmented or stripped of corrections. Opening an
old workspace continues to show its original saved bounds. To manually suppress
text, choose Standalone SFX or Dialogue/narration only. Use the ignored-text
filter (or that category) to inspect and restore the record as Illustration.

Required next visual regression inputs: Chapter 1 original archive or the full
source strips contributing to CH01_P155, preferably with the prior manifest and
crop. A prior exported crop alone cannot prove that no original artwork is lost.

Release assessment: artwork-policy candidate for supervised testing, NOT a
production-ready repair of the full Chapter 1 audit. CH01_P155 visual acceptance
is blocked by missing input, and known semantic segmentation failures remain.

## Final executed checks

```text
........................................................................ [ 40%]
...................................................................ssss. [ 80%]
..................................                                       [100%]
174 passed, 4 skipped, 4 deselected in 162.77s (0:02:42)
```

UI/API/artwork subset: 27 passed. JavaScript syntax and Python compilation passed.
Four real-input acceptance tests skipped; four large-batch tests deselected.
