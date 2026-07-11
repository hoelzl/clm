- **Sync: a one-sided tag edit can no longer be silently banked by resolving
  the body row it coincides with** (#615). Previously a tag change folded into
  a `verify_translation` / `translate_edit` resolution (or a `verify_cold`
  confirm) left the twins' tag sets diverged, with `sync report` silent while
  `clm validate` kept warning. Tag parity is now a first-class diff aspect:
  a one-sided tag move co-frames a mechanical `mirror_tags` row next to the
  body row; a divergence with no attributable direction — both sides' tags
  moved apart, or a divergence already banked in a ledger — frames a new
  `conflict_tags` action (answer `de`/`en`; mirrors only the chosen side's
  tag set onto the twin, bodies untouched); `confirm` on `verify_translation`
  and `verify_cold` is rejected while the tag sets diverge; `apply` no longer
  records a member that still carries an unresolved sibling item — deferred
  record-only rows report the new `deferred` status (in the `--json` counts),
  deferred file-mutating rows keep `applied` with a
  "recording deferred" reason suffix; and
  `clm slides sync verify` now surfaces cross-side tag-parity mismatches as a
  warning (never failing the structural gate).
