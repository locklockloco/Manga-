# V3.2 artwork-first candidate

- Dialogue no longer causes floating-bubble attachment or crop expansion.
- New bounded artwork-priority component proposals; unexplained drawing details
  and multi-object compositions remain protected.
- Standalone text-pattern candidates have reversible metadata instead of PNGs.
- Explicit dialogue/SFX classification suppresses public exports while retaining
  immutable revision history; classification back to illustration restores them.
- Review ignored-text filter and restoration; approval concerns artwork, with
  optional dialogue. Source files, geometry maps, input identity validation,
  checksum repair, manual edits and checkpoint mechanisms remain in place.
- Added artwork-policy and reversible-exclusion tests; updated previous mandatory
  speech tests and classification-output expectations for the requested policy.

Known unresolved: CH01_P155 not visually testable without Chapter 1 sources;
giant dark-background scene merges and other Chapter 1 audit cases are not fixed
by this policy patch. Read ARTWORK_FIRST_REPORT.md. This is a supervised candidate,
not an unattended-production release. Historical V3.1 metrics are not V3.2 results.
