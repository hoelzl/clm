- `verify_translation` now accepts a **`body` + `side`** answer, not only
  `confirm`. Both sides moved off base; when your review finds one of them
  wrong you can replace it in the same pass instead of hand-editing the file
  and re-reporting. (`side` is required here — both sides moved, so the engine
  cannot infer which one you corrected.) `clm info sync-agents` documented this
  answer before the engine accepted it; that contradiction is gone.
- `fork_pending_twin` gains the **`mark_twin`** answer. Turning a shared cell
  into a localized pair left the twin's `lang=` attribute to a hand edit —
  which is precisely the operation the doctrine forbids ("never hand-edit the
  other language"), so the flow's only route was the one it prohibits. The
  engine now writes the attribute; the body adaptation stays yours, framed as
  `translate_edit` on the next pass. The two-pass fork recipe is documented in
  `clm info sync-agents` for the first time, including why doing both steps in
  one edit drops the member's ledger history.
