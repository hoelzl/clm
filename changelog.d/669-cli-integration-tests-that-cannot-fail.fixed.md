- **The CLI build integration tests can now fail.** Every functional assertion in
  `tests/cli/test_cli_integration.py` was wrapped in `if result.exit_code == 0:`,
  one assertion was a literal tautology (`assert "does not exist" in output or
  exit_code != 0`, immediately after asserting `exit_code != 0`), and the course
  output check globbed `kurs-2-*` — a pattern that has matched nothing since the
  three-tier default output structure landed. The suite reported green for
  "CLI → Backend → Workers → Output" while only detecting argument-parsing typos.
  Build success and the produced output tree are now asserted directly,
  `--clear-cache` is verified against a seeded sentinel row with a
  no-flag control run, and each error case asserts its specific diagnostic. The
  tests also pin `--cache-db-path` under `tmp_path`; they previously wrote a
  multi-megabyte `clm_cache.db` into the working tree and shared it across xdist
  workers.
