- **Stale-watermark auto-heal now covers directory (batch) sweeps.** A writing
  `clm slides sync apply DIR` / `autopilot DIR` re-baselines each pair's
  stale-but-consistent watermark independently (the same safe git-HEAD-no-op gate as
  the single-pair path), instead of only the single-pair case. The batch rollup names
  how many were re-baselined and each `--json` pair entry carries `auto_healed`
  (#364 follow-up).
- **`clm slides sync --explain` shows the git-HEAD baseline side by side** with the
  watermark baseline when the two disagree — so a stale watermark (errors/conflicts
  vs the watermark, clean vs git HEAD) is visible at a glance rather than a
  clean-looking anchor diff that nonetheless errors (#364 follow-up).
- **The "id-less localized cells edited on both decks" error is now machine-readable.**
  Besides naming the offending cell's owning slide group in prose, the `report --json`
  issue item carries the offending cells' localized positions and bytes
  (`source_position` / `source_excerpt` / `source_line` for DE, `target_*` for EN), so
  an agent reads them straight from the report (#364 follow-up).
