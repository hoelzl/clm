- A build whose stage processing raised (e.g. "No workers available") no
  longer prints "✓ Build completed successfully" with a clean summary while
  the exception propagates (#596). The summary now shows "✗ Build aborted"
  (JSON output: `"status": "aborted"` plus an `aborted` flag), the failure is
  counted as a fatal infrastructure error, and — because errors were
  recorded — the stale-output sweep skips itself instead of running against
  the aborted build's incomplete write registry.
