- `scripts/install_global_clm.py` reinstalls this checkout as the global
  editable `clm` uv tool only when nothing holds the old tool environment.
  It lists processes running from it (e.g. agents' `clm mcp` servers) and,
  on Windows, probes the environment with a rename. This avoids the
  half-installed tool that `uv tool install --force` leaves behind when a
  file in the environment is still in use. `--check` runs only the checks.
