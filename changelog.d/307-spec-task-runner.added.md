- **`clm run` — spec-defined task sequences.** A new `<tasks>` block in the
  course spec declares named sequences of clm commands (e.g. a `pre-release`
  task that regenerates calendar/outline exports and then builds, so the
  output never ships stale files). `clm run pre-release course.xml` executes
  the steps in order; `clm run course.xml` lists the spec's tasks; `--dry-run`
  previews the resolved commands. Steps are clm commands only (no shell — that
  is what makes tasks portable across machines), support a `{spec}`
  placeholder, run as subprocesses in the same Python environment, and are all
  validated (placeholders + command existence) before the first one executes.
  The first failing step aborts the task with its exit code. `clm validate`
  checks declared tasks too. `python -m clm` now works (new module entry
  point). See `clm info spec-files` / `clm info commands` and
  `docs/user-guide/tasks.md`.
