- **Internal re-layering (Phase 8 step A6, refs #802)**: the course-domain path
  vocabulary (`Lang`/`Format`/`Kind`, `OutputSpec`, `output_specs()`,
  `output_path_for()`, the skip/ignore rules, slide file-family detection, image
  dir constants and prog-lang/extension mapping) moved from
  `clm.infrastructure.utils.path_utils` to `clm.core.utils.path_utils`. The
  infrastructure module keeps only the filesystem helpers (`find_project_root`,
  `atomic_write_all`, `atomic_write_bytes`). Import paths only; no CLI behavior
  change.
