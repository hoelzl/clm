# Recordings: Job Progress UX and Reconciliation

**Status**: Draft for discussion
**Author**: Claude (Opus 4.7) + TC
**Date**: 2026-04-17
**Scope**: `src/clm/recordings/workflow/job_manager.py`, `backends/auphonic.py`, `backends/auphonic_client.py`, `src/clm/recordings/web/`

---

## 1. Problems

Two distinct pain points with the Processing Jobs pane, both observed on the Auphonic backend:

### 1.1 UI appears frozen for minutes at a time

During a long Auphonic job, the dashboard shows no change for several minutes even though work is happening. Three contributors, in rough order of impact:

1. **Polling gaps.** `AUPHONIC_POLL_INITIAL_SECONDS = 30` (`auphonic.py:54`). Between the end of the upload phase and the first poll-based status update, 30 seconds pass with no new events. After 30 minutes the gap grows to 5 minutes (`AUPHONIC_POLL_LONG_SECONDS = 300`).
2. **Upload progress is chunk-granular.** `DEFAULT_UPLOAD_CHUNK_SIZE = 8 MiB` (`auphonic_client.py:46`). On a slow uplink, each chunk is many seconds, and the progress bar only ticks when a chunk completes.
3. **No elapsed-time heartbeat.** Between state transitions the job's `message` stays constant, so even when the poller runs it doesn't push a new SSE payload that looks different to the user.

### 1.2 Jobs get stuck in a FAILED state even when Auphonic finished the work

Concrete incident: user stopped the CLM server while an Auphonic job was processing. On restart the job showed up as FAILED, but Auphonic had actually completed the production on its end. Two code paths contribute:

- `JobManager.__init__` (`job_manager.py:159`) transitions any `UPLOADING` job to FAILED with "Upload was interrupted by a process restart." If the upload actually *completed* before the crash, the production is fine upstream but we've already marked the job failed.
- `_has_timed_out` (`auphonic.py:509`) fails any `PROCESSING` job whose `started_at + 120 min` is in the past. If the server was down for more than 120 minutes, every in-flight job fails on the first poll after restart — regardless of what Auphonic actually thinks.

There is no reconciliation path today: the `AuphonicClient` has no `list_productions`, the backend has no `reconcile_job` method, and the UI has no "verify state" action.

## 2. Goals

1. Make the UI feel alive during Auphonic processing — every phase should have at least one visible tick per 5–10 seconds.
2. Give the user a one-click way to reconcile any job's displayed state against reality (upstream API + local filesystem).
3. Stop auto-failing jobs whose backend work is actually fine.
4. Make the reconciliation protocol generic so audio-first backends (ONNX, External, RX 11) can use it too.

## 3. Non-goals

- Webhook-based Auphonic updates. Polling is fine; we just need it to be tighter and more generous about errors.
- A full job-orchestration rewrite. This is an additive set of improvements to the existing `JobManager` / backend Protocol.
- Retroactive fixing of jobs marked FAILED before this work lands. The reconcile action will naturally repair them on-demand.

## 4. Live progress updates

### 4.1 Time-based upload progress callback

Change `AuphonicClient.upload_input` (`auphonic_client.py:382`) so `on_progress` fires on a time cadence *in addition to* on chunk boundaries. Implementation sketch:

```python
UPLOAD_PROGRESS_MIN_INTERVAL = 0.25  # seconds

last_report = time.monotonic()
for chunk in _read_chunks(path, self._chunk_size):
    ... upload the chunk ...
    sent += len(chunk)
    now = time.monotonic()
    if on_progress is not None and total_size > 0:
        if (now - last_report) >= UPLOAD_PROGRESS_MIN_INTERVAL or sent == total_size:
            on_progress(min(sent / total_size, 1.0))
            last_report = now
```

No chunk-size change (8 MiB stays, throughput-friendly). The callback now fires at least 4× per second, which is what the UI needs.

If we want sub-chunk progress (useful for slow uplinks where a single chunk takes 20+ seconds), we can switch the inner write to a streaming callback on `httpx` — but that's a second-order optimisation; the time-gate is the high-leverage change.

### 4.2 "Poll soon" signal to kill the post-upload gap

Today `JobManager._poller_loop` uses `self._stop.wait(self._poll_interval)`. The `Event` is only ever set on shutdown. Add a second trigger:

```python
self._wake = threading.Event()

def _poller_loop(self) -> None:
    while not self._stop.is_set():
        self.poll_once()
        # Wait for either the normal interval, an explicit wake, or shutdown.
        self._wake.wait(self._poll_interval)
        self._wake.clear()

def request_poll_soon(self) -> None:
    """Wake the poller to run again on the next scheduler tick."""
    self._wake.set()
```

Expose `request_poll_soon` on `JobContext` so backends can call it. In `auphonic.py:submit`, call `ctx.request_poll_soon()` right after the transition to `PROCESSING` — the next poll happens immediately instead of waiting up to 30 seconds. The Auphonic API returns status in <1 second, so the first user-visible update arrives within ~1 second of upload-complete.

