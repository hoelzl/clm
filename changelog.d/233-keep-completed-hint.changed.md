- `clm validate`: the `'completed' tag without a preceding 'start' cell`
  error now points at a `keep`-tagged preceding code cell when present
  ("did you mean 'start'?") — the recurring incremental-build mis-tag
  found during cold-start conversions (#233 item 4b).
