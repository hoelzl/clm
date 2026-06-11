# Kernel Execution Telemetry & Triage (issue #330)

Status: implemented (first cut). Crash-prefix bisection (issue #330,
checkbox 4) is a deliberate follow-up — see "Deferred" below.

## Problem

The xeus-cpp / CppInterOp / clang-repl kernels crash in two distinct ways:

1. **Deterministic crashes** — cumulative-JIT failures that reproduce on
   every attempt (worked around in course specs with `evaluate="no"`).
2. **Transient flakes** — a deck dies once and passes on the next attempt.

The retry loop in `notebook_processor._execute_notebook_with_path`
(6 attempts, fresh kernel + preprocessor per attempt) hid both: a deck
that passed on attempt 3 looked identical to a clean pass, and nothing
recorded which class a final failure belonged to. `evaluate="no"`
workarounds were silent debt — nothing re-tested them after kernel
upgrades.

## Architecture

### Worker side (capture)

`_execute_notebook_with_path` records one entry per **failed** attempt:
`{attempt, failure_type, error_class, failing_cell_index, message}`.
Classification lives in `classify_execution_failure` (cell_execution_error
/ dead_kernel / startup_timeout / cell_timeout / other; kernel-level
failures are matched on jupyter_client's RuntimeError messages).
`summarize_execution_attempts` folds the list into a record with an
`outcome` (`passed_after_retry` / `failed` / `suppressed_failure`) and a
`classification`: `flaky`, `deterministic` (all attempts failed with the
same failure type at the same cell), or `mixed`.

The cell context (`_current_cell`) is cleared at the start of every
attempt so a startup timeout is never blamed on the previous attempt's
failing cell.

Two transport channels to the host (workers — possibly in Docker — never
open host databases):

- **Completed jobs** (flake or skip-errors-suppressed failure): a
  `ProcessingWarning` with category `execution_telemetry` and the record
  in `details`. This is the only structured worker→host channel that
  survives the jobs-table round trip for completed jobs.
- **Failed jobs**: the record is attached to the enhanced error as
  `error.execution_telemetry`; `worker_base` copies it into the
  structured error JSON (`error_info["execution_telemetry"]`), same
  pattern as `notebook_error_class` etc.

Clean first-attempt passes are deliberately NOT recorded (one write per
executed deck for no diagnostic value; absence == clean).

### Host side (persistence + surfacing)

`ExecutionTelemetryStore`
(`clm/infrastructure/database/execution_telemetry.py`) appends rows to
`clm_telemetry.db` — by default next to `clm_cache.db` but a **separate
file**, so cache clears never erase crash history. Global CLI option:
`--telemetry-db-path`.

`SqliteBackend` (given a `telemetry_store`):

- in `_extract_and_report_job_warnings`, intercepts the
  `execution_telemetry` category: persists the record, and for
  `passed_after_retry` calls `BuildReporter.report_flaky_file`. The
  record is NOT forwarded as a user-facing warning and NOT stored via
  `store_warning` (a cache hit replays no execution, so it must not
  replay telemetry).
- in the failed-job branch, parses the error JSON and persists
  `execution_telemetry` when present.

`BuildReporter` aggregates flakes per source file into
`BuildSummary.flaky_files` (`FlakyFileInfo`); the Default/Verbose
formatters render a "Flaky decks (passed only after retry)" section and
the JSON formatter always emits a `flaky_files` key.

### `clm kernel-triage`

Candidates = all `evaluate="no"` topics (from the loaded course) + all
decks with telemetry events inside `--since-days` (default 90; telemetry
paths no longer in the course are reported as stale, never re-run).

Re-execution reuses the production build path end to end: the command
writes a **triage spec** next to the original (so relative paths resolve)
with non-target topics removed, `evaluate` stripped from targets,
`<output-targets>` dropped, and emptied sections/subsections disabled —
then runs `clm build` as a subprocess (`-m clm`, precedent: `clm run`)
with throwaway output/cache/jobs paths and `--telemetry-db-path` pointed
at the real database. Fresh caches mean each deck executes exactly once
per language (kinds share the execution cache within one build). Outcomes
are classified from the build's JSON summary (`errors`, `flaky_files`)
plus telemetry rows recorded during the run window, and rendered as
per-deck recommendations ("workaround can be lifted" / "keep" / "still
flaky").

## Deferred

- **Crash-prefix bisection** (emit a minimal reproducer notebook for
  upstream xeus-cpp reports by executing cell prefixes `0..N` for
  decreasing N on deterministic crashes). The telemetry already records
  the failing cell index, which is the starting point for this.
- API-mode (Docker) per-cell heartbeats are unrelated but share the
  "worker cannot reach host DB" constraint; if a worker-side channel ever
  becomes richer than the result JSON, telemetry could ride it too.