### 4.3 Elapsed-time heartbeat in job messages

In `AuphonicBackend._message_for` (`auphonic.py:524`), include time spent in the current phase:

```python
@staticmethod
def _message_for(production: AuphonicProduction, job: ProcessingJob) -> str:
    elapsed = ""
    if job.started_at is not None:
        delta = datetime.now(timezone.utc) - job.started_at
        elapsed = f" — {_humanize_duration(delta)}"
    status = production.status_string or f"status {production.status}"
    return f"Auphonic: {status}{elapsed}"
```

Each poll publishes a new message even if Auphonic's status didn't change. The SSE queue gets an event, the dashboard row re-renders, and the user sees "Auphonic: Audio Processing — 3m 47s" ticking up. Trivial change, big perceptual effect.

### 4.4 UI partial wiring

Make sure `partials/jobs.html` renders the per-job progress bar *and* the current message, and that the SSE handler triggers a refresh on `event:job` (not just on generic `state_changed`). Audit during implementation.

## 5. Manual reconciliation

### 5.1 Generic backend method

Extend the `ProcessingBackend` Protocol (`backends/base.py`) with an optional `reconcile`:

```python
def reconcile(self, job: ProcessingJob, *, ctx: JobContext) -> ProcessingJob:
    """Verify a job's state against upstream reality and the filesystem.

    Called by the JobManager when the user requests a status check on a job.
    Must be safe to call on any state (including terminal states — a FAILED job
    whose work actually completed upstream should be resurrected).
    """
```

