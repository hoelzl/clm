- **Internal**: removed every cross-module underscore-private import (#802
  Phase 8 A9). Shared seams now have public names in their defining modules —
  `build_client` (LLM client factory), `summaries_by_hash` (export context),
  `atomic_write_text` (cassette writes), `git_toplevel`,
  `group_paths_into_units`, `twin_ids_for`, `lines_sans_id`, `is_shared_cell`,
  `apply_slide_ids`, `format_exit_failure`, `build_course`, and the
  `validate` command helpers. The duplicated cell-boundary predicate and
  workshop-range membership check each collapsed onto their canonical copy.
  A new architecture-contract test fails on any future
  `from clm.x import _name` import, so the count stays at zero. No
  user-facing behavior change.
