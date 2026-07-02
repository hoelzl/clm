- `clm slides normalize --stamp-ids` — the one-time sync-v3 id normalization
  (#520 Phase 0): stamps a `slide_id` onto every id-less localized cell and
  gives every voiceover/notes narrative its **own unique** content-slug id
  (re-pointing legacy inherited-owner and placeholder ids). EN-authority and
  pair-atomic: split decks are stamped through the unified pair so
  `de_id == en_id`; cells without a directly-adjacent DE/EN twin are refused
  as review items, never half-stamped. Voiceover companion files are stamped
  together with their deck pair (own ids on the companion cells,
  `for_slide`/`vo_anchor` untouched, one id namespace across the deck +
  companion files). Shared language-neutral cells are never stamped.
  `clm validate` now accepts both narrative-id conventions — the legacy
  inherited form and a unique own id — while still flagging a narrative id
  that duplicates another cell's id (the stale copy-paste case) and
  divergent ids on a DE/EN narrative twin pair.