Default implementation (for backends that don't implement it): check the local filesystem for `final_path` existence and, if present, mark COMPLETED. This is already useful for audio-first backends that may have been interrupted mid-assembly.

### 5.2 Auphonic implementation

```python
def reconcile(self, job: ProcessingJob, *, ctx: JobContext) -> ProcessingJob:
    # 1. Check local filesystem first — cheapest win.
    if job.final_path.exists() and job.final_path.stat().st_size > 0:
        if job.raw_path.exists():
            # Raw still sitting in to-process/ — finalize: archive raw, mark COMPLETED.
            self._archive_raw(job)
        job.state = JobState.COMPLETED
        job.progress = 1.0
        job.message = "Recovered: final already on disk"
        job.completed_at = job.completed_at or datetime.now(timezone.utc)
        ctx.report(job)
        return job

    # 2. If we have an upstream UUID, query Auphonic directly.
    if job.backend_ref:
        try:
            production = self._client.get_production(job.backend_ref)
        except AuphonicHTTPError as exc:
            if exc.status_code in (404, 410):
                self._fail(job, f"Auphonic production {job.backend_ref} no longer exists", ctx)
                return job
            raise  # transient — caller surfaces the error

        if production.status == AuphonicStatus.DONE:
            # Production is done — download and finalize.
            return self._finalize(job, production, ctx)
        if production.status == AuphonicStatus.ERROR:
            self._fail(job, production.error_message or "Auphonic reported ERROR", ctx)
            return job
        # Still in-flight on their side. Resurrect from FAILED/UNKNOWN to PROCESSING.
        job.state = JobState.PROCESSING
        job.error = None
        job.last_poll_error = None
        job.message = self._message_for(production, job)
        job.progress = self._progress_for(production.status, job.progress)
        ctx.report(job)
        return job

    # 3. No UUID — try to find the production by title.
    title = self._title_for(job.raw_path)
    candidates = self._client.list_productions(title=title, since=job.created_at)
    if len(candidates) == 1:
        job.backend_ref = candidates[0].uuid
        ctx.report(job)
        # Recurse: branch 2 will pick it up next.
        return self.reconcile(job, ctx=ctx)
    if len(candidates) > 1:
        job.message = f"Multiple Auphonic productions match '{title}' — resolve manually"
        ctx.report(job)
        return job
    # No match — leave the job in its current state; nothing to recover.
    return job
```

This handles the Auphonic-finished-while-server-was-down case, the user's motivating incident.

### 5.3 `AuphonicClient.list_productions`

Add a thin wrapper around `GET /api/productions.json` (an endpoint that already exists in the Auphonic API). Parameters: `title` for filtering (Auphonic supports title search), `since` to cap the result set. Return a list of `AuphonicProduction` objects (the existing model).

### 5.4 `JobManager.reconcile`

```python
def reconcile(self, job_id: str) -> ProcessingJob | None:
    """Run the backend's reconcile hook for *job_id*.

    Works on any state (including terminal). Returns the updated job.
    """
    with self._lock:
        job = self._jobs.get(job_id)
    if job is None:
        return None
    ctx = self._make_context()
    updated = self._backend.reconcile(job, ctx=ctx)
    self._store_job(updated)
    return updated
```

### 5.5 Web route and UI

```
POST /jobs/{id}/reconcile → calls JobManager.reconcile → returns updated jobs partial
```

UI: each row in the Processing Jobs table gains a "↻ Verify" button. Disabled briefly while a verify is in flight. Outcome is visible through the existing SSE job-event stream.

## 6. Stop auto-failing healthy jobs

### 6.1 Soften the hard timeout

`AUPHONIC_POLL_TIMEOUT_MINUTES = 120` currently fails any job older than 2 hours. Change the semantics:

- Keep the constant, but rename: `AUPHONIC_STALE_WARN_MINUTES`.
- A job older than this is flagged as "stale" (new field `job.stale: bool`), *not* failed.
- The UI renders stale jobs with a warning badge and a prominent Verify button.
- Jobs are only marked FAILED on explicit upstream ERROR, upstream DELETED, or persistent permanent errors (401/403).

If we want a hard cap as a safety net (e.g. 7 days), add `AUPHONIC_HARD_GIVEUP_DAYS = 7` with the current fail-the-job behavior.

### 6.2 Soften UPLOADING → FAILED on restart

Today `JobManager.__init__` (`job_manager.py:164`) unconditionally fails UPLOADING jobs. Replace with:

```python
if job.state == JobState.UPLOADING:
    if job.backend_ref:
        # Production exists upstream; try reconcile on first poll tick.
        job.state = JobState.PROCESSING
        job.message = "Resumed after restart — checking Auphonic"
    else:
        # No production created yet — upload never made it past step 1.
        job.state = JobState.FAILED
        job.error = "Upload was interrupted before the Auphonic production was created."
```

This handles the case where the crash happened mid-upload but after the production was created: reconcile will either find the upload complete (Auphonic kept it) or failed, and `poll` will surface the right state.

## 7. Tests

In `tests/recordings/test_job_manager.py`:

- `test_request_poll_soon_wakes_the_loop`
- `test_reconcile_routes_to_backend`
- `test_reconcile_on_terminal_job_can_resurrect_from_failed`

In `tests/recordings/test_auphonic.py` (new or extended):

- `test_reconcile_with_local_final_marks_completed`
- `test_reconcile_uses_backend_ref_when_present`
- `test_reconcile_resurrects_failed_job_when_upstream_done`
- `test_reconcile_finds_production_by_title_when_uuid_missing`
- `test_reconcile_respects_http_404_as_deleted`
- `test_message_for_includes_elapsed`

In `tests/recordings/test_auphonic_client.py`:

- `test_upload_input_reports_progress_on_time_interval`
- `test_list_productions_filters_by_title`

In `tests/recordings/web/test_routes.py`:

- `test_reconcile_route_returns_updated_partial`
- `test_reconcile_requires_known_job_id`

## 8. Additional robustness hooks (smaller, adjacent issues)

These came up during the analysis and are worth folding into this work because they share the same files.

### 8.1 OBS reconnect loop

`ObsClient.connect` currently exits after a single failure. Add an optional `auto_reconnect: bool = False` mode that runs a background watchdog: ping OBS every N seconds via `get_record_status`; on exception, mark the client disconnected and retry `connect` with exponential backoff. SSE events for `obs_connected` / `obs_disconnected` so the UI can show a warning when the EventClient silently dies (current symptom: arming and OBS events both appear to work but no events ever fire because the socket is dead).

### 8.2 Rename-thread timeout

`RecordingSession._wait_for_stable` (`session.py:423`) polls file size forever. If OBS is stuck with an open handle (rare but observed with hardware encoder stalls), the session wedges in `RENAMING`. Add a total timeout (default 10 minutes): on expiry, move the OBS output to `superseded/` with a clear reason, clear `_armed`, push an error event. User can then retake.

### 8.3 `superseded/` retention

Add `clm recordings prune-superseded --older-than=30d` CLI command. Not automatic — users who are still debugging a course may want the history for a while.

## 9. Implementation order

1. **Upload progress time-gate** (§4.1). Tiny change, immediate UX improvement. Ship.
2. **"Poll soon" signal** (§4.2) + **elapsed-time message** (§4.3) + **UI partial audit** (§4.4). Ship.
3. **Generic `reconcile` backend method** (§5.1) + **Auphonic implementation** (§5.2, §5.3) + **web route and UI** (§5.4, §5.5). Ship.
4. **Soften hard timeout and UPLOADING-on-restart** (§6). Ship.
5. **(Optional)** §8 additions as they're needed.

Each step is independently testable and independently useful.

## 10. Open questions

- **Should `reconcile` be exposed on audio-first backends in this pass?** The generic default (check `final_path` existence) is enough for most recovery cases. ONNX-specific reconciliation (e.g. "was the denoise step partial?") can wait until someone actually needs it.
- **Should we add a "reconcile all" button to the dashboard?** Tempting, but for a user with many jobs it's a lot of API calls. My lean: per-row only, with a CLI equivalent `clm recordings jobs reconcile --all` for bulk recovery.
- **Do we want per-backend config for the poll cadence?** Today the Auphonic cadence is hardcoded. Making it configurable adds surface area; the "poll soon" signal covers the current motivating case without exposing a new knob. Keep it hardcoded.
