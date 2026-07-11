- `clm slides sync`: inserting a new id-keyed slide before a run of un-id'd
  positional shared cells on one half no longer frames the twin's untouched
  cells as mechanical `mirror_remove` rows (a plain `sync apply` deleted
  them). The suspected group split is now reframed as an answerless
  conflict with a `suspected_group_split` report observation; mirroring the
  inserted slide on the twin (e.g. answering its `translate_new`) and
  re-reporting resolves it losslessly. Pools carrying such an unresolved
  item are frozen during apply's ledger re-record so a landing sibling row
  cannot erase the removal evidence (#610).
