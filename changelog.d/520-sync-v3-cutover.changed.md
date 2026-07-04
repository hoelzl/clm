- **BREAKING** — `clm slides sync` now always runs the document-model engine
  (#520 Phase 4): the `CLM_SYNC_ENGINE` opt-in flag is gone, the committed
  per-topic ledger (`<topic>/.clm/sync-ledger.json`) is the only trust store,
  and the verb surface is exactly `report` / `apply` / `verify` / `record`.
  `report --since DATE|REF` stays as a read-only forensic view (now diffing
  the whole ≤4-file bundle at the resolved ref). Seed existing repos once with
  `clm slides sync record DIR` from a verified state; see `clm info migration`.
- `clm slides split` and `clm slides translate` record freshly-created pairs
  in the committed sync ledger instead of the removed watermark cache
  (`split --no-record` skips it; `--no-watermark` is kept as an alias, while
  `split --cache-dir` and `translate --provider/--llm-model` were removed).
  `translate` over an existing twin now reports the pair's sync state
  read-only instead of running a model-driven incremental sync.
- The Studio language lock and sync button run on the ledger engine: an
  unseeded pair locks both languages until `sync record`, and the sync
  subprocess applies only mechanical items (framed items keep the lock and
  need the agent loop).
- The MCP `slides_sync_report` tool returns the schema-3 member table
  (mechanical/framed actions with per-item `answers` vocabulary) instead of
  the v2 tiered report.
