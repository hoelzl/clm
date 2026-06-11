- New `clm cache explain SOURCE_FILE --spec SPEC` (#328): read-only, per-deck
  view of the execution-cache key components (hashed topic siblings, template
  fingerprint, worker image identity, execution flags), the resulting hashes,
  and the hit/miss state of every cache layer with stored-at timestamps,
  ending in a per-artifact "replays / skips / will execute" verdict — the
  one-screen answer to "why did this deck replay stale output?" that the
  #321 diagnosis lacked.
