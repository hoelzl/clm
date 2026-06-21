- **`clm slides sync` now localizes the "id-less localized cells edited on both
  decks" error (Issue #364).** The error previously carried `slide_id=None` and
  named no cell; it now pins to the drifted cell's owning slide group and echoes
  the offending cell's first line (per half), so the author can find it. The
  message also leads with the actual common cause — a stale watermark — pointing
  at `clm slides sync --rebaseline` rather than only the unhelpful "assign
  slide_ids" steer. `--explain` adds a note when the watermark-baseline diff
  errors, flagging that the baseline may be stale and to compare against
  `--baseline HEAD` / `--rebaseline` (a clean-looking diff that still errors is
  no longer a mystery).
