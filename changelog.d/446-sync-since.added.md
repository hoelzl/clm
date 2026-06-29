- **`clm slides sync report` / `apply` / `task` / `accept` / `autopilot` gain
  `--since DATE|REF`.** Resolves a *timeframe* to a baseline commit instead of forcing
  you to hand-resolve a git ref: a git ref is used verbatim (an alias for
  `--baseline`), while a date or relative time (`"2 days ago"`, `2026-06-21`) resolves
  to the last commit at/before that instant (what was `HEAD` then), so a week of
  committed single-language edits is diffed correctly. Try-ref-first, so a branch/tag
  literally named like a date is treated as a ref. The chosen commit is echoed to
  stderr and surfaces as the plan's `git:<sha>` baseline source. Mutually exclusive
  with `--baseline` / `--baseline-from` (and, on `autopilot`, `--rebaseline` /
  `--verify`); works over a directory exactly as `--baseline` does. Pure sugar over
  `--baseline` — no engine change. (#446)
