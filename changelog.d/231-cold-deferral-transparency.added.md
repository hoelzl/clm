- `clm slides sync` cold-start mint/adopt deferrals are no longer an
  opaque count (#231): when the correspondence verifier rejects DE/EN
  slide pairs, the output (and `--json` via `apply.cold_deferrals`) names
  each rejected pair's index and both headings plus a
  `clm slides validate` hint, and verifier-unavailable / safe-abort /
  plan-error / race deferrals state their reason.
