- **Sync v3 Phase 3 (#520): per-item apply, the `record` verb, and the
  member-keyed committed ledger — behind `CLM_SYNC_ENGINE=v3`.** The committed
  per-topic `.clm/sync-ledger.json` is promoted to the v3 trust store (schema-2
  envelope; the v1 sections coexist untouched, and the v2 engine round-trips
  the v3 `decks` section verbatim): per-member entries record langness, layout,
  per-side fingerprints, tags, provenance, and a hash version that lazily
  invalidates on hashing changes. `clm slides sync apply` (under
  `CLM_SYNC_ENGINE=v3`) executes every mechanical diff row deterministically
  and resolves framed items from a per-item validated `--decisions` JSON
  document (multi-cell smuggling and stale handles rejected individually, valid
  answers land regardless), re-parses the mutated bundle before writing, writes
  the ≤4 files atomically, and records each landed item into the ledger; the
  new `clm slides sync record` verb (bless/accept collapsed) banks a verified
  deck's state wholesale or per member, gated on the structural verify and
  performing the pos→id key migration at record time. Engine dispatch is a
  single verb-layer switch (`CLM_SYNC_ENGINE`, v2 default), with the schema-3
  JSON envelope keeping `is_clean`/`needs_model`/`needs_agent` stable.
