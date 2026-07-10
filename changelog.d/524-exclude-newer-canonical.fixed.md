- Pin `[tool.uv].exclude-newer` as uv's canonical `<date>T00:00:00Z` timestamp
  instead of a bare date, so `uv lock --check` / `uv sync --locked` accept the
  committed lockfile on a clean checkout ([#524](https://github.com/hoelzl/clm/issues/524)).
  `scripts/update_exclude_newer.py` now canonicalizes a bare-date argument to
  the next UTC midnight (and passes a full timestamp through unchanged),
  `scripts/check_exclude_newer.py` requires exact equality between
  pyproject.toml and uv.lock (the old date-prefix comparison let the drift
  reach CI), and CI installs with `uv sync --locked` again, restoring
  fail-fast lockfile-drift detection.
