- **The `[all]` extra and the default `uv sync` environment no longer bundle
  `[ml]` or `[jupyterlite]`.** Neither is imported by clm itself — `[ml]`
  (PyTorch/pandas/transformers/…) is course-*runtime* that only Direct-mode
  notebook kernels import, and `[jupyterlite]` is a standalone build tool clm
  shells out to. Bundling them made every clm install multi-GB, and
  jupyterlite's transitive `empack` (which pins `click<8.2`) silently held the
  whole environment back to Click 8.1.8, breaking ~30 CLI tests on a
  freshly-synced worktree (issue #516 follow-up). `[ml]` and `[jupyterlite]` are
  now opt-in: `pip install -e ".[all,ml]"` for ML course decks, and
  `uv sync --extra jupyterlite --no-default-groups` (a `[tool.uv] conflicts`
  fork) to build the JupyterLite output format. The unused `deepeval` dependency
  was removed. See `docs/user-guide/installation.md`.
- **CI now installs from `uv.lock` (`uv sync --frozen`) instead of fresh
  `uv pip install`,** so continuous integration runs the exact dependency
  versions a developer gets locally rather than re-resolving independently.
