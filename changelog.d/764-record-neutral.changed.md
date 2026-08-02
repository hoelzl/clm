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

  Nothing that was previously mechanical becomes a question, and nothing that
  required judgement is now decided for you — a member failing any part of the
  test keeps exactly its old framing.
