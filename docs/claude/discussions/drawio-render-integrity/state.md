---
status: active
owner: maintainers
updated: 2026-11-16
review-by: 2027-05-16
---

# State: Draw.io render integrity and cache repair

The session ran from the CppCourses checkout (Claude slug
`G--Courses-CppCourses`) but was about PythonCourses (the ML AZAV cohort
releases) and this repository. It is saved here because the design was
settled here. CppCourses keeps the operational recipe in its
`session-knowledge.md` and a pointer to this thread. Task state is in the
issues and PRs below.

## Settled

- **Root cause (#1245).** A committed PythonCourses diagram PNG came back
  broken after a build. The owner suspected the converter images published
  that day. The agent measured six byte-identical renders across images
  1.34.3 and 1.39.0, so the hypothesis was falsified. The bad PNG came from
  a `processed_files` row written on 2026-11-09, when moving the repo to
  another drive changed every absolute-path key and every diagram
  re-rendered at once. The pixel diff showed the same dimensions with
  rectangular tiles of connector strokes missing. Deleting the row and
  rebuilding fixed it. A re-pulled mutable tag would not have changed the
  cache key anyway (`caching.md`, diagram keys).
- **The size check was withdrawn.** The agent's first suggestion in the
  issue was a size/dimension heuristic. The pixel diff killed it: identical
  dimensions, only 8% smaller.
- **Design (owner + agent).** The owner proposed "if the source is
  unchanged, render once and compare with the stored hash; otherwise render
  twice". The agent removed the source-change branch: compare the render
  with the file already at the output path; on a mismatch, accept only when
  two renders agree; three different renders fail the job. Deciding
  "unchanged" would have meant trusting the cache under suspicion, and a bad
  file on disk heals. The owner accepted ("I like your proposal").
- **Settle delay: deferred, not rejected.** The owner thought it cheap; the
  agent argued that incomplete painting is inferred, not measured, and that
  a fixed delay costs time on every render. Mismatch warnings are logged, so
  how often they occur can decide it later (more likely by lowering
  converter concurrency).
- **`clm cache forget`:** the owner called it "a clear win".
- **#1251 merged** (both pieces, CI green). Native Windows draw.io was
  measured deterministic run to run, but its bytes differ from Docker's.
- **#1252 filed:** repository relocation cold-misses every key.
  Recommended: `clm cache relocate OLD NEW` now, root-relative keys at the
  next cache-schema bump. A content-hash fallback is rejected (C++ exports
  embed the stem, #928).
- **No cleaner issue (owner's question at the end).** CppCourses's
  `tools/clean_transcript.py` leaked six harness items in this session (two
  "Tool loaded.", two image-size notes, a duplicated owner turn, and the
  compaction summary, which also sat in an `api-request-blob` record as a
  bare string). This repository's `scripts/clean_transcript.py` already
  drops `api-request-blob` records (the S20 fix) and cleaned the raw
  session with nothing leaking. The CppCourses copy is an older fork (588
  vs 820 lines, a strict subset of the functions), so the fix is to port
  this one there, not a clm issue.

## Open / deferred

- #1252 is unstarted.
- #1273: worker `main()` tests parse pytest's own `sys.argv` under a serial
  `-n0` run with arguments. Found while testing #1251.
- The settle delay and converter concurrency wait on the mismatch warnings.
- The paint-timing mechanism is still unconfirmed.
- Porting the cleaner into CppCourses (a CppCourses change).

## Known weak points

- The determinism premise rests on one diagram (6 Docker + 3 Windows
  renders). A diagram type that renders non-deterministically now fails its
  job instead of caching, and the error message says so.

## Next boundary

Resume when #1252 is picked up, or if `Draw.io renders disagreed` warnings
appear in course builds.
