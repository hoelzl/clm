- **Course loading no longer re-scans `slides/` once per module-bound topic.**
  A spec that binds topics to a module (`<topic module="…">` or a section-level
  `module=` default) resolved each bound topic by re-walking the entire
  `slides/` tree from scratch, so "Loading course specification…" scaled with
  *(bound topics × total slide files)*. The full topic map is now built once and
  cached (`Course._full_topic_map`) and reused for module-bound resolution. On a
  real 124-topic / 54-module-bound course over ~2,150 slide files this cut
  `Course.from_spec` from ~32 s to ~1 s.
- **Provenance-manifest hashing now runs in parallel.** Writing
  `.clm-manifest.json` re-reads and SHA-256-hashes every output file; the reads
  were serial, so the step took several seconds on a large output tree. Hashing
  is now done with a bounded thread pool (order-preserving, so the manifest is
  still deterministic), a ~4–7× speedup per target on a multi-core machine
  (~6 s → ~1.2 s across three targets in local testing).
