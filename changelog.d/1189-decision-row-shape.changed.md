- `clm slides sync apply --decisions`: a decision row with a field other than
  `key`, `action`, `choice`, `body` and `side` now refuses the document (exit 2,
  naming the row and the field) instead of being silently ignored, so a typo
  beside a valid answer is visible. Every `clm slides sync report --json` item
  now also carries `templates`: one ready-to-fill decision row per entry of
  `answers`, naming the field each answer needs (`choice`, or `body` plus
  `side` where required). `answers` is unchanged. Both engines (#1189).
