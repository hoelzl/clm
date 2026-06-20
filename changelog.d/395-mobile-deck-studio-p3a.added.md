- **Mobile Deck Studio P3a — bilingual lock.** The phone authoring surface
  (`clm serve --spec`) now shows a **language toggle** for split DE/EN deck
  pairs and derives a **watermark-based lock**: a language is editable only when
  its twin half is clean relative to the last `clm slides sync` baseline, so
  edits on one side mark the other stale and lock it (surfaced as a banner +
  stale badge) until a sync reconciles them. Locked writes return **423**. The
  lock is read-only and LLM-free; in-app *Discard* and *Sync-to-other-language*
  follow in P3b. (#395)
