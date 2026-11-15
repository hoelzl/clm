- `clm slides sync apply --decisions` accepts a `replace` answer,
  `{"key": "id:x", "replace": [["old", "new"], ...]}`: a `body` given as edits
  of the target cell's current text, for small changes to an existing twin.
  Pairs apply in order and each `old` must match exactly once (overlapping
  matches count). Otherwise the row is rejected and none of it is written.
  The answer is offered wherever `body` is on `translate_edit` (no `side`) and
  on id-keyed `verify_translation` / `verify_cold` (`side` required), and it
  goes through the same guards as `body`. `report --json` lists it in
  `answers` and `templates`. Both engines; on the sequence core it applies to
  `translate_edit` (#1171).
