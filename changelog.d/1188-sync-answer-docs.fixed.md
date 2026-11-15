- Sync docs: `apply --help` and `commands.md` show `report_id` in the
  decision-document shape. `remove_localized_side` and its answers are
  documented. `clm info sync-agents` has a table mapping each `answers` entry
  to its decision row, separates the title macro (a synced member) from
  `# %%` header attributes (`vo_anchor` is not synced by the default engine),
  and no longer contradicts itself on what `--dry-run` verifies (#1188).
