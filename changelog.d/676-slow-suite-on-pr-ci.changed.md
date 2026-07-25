- **The `slow` test tier now runs on every PR instead of nightly.** It joins the
  CI matrix as a fourth parallel suite, so it costs machine-minutes but no wall
  clock — the tier is 37 tests in ~78 s at `-n 4`, against a `unit` job of
  ~4.5 min and a Docker job of ~6.5 min. It was never excluded for cost. The
  nightly workflow is repointed accordingly: it now runs the **whole** suite,
  Docker tier included, against unchanged `master`, as a flake and rot detector
  — the one thing PR CI structurally cannot do. Its issue-filing mechanism moved
  into a `.github/actions/report-failure` composite action so other workflows
  can reuse it.
