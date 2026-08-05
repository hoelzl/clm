- **Re-layering prerequisites (#801, remediation Phase 7 / D11) — the gate on
  the #802 re-layering is in place.** Four pieces: a golden double-build
  characterization suite (`tests/e2e/test_e2e_golden_build.py` — the rich and
  the minimal reference courses each built twice from scratch and byte-compared
  via `--snapshot`/`--verify-against`; acceptance met with two consecutive
  green runs on unchanged code); executable layer-boundary contracts
  (`tests/test_architecture_contracts.py` — the complete 50-edge inventory (40 files)
  of today's layering violations over the documented layer stack as a
  shrink-only ratchet, plus pins on the
  `Backend` surface and the worker payload schemas); real unmocked
  build-pipeline tests in the fast suite
  (`tests/build/test_pipeline_unmocked.py` — a data-only course through the
  real stages and a PlantUML job round-tripping a real queue on a temp DB);
  and per-module coverage floors on everything Phase 8 moves
  (`scripts/check_coverage_floor.py`, checked in CI's unit job). Remediation
  Phases 4–8 now have tracking issues (#798–#802).
