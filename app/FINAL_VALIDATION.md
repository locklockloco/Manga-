# Final processing validation

The final detector `v3.1-regions-2026-11-14.1` completed fresh 200- and 500-strip tests. Its SHA-256 is `8af046b392b1771d0833c44716aa3ea0108e0753d1007392624149e20f3a7c5f`.

Every input strip was **690 × 1280**. The new jobs began with empty output directories and no prior image profiles or crop results. Existing synthetic input bytes were reused; the operating system's file cache was not flushed. Both jobs ran on a shared Linux host, alongside other validation work. Contact-sheet generation was disabled for these measurements; import, indexing, segmentation, PNG export, and QA were included.

| Final run | Input pixels | Review exports, including alternatives | Processing wall time | Sampled CPU time | Sampled peak RSS | Input strips/s |
|---|---:|---:|---:|---:|---:|---:|
| Fresh 200, including intentional interruptions and retries | 176,640,000 | 134 | 600.313 s | 427.590 s | 148.49 MiB | 0.3332 |
| Fresh 500, uninterrupted | 441,600,000 | 334 | 1260.652 s | 1067.050 s | 167.84 MiB | 0.3966 |

The 200-strip first attempt took 42.316 seconds and stopped intentionally at INDEX 60/200; its subsequent processing attempts totaled 557.997 seconds. The 500-strip run completed in one attempt. Totals exclude fixture generation, simulated manual edits, external integrity audits, post-completion fault checks, and separate damaged/missing-source jobs. CPU/RSS samples were taken every 0.2 seconds; sampled peaks are observations, not guaranteed memory ceilings or isolated speed benchmarks.

## Actual integrity results

| Check | Fresh 200 | Fresh 500 |
|---|---:|---:|
| Unchanged original files compared | 200 | 500 |
| Exported pixels compared against exact source coordinates | 208,828,688 | 524,630,830 |
| Pixel mismatches | 0 | 0 |
| VERIFIED regions | 0 | 0 |
| Unreferenced active/revision PNGs | 0 | 0 |
| Leftover staging files | 0 | 0 |

Export-pixel totals include overlapping review alternatives; they are **not distinct source-coverage counts**. Every exported file was checked, including inactive alternatives. Actual immutable PNG hashes and named projection hashes matched each manifest. The SQLite manifest matched its JSON projection; CSV rows, bounds, hashes, and source-pixel rectangles also matched. Stable IDs remained unique.

**These are fidelity and durability results, not proof of complete illustration boundaries.** Both jobs finish at stage `REVIEW_READY` with status `REVIEW_REQUIRED`. Nothing in these tests grants production readiness or human verification. Semantic accuracy, false merges/splits, severe cutoffs, and real chapter acceptance belong to the separately annotated benchmark and visual review.

## Interruptions, corrections, and request identity

The 200-strip test recorded genuine SIGKILL exits (`-9`) at INDEX 60/200 and 75/200, after EXPORT 2 committed, and before EXPORT 3 committed. It also acknowledged pause at INDEX 65 and cancel at INDEX 70, then resumed the same output directory.

A simulated manual boundary edit between export interruptions survived with its exact saved coordinates and PNG checksum. Previously committed output checksums and all original bytes survived. Re-entry after completion was idempotent.

Post-completion checks established that:

- A damaged named PNG projection was repaired from its valid immutable revision.
- Damaged immutable PNGs and copied originals were rejected without overwriting valid projections or changing saved job/manifest state.
- A different input, chapter ID, or segmentation setting was rejected without changing saved work.
- Changed input bytes and a missing input source were rejected. The harness restored its deliberately damaged **synthetic** fixtures afterward; the application did not silently rewrite immutable history.

The exact fault outcomes, stderr reasons, preserved checksums, and correction history are serialized in the linked reports.

## Separate source-90 fault jobs

Both tests used a separate 200-position input copy; they did not damage the clean shared fixture.

| Case | Valid decoded strips | Raw files retained and checked | Review exports | Blocked joins | Exports crossing blocked joins | Integrity errors |
|---|---:|---:|---:|---:|---:|---:|
| Corrupt JPEG at position 90 | 199 | 200, including the corrupt bytes | 133 | 1 | 0 | 0 |
| Missing position 90 | 199 | 199 present files | 133 | 1 | 0 | 0 |

Each case compared 208,011,763 exported pixels to original source coordinates with zero mismatches. JSON/CSV mappings and immutable/projection hashes matched. Missing/corrupt-source diagnostics remained visible and production status stayed `REVIEW_REQUIRED`. “Passed” here means safe recovery and reporting; missing artwork was not invented or declared complete.

## Environment restart and build identity

An unplanned environment restart happened **after** main-200 processing and its completed-job fault checks finished. Its original measurements were retained unchanged. The corrupt-source subjob resumed from its persisted state; its prior 284.993-second last sample is only a lower bound, and the measured 459.738-second resumed attempt is not presented as a complete cold-job time. The missing-source job then completed fresh.

An earlier 500-strip attempt was also interrupted at SEGMENT 239/252. Its last sample, 898.666 seconds, is retained as incomplete evidence and excluded from the table. The successful 500 result above came from a different, empty `500-cold-final/output` directory with no reused analysis cache.

Every completed measured attempt checked frozen Python hashes before and after execution. The eight processing files—detector, runner, pipeline, QA, identity, importer, chapter reader, and SQLite storage—remain byte-identical to the measured build. Full measured application/harness hashes are retained in the freeze reports. A subsequent `review.py` display-provenance change is outside these processing measurements and is recorded separately; it does not change those eight processing hashes.

Native Windows/macOS execution remains untested. No universal cropping-accuracy or native-platform performance claim is made.

## Evidence and reproduction

Portable evidence is included with the application:

- [Processing module hashes](validation/final_stress/processing_build.json)
- [Complete 200 report](validation/final_stress/200/final_suite.json), [final integrity audit](validation/final_stress/200/integrity_final_recovered.json), and [JSON/CSV consistency](validation/final_stress/200/manifest_consistency.json)
- [Complete 500 cold report](validation/final_stress/500-cold-final/final_suite.json), [final integrity audit](validation/final_stress/500-cold-final/integrity_final.json), and [JSON/CSV consistency](validation/final_stress/500-cold-final/manifest_consistency.json)
- [Corrupt-source result](validation/final_stress/200/corrupt_90/source_fault_result.json) and [missing-source result](validation/final_stress/200/missing_90/source_fault_result.json)
- [Interrupted 500 evidence, not a completed benchmark](validation/final_stress/500-interrupted/unplanned_restart.json)

The source generator and harness are `tools/stress_v3.py`; optional pytest entry points are `tests/test_large_final_v3.py`. Generate full-size fixtures, then use a new output directory for each complete suite:

```sh
python tools/stress_v3.py generate /path/to/fixture200 --strips 200
python tools/stress_v3.py generate /path/to/fixture500 --strips 500
python tools/stress_v3.py final-suite /path/to/new/run200 --fixture /path/to/fixture200 --strips 200 --source-faults --alternate-fixture /path/to/fixture500
python tools/stress_v3.py final-suite /path/to/new/run500 --fixture /path/to/fixture500 --strips 500 --no-interrupts
```

The application itself resumes an interrupted chapter through the normal `process` command against the same output directory. The full validation harness deliberately requires a fresh initial output; its interruption steps reuse that job only within the suite.
