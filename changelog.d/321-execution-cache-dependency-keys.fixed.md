- `clm build` notebook caches no longer replay stale execution results when
  a dependency changes with unchanged deck text (#321): the cache keys now
  cover every topic sibling shipped to the kernel (C++ `#include` headers,
  Jinja `{% include %}` targets, runtime data files), a fingerprint of the
  bundled Jinja templates (`macros.j2` etc.) plus the CLM version, the
  worker execution environment (`direct`, or the configured Docker image
  reference — a cache populated under one worker image is no longer
  replayed under another; pin versioned tags rather than `:latest` for
  exact invalidation), and the per-topic `evaluate=` / `skip-errors=`
  flags. The HTTP-replay cassette remains deliberately excluded
  (record-after-run miss loop). The first build after upgrading re-executes
  everything once (key schema change).
- Cached-issue replay actually works for notebook jobs again (#321): stored
  errors/warnings were keyed under `str(tuple)` output metadata while
  lookups used the colon-joined form, so a cache hit never re-surfaced the
  warnings/errors recorded for that content. Both cache layers (database
  result cache and job-level cache) now replay stored issues on a hit; the
  job-level path previously dropped them entirely.
- A successful build of a deck now clears errors/warnings stored by earlier
  runs of the same content (#321): previously a transient failure's stored
  error would have been replayed on every later cache hit, and repeated
  `--ignore-cache` runs accumulated duplicate warnings.
- `clm build --output-mode verbose` now prints an explicit
  `↻ Replayed from cache` line for every file served from a cache instead
  of executed (#321) — replayed output is freshly timestamped and was
  previously indistinguishable from executed output.
