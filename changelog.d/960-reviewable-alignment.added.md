- Added `clm harvest align report` / `align accept` (#960, umbrella #970):
  the deterministic pipeline now keeps its judgement trail — per-segment
  assignment records with overlap fractions and runner-ups (aligner), and
  runner-up / sequential-override evidence on timeline entries (matcher).
  `align report` frames uncertain assignments, unassigned segments, and
  weak/overridden slide matches as reviewable items; `align accept` applies
  agent-decided segment reassignments (freshness-guarded by an
  `alignment_fingerprint`), re-derives the `[Revisited]` grouping, and
  writes a full alignment file to feed back via `--alignment`. Older cache
  entries decode unchanged (the new fields are additive).
