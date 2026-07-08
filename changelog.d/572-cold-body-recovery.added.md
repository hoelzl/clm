- **Inline stale-twin recovery on cold cells.** A two-sided `verify_cold` item
  on an **id-keyed** member now accepts a `body` answer in the sync decisions
  document, alongside a new `side` (`"de"`/`"en"`) field naming the twin to
  overwrite: `{"key": "id:x", "body": "…", "side": "de"}`. Previously the only
  answer was `confirm`, which banks **both sides as-is** — so a cell that fell
  cold with a *stale* twin (e.g. its source was edited while the ledger was
  cold) could only be fixed by hand-editing the file and then confirming.
  Supplying the corrected twin inline makes cold recovery a one-pass operation,
  consistent with `translate_edit`. Positional cold members (no addressable id)
  still take only `confirm` — mint a `slide_id` first if their twin is stale.
  See `clm info sync-agents` / `clm info commands`. (#572)
