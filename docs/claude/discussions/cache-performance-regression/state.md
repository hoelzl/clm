---
status: active
owner: maintainers
updated: 2026-11-10
review-by: 2027-05-10
---

# State: cache performance regression (#711, cached builds that were not)

Saved retroactively on 2026-11-10 from the Hermes session of 2026-07-27 (S22).
The findings, the measurements and the ranked candidates live in
`docs/claude/cache-performance-investigation.md`, which this session wrote.
Today's guidance for the same symptom is `AGENTS.md`'s troubleshooting item 4
(`clm cache explain`) and `docs/developer-guide/caching.md`. This file keeps
only the conversational position.

## The owner's question (S22)

- An impression, stated as one: building a large course with many cached files
  felt noticeably slower on clm 1.23 than on 1.21, most of all the AZAV ML
  course. "This is not a measured result, just my impression from waiting for
  the courses to finish, so it would be great to actually measure this."
- One constraint: "Please make sure that you don't remove the cache or the
  output directory during the investigation."

## Settled (S22, 2026-07-27)

- **File an issue first, then measure** (owner): #711.
- **Pause while the owner prepares courses for recording** (owner). The agent
  stopped its builds. The owner then offered the next real build as a
  measurement: "Is there something you want to do to set up so that this build
  will be useful for you?"
- **Keep five cache versions per file** (owner, asked for it to be made
  permanent): `CLM_RETENTION__CACHE_VERSIONS_TO_KEEP=5` for that build, then
  `[retention] cache_versions_to_keep = 5` in PythonCourses' `clm.toml`.
- **Write the results into a file, open a PR, then start on the optimization**
  (owner, in that order).

## What was found

- **The cause of the "content changed" storms:** two installs of the same clm
  hashed the notebook templates differently. The global `clm` was an editable
  install of the working tree, whose templates had CRLF line endings; the course
  venv's copy had LF. The template fingerprint hashed raw bytes, so switching
  installs invalidated every notebook's cache entry.
- **Why a mostly cached build was still slow:** a full-scan `DELETE` on every
  per-operation open of the executed-notebook cache (new in 1.23), pickle loads
  and output hashing per hit, and a health-monitor thread spending about 57% of
  its samples on system-wide `psutil` environment scans.
- **Workers:** the owner's build with `--notebook-workers=8` finished in about
  12.5 minutes. The agent's own run with the default single worker failed after
  29 minutes.
- **Ruled out:** the cached fast path itself, unchanged from 1.21 to 1.23; the
  cache hash schema version; the WAL heuristic; cassettes leaking into hashes.

## Shipped

- Issue #711 (closed 2026-09-18).
- PR #712 (merged 2026-07-27): the investigation doc.
- PR #713 (merged 2026-07-27): the fingerprint normalizes newlines; the
  executed-notebook migration runs once per database; the worker liveness scan
  is narrowed and cached.

## Open / revisit conditions

- Left on #711 at the end of the session: the default notebook-worker count,
  the notebook jobs that hung for 1200 s, and hashing per file instead of per
  topic (one edit rebuilt about 90 operations). Where each went afterwards was
  not traced at this save; start from #711 and `caching.md`.

## Not in the transcript

- Two owner steers the agent's replies refer to (the correct spec name, and why
  the course tree was clean) are not in the source as owner rows.
- Background-process notices, the preserved task list and the compaction
  copies, removed as harness rows.

## Agent corrections recorded in the transcript

- The first timed build used the wrong spec (`machine-learning-azav.xml`). The
  owner named the right one, `machine-learning-azav-2026-04.xml`.
- The agent's measurement caused the thing it was measuring: it built with the
  course venv's clm while the owner used the global one, which flipped the
  template fingerprint, missed 51% of the cache, rewrote 3,660 output files and
  then failed. The owner's next build repaired the tree.
- The agent's prime suspect after the first run, a cycle of hanging notebooks
  that never lets the cache warm, was not the cause. The line-ending difference
  was.
- The `--explain-rebuilds` flag the agent asked for cost 36% of the main
  process's time during the miss storm. It was the agent's instrumentation, not
  a cost of the owner's normal builds, and the agent said so.
- A mid-build `git pull` in the course repo muddied the first comparison. The
  agent asked for none during the second.
