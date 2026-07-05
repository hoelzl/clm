- `clm slides sync apply` can now create a missing separated **EN voiceover
  companion** from a `translate_new` decision. A one-sided (DE-only) companion
  — the state `clm harvest accept` leaves when the EN twin is deferred — is
  framed `translate_new` by `report`, but `apply` previously rejected every
  member with `the en source cell of id:<slide-id> is missing`: the executor
  derived the translation *target* from the framed item's `side`, whose meaning
  differed between the reporters (a *standing* one-sided member reports the
  *missing* side, a fresh add reports the *present* side), inverting the
  direction. `translate_new` now mints the absent twin from the member itself,
  so answering with the EN `body` writes `voiceover/voiceover_x.en.py` (minting
  the shared `slide_id`/`for_slide`) and leaves the EN deck untouched — the
  documented harvest → sync handoff closes through the ordinary loop instead of
  requiring the companion to be hand-authored ([#570](https://github.com/hoelzl/clm/issues/570)).
