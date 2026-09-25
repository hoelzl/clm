- `clm slides sync apply` no longer withholds the ledger record of every
  landed member when the structural verify fails somewhere else in the pair
  (#992). The post-write gate is now scoped per slide: a violation attributed
  to a slide (an `id-asymmetry` from a hand-removed slide whose removal is
  still a pending question, an orphaned voiceover companion cell) withholds
  only that slide's group — its rows keep their file writes and old baseline,
  carry a `(recording deferred: the structural verify failed on this member's
  slide …)` reason suffix, and are listed in the new `verify_withheld` JSON
  field — while every other landed member records in the same pass. A
  deck-wide violation (`unify`, `order-parity`, an unprojectable companion
  layout) still withholds everything. Before, one such violation re-framed
  every freshly applied body as `verify_translation` on the next report and
  cost a full `confirm` round. `ledger_recorded` can now be `true` beside a
  non-empty `verify_violations`.
