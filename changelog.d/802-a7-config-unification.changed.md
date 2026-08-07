- **Config unification close-out (A7 of #802).** The `[tool.clm]` pyproject
  table now has a single shared reader (`clm.core.utils.pyproject_settings`),
  and `clm config show` / `clm config locate` display the authoring
  sidecar-layout default with its source (env / pyproject / unset) alongside
  the LLM cache dir; `config show` also gained `[Authoring]` and `[Git]`
  sections and an `authoring` block in `--json`. New config-file settings,
  each folding under its existing env var: `[external_tools] mitmdump`
  (`CLM_MITMDUMP`; also stripped from repo-local configs like the other
  executable paths), `[jupyter] cell_timeout_seconds` /
  `replay_cell_timeout_seconds` (`CLM_CELL_TIMEOUT_SECONDS` /
  `CLM_HTTP_REPLAY_CELL_TIMEOUT_SECONDS`; the host now injects the resolved
  values into **both** Direct and Docker notebook workers — Docker workers
  previously never saw them), and `[git] token_auth` (`CLM_GIT_TOKEN_AUTH`).
  The three-channel configuration model is documented in
  `docs/user-guide/configuration.md` ("How the pieces fit together").
