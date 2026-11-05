- Recording provenance now finds the build manifest of a spec whose
  `<output-targets>` live outside `output/<tier>` (e.g. `dist/shared` or
  `output/<spec>/shared`). Both the recording-time `slide_digest` stamp and
  `clm recordings report --spec-file SPEC` (without `--source`/`--manifest`)
  used to search only the `output/` convention, so slide provenance for such
  specs was silently unknown. The lookup now resolves the spec's targets with
  the build's own path rules and reads that spec's
  `.clm-manifest.<spec>.json` from the first target that has one, trying
  targets that build a public kind (`code-along`/`completed`/`partial`) before
  private ones, each group in spec order. Specs without `<output-targets>`
  keep the `output/` lookup (#1107). A spec with explicit targets under
  `output/` whose alphabetically-first target is not its first public one now
  reads a different target's manifest, so parts stamped before this change
  may show `built output changed`; the flag remains a secondary hint.
