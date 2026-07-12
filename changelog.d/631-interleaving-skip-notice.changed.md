- `clm slides normalize`: an explicit `--operations interleaving` on a
  language-split half (`*.de.py` / `*.en.py`) is no longer a silent no-op —
  the intentional skip (#611) is now reported as a `[SKIPPED]` line (a
  `notices` array in `--json`), without affecting status or exit code (#631).
