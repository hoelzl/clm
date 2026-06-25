- **Cache & config discovery now walk up to the project root.** `clm` resolves
  the LLM/sync cache directory, project config, and the build/jobs databases
  relative to the **project root** — discovered by walking up from the current
  directory to the nearest `pyproject.toml` / `.clm/config.toml` / `.git`, like
  `git` / `uv` / `ruff`. Running a command from a subdirectory (e.g. a topic dir)
  no longer treats the subdirectory as the root: it stops ignoring
  `[tool.clm] cache_dir`, stops creating a stray `<subdir>/.clm-cache/`, and so a
  watermark/`baseline bless` written from a subdir is visible from the repo root
  (#477). `clm config locate` now also reports the discovered project root. An
  explicitly supplied `--cache-db-path` / `--jobs-db-path` is still honored
  verbatim.
- **`clm slides sync` watermarks now work from a git worktree.** The watermark is
  keyed by the **main-checkout** path even when the command runs inside a linked
  git worktree, so a watermark recorded from the main checkout is found from any
  worktree (and writes land on the one canonical key instead of accumulating
  orphaned worktree-path rows). Previously every pair silently missed its
  watermark from a worktree and cold-started off git `HEAD` (#435). The committed
  per-slide sync ledger (#448) was already worktree-portable and is unaffected.
