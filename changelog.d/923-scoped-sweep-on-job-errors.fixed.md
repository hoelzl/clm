- **One failed notebook no longer disables the whole stray-file sweep
  (#923).** A build that recorded any error skipped the post-build sweep
  wholesale, so after a spec restructure a single failing deck left the
  previous revision's notebooks duplicated (old section next to new) across
  every output tier. The skip existed because the write registry is missing
  the writes that never happened — but for a per-job failure those writes
  are exactly the failed job's outputs. The backend now stamps the unwritten
  output path on failed jobs and on jobs orphaned at build give-up; when
  every error is such a job-scoped error (none fatal, build neither timed
  out nor aborted) the sweep runs scoped around them: the directory holding
  each failed output is left untouched together with everything below it (a
  job may write companion files beside its output), while every other stale
  file is removed — including a failed deck's previous copy at an old
  location, which regenerates once the deck builds again. Errors whose
  missing writes cannot be enumerated — a fatal abort, a timed-out build,
  course-load / cross-reference / image-collision errors, replayed cached
  errors — keep the wholesale skip and its "NOT swept" notice. An unowned
  output root that merely contains a protected directory is kept unowned
  without being reported as an ownership refusal. The startup notice says
  what was kept; `clm info commands` documents the rule.
