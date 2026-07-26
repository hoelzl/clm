- **A course repo can no longer choose which program CLM runs.** `clm.toml` /
  `.clm/config.toml` are discovered by walking up from the working directory, so
  they are found *inside a cloned course repo* — and
  `external_tools.drawio_executable` reached `subprocess` with no validation at
  all: clone a course repo, run `clm build`, and a repo-supplied binary executes
  on the first `.drawio` file, on the host, in every worker mode, before any of
  that repo's own content runs. Both executable-path keys
  (`external_tools.plantuml_jar`, `external_tools.drawio_executable`) are now
  dropped from the *project* config tier with a warning naming the file; the
  operator tiers (user config, `PLANTUML_JAR` / `DRAWIO_EXECUTABLE`) are
  unchanged, and `CLM_ALLOW_PROJECT_TOOL_PATHS=1` opts back in per invocation —
  an environment variable because a repo cannot set one.
  `[jupyter] kernel_python` is deliberately still allowed from a project config
  (it is documented, load-bearing, and selects the interpreter for the repo's own
  notebook code, which a Direct-mode build executes on the host regardless).
- **Git remote URLs derived from a course spec are validated, and the `ext::`
  transport is disabled.** `<repository-base>` / `<remote-template>` become a URL
  handed to `git clone` / `git ls-remote`, and git's `ext::<command>` transport
  **executes its argument as a shell command** (`protocol.ext.allow` defaults to
  `user`, i.e. allowed for exactly this kind of direct invocation). Derived URLs
  are now checked against a scheme allowlist (`https`, `http`, `ssh`, `git`,
  `file`, the scp-like `user@host:path` form, or a local path) with an error
  naming the spec element, and every git invocation additionally runs with
  `-c protocol.ext.allow=never` — which also covers remotes CLM never derived,
  such as one hand-edited into an output repo's `.git/config`.
