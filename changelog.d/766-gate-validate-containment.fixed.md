- **Sync write gate**: the whole-deck structural gate now labels its promoted
  `order-parity` violation `error` instead of passing it through as a
  `warning`. Everything the gate returns is blocking, so a caller that
  re-filtered its result on `severity == "error"` would have dropped the
  promoted violation and re-opened the order-divergence hole (#652) that #719
  closed. `clm slides sync verify` is unaffected — it reports `order-parity` as
  a warning as before, so pre-existing committed divergences still do not
  hard-fail CI.
- **Sync write gate**: pinned the containment relation between `clm validate`'s
  split-pair checks and the sync write gate — a pair `validate` reports an
  *error* on can no longer be recorded into the trust store without a test
  failing. The property holds across the full slide corpus today; it was
  previously unguarded, so nothing would have caught a future change that broke
  it.
