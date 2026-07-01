- **`clm build --explain-rebuilds` logs why each deck missed the build cache
  and is being rebuilt.** When many decks rebuild whose sources should not have
  changed, this names the cause per deck: `no cache entry` (never built with
  this cache, or the cache was cleared), `content hash changed` (the source
  text or one of its dependencies differs — with the cached vs. current hash),
  or `no cache entry for this output target` (a new kind/format/language). Off
  by default so a normal build pays nothing — the extra read-only probe runs
  only on a miss and only when the flag is set. Per-deck reasons always go to
  the log file and, under `-O verbose`, to the console; the build summary also
  gains an aggregated **Rebuild reasons** breakdown (count per reason, most
  frequent first — and a `rebuild_reasons` object under `-O json`) so the
  dominant cause of unexpected rebuilds is visible at a glance. Also settable
  via `CLM_EXPLAIN_REBUILDS={1,true,yes,0,false,no}`. Complements
  `clm cache explain FILE --spec SPEC`, which gives a full per-artifact,
  per-cache-layer breakdown for a single deck.
