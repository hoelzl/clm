- `clm slides sync apply`: a `body` + `side` answer on an id'd localized member
  that frames no question is now the German-only fix: it writes the named half
  and records the member (a `rewrite_side` result row), on both the default
  engine and the sequence core. It used to report `already_applied` with exit
  0 and write nothing. A `body` on such a member is `already_applied` only when
  the cell already reads as that body; without `side`, or on a shared cell, an
  id-less handle or a one-sided member, it is `rejected` with the reason. An
  answer that only a mechanical row on its handle matched is no longer
  dropped silently, and on the sequence core a rejected answer now exits 1
  (#1182).
