```added
- `clm recordings status/jobs/backends/check --json`: machine-readable
  output for the recordings read side (#965, surface half). Job rows are
  full-fidelity (full ids, untruncated messages, full input/output paths);
  `jobs poll --json` streams one compact JSON document per tick (JSON
  Lines, `--watch` honored in both modes), `jobs wait --json` emits a
  single `{outcome, job}` document, and the load-bearing exit codes
  (0 ok / 1 failure / 2 timeout) are preserved in JSON mode — including
  an already-failed `wait` target. Failure diagnostics go to stderr so
  stdout stays parseable, and `jobs prune --json` requires `--yes` (the
  interactive prompt cannot be kept off stdout). The `recordings report`
  / acknowledgement verbs remain planned, gated on the #907
  drift-semantics design.
```
