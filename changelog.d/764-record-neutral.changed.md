- **`clm slides sync`** no longer asks you to verify what it can see for itself.
  A member with no ledger entry used to be framed as a question purely because
  both halves were present — including cells the two halves share *byte for
  byte*, where there is no translation divergence to verify and the question has
  one possible answer. Such members now resolve as a new mechanical
  `record_neutral` row that writes **no file bytes**, only the ledger entry
  (provenance `structural`). On the reference corpus this removes **13,059 of a
  cold start's 28,791 items — 45.4%**.

  It fires only when the engine can check the claim rather than trust it: no
  ledger entry, both halves present, declared language-neutral (no `lang=`), of
  kind `code` or `j2`, and agreeing on every field the differ compares.

  **Prose is deliberately excluded**, even when the halves are byte-identical: a
  genuinely language-neutral `markdown` cell and German prose duplicated onto the
  English side look the same to the tool, and auto-blessing the second would bank
  an untranslated cell as in-sync. Those stay `verify_cold` for a human to judge.

  That boundary is a **base-rate trade, not a guarantee**: about 0.6% of the
  members this now records (83 of 13,049 on the reference corpus, across 41 of
  730 decks) carry German inside a comment or string literal, so they are
  untranslated cells in the English deck too. Code cells are auto-recorded
  because prose is *rare* there, not because it is impossible — the tool
  compares the two halves, it does not read them.

  Nothing that was previously mechanical becomes a question, and nothing that
  required judgement is now decided for you — a member failing any part of the
  test keeps exactly its old framing.
