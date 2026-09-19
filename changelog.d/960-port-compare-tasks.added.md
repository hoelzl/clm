- Added the agent-first revision-history half of `clm harvest` (#960,
  umbrella #970): `harvest task --kind port|compare --source FILE` frames
  port/compare judgment per matched slide pair (deterministic
  `match_slides` pairing, no model, no video). Port answers are the
  standard `harvest-bullets` documents and land through the ordinary
  `harvest accept` write path (id-keyed, companion-aware — replacing the
  old index-keyed inline write); compare verdicts are validated by the new
  `harvest compare-accept` (freshness via file-content fingerprints) and
  written as the canonical compare-report JSON. Validators `harvest-bullets`
  (now accepting `kind: "port"`) and the new `harvest-compare` register in
  the shared kit's registry (#959). The embedded-LLM `port`/`compare` verbs
  remain until the autopilot quarantine slice.
