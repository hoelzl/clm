- **Provenance manifest no longer records `.git` (and other never-copied
  paths) from output trees (issue #302).** The manifest's dir-group walk now
  applies the same ignore filter as the build's dir-group copy, so a `.git`
  left inside an output target (e.g. by `clm git init`) no longer enters the
  skeleton as 1000+ topic-less entries that `clm release sync` would then
  copy into a cohort repo — for a language-scoped channel landing at the repo
  root and clobbering the destination's real `.git`. As defense in depth,
  `clm release sync` now refuses to copy any manifest path containing a
  `.git`/`.svn`/`.hg` segment (with a warning), so a polluted manifest from
  an older build can never overwrite a destination repo's VCS metadata.
