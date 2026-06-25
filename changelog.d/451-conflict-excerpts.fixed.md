- **`clm slides sync report` now carries both current cells on a keyed `conflict`.**
  A baseline-relative conflict (both halves changed since the baseline) previously
  reported empty `source_excerpt` / `target_excerpt`, forcing an agent to re-open
  and re-parse every `.de.py` / `.en.py` to tell a *false* conflict (a consistent
  bilingual edit) from a genuine one. The conflict's current DE cell (`source_*`)
  and EN cell (`target_*`) are now populated — resolved by the conflict's
  `slide_id` (robust to the position-scheme used for id-less cells), with a
  remove-vs-edit conflict carrying only the surviving side. Flows through `clm
  slides sync report --json`, the `slides_sync_report` MCP tool, and `task` /
  `accept` framing. (#451)
