- **`clm slides sync report`** now emits a deck-level `uniform_drift_side`
  observation when *every* `translate_edit` item drifted on the same language
  half — the shape a review-after-translate pass produces. Each row on its own
  reads "the en variant was edited — adapt the twin", so a report with thirty of
  them looks like thirty requests to rewrite the other language, when the single
  answer that resolves them is usually `keep_twin`. The observation names the
  drifted side and both readings (bank the reviewed half with `keep_twin`, or
  supply adapted bodies for the twin), and counts any `verify_translation` rows
  so the summary is not mistaken for a blanket instruction. It appears in the
  JSON envelope and in the text report, below the items it summarizes.
- **`clm slides sync report`**: `translate_edit` item details now name the
  `keep_twin` answer alongside "adapt the twin".
