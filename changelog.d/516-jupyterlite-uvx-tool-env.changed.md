- **The JupyterLite site build now runs in an isolated `uvx` tool environment,
  and the `[jupyterlite]` extra has been removed.** clm never imported
  `jupyterlite-core` — it only shells out to `jupyter lite build` — so the build
  now invokes `uvx --from jupyterlite-core==<pin> --with … jupyter-lite build`
  with pinned versions of jupyterlite-core, both kernel addons, and
  jupyter-server (pins live in `src/clm/workers/jupyterlite/builder.py`). Because
  jupyterlite-core/`empack` (which caps `click<8.2`) can no longer enter clm's
  dependency graph, the `[tool.uv] conflicts` Click fork added in the previous
  release is gone too. Building the JupyterLite output format now needs only
  [`uv`](https://docs.astral.sh/uv/) on your PATH — nothing added to clm's own
  environment. See `docs/claude/design/dependency-environment-isolation.md`
  (Wave 2a) and issue #516.
