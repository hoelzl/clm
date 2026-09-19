- **Retire top-level embedded-model harvest history verbs (#960).** `harvest
  port`, `compare`, `backfill`, `compare-from-inventory`, and `sync-at-rev`
  are removed without aliases; legacy execution is available only under
  `harvest autopilot`. MCP's `harvest_backfill_dry` is also removed because
  its dry run still invoked embedded models. The migration guide and
  `clm info harvest-agents` describe the agent-driven replacements.
