- `clm course renumber` — renumber `topic_NNN_` directories to match the course
  spec's topic order **without losing cached build state**: directories move via
  two-phase `git mv` and the `clm_cache.db` input-path columns are rewritten in
  the same run, so the next build replays every cached result instead of
  re-executing. Fails closed on non-canonical topic names (renumbering them
  would change their topic id), orphan collisions, ambiguous topic resolution,
  and active builds; `--report-only`/`--json` preview the full change set
  including dry-run cache-row counts (#589).
