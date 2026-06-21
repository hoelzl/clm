- **`clm slides reconcile-vo-ids` polish (Issue #403).** Directory mode now warns about
  stranded `.de`/`.en` halves with no twin (instead of silently reporting only the
  healthy pairs), matching `clm slides sync`; a usage error under `--json` is now emitted
  as a `{"error": …}` envelope rather than plain text; two explicit halves spelled with a
  mix of absolute and relative paths are now accepted (they are resolved before the
  same-deck check); and the `--dry-run` report uses future-tense wording and always
  surfaces the unpaired-narrative count, even for an already-symmetric pair.
