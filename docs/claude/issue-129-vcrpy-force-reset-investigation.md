# Issue #129 Investigation: vcrpy `force_reset()` race

Investigation date: **2026-05-25**
Issue: <https://github.com/hoelzl/clm/issues/129>
Reproducers: `C:\Users\tc\Programming\Python\Tests\clm-bug-repros\issue-129-vcrpy-force-reset-race\`
Status: **workaround #2 shipped 2026-05-25 in
`src/clm/workers/notebook/notebook_processor.py`
(`_HTTP_REPLAY_BOOTSTRAP_TEMPLATE`).** Tests:
`tests/workers/notebook/test_notebook_processor.py::TestBootstrapDurability::test_bootstrap_scopes_vcr_force_reset_to_urllib3`
and `…::test_bootstrap_force_reset_does_not_strip_httpcore_patch`.

**Remove the workaround** when vcrpy ships a scoped `force_reset()`
upstream (track `kevin1024/vcrpy`). Search for `_clm_scoped_reset_patchers`
to find the block.

## TL;DR

Under `clm build --ignore-cache --http-replay=new-episodes`, a small
fraction of LLM HTTP calls hit the real upstream API but never land in
the cassette. The issue lists two hypotheses — httpcore stub install
ordering, and connection pool reuse — and neither is correct. The actual
root cause is:

**`vcr.patch.force_reset()`** — a context manager vcrpy's urllib3 stub
opens on every connection setup — temporarily **un-patches every vcr
stub including `httpcore.ConnectionPool.handle_request`**. The un-patch
is global, not scoped to urllib3, and not thread-safe. When LangSmith's
background thread (which uses `requests`/urllib3) is in the middle of a
connection setup, the httpcore patch is gone for a few milliseconds. If
the foreground thread dispatches `pool.handle_request(req)` during that
window, the call resolves to the unpatched original and bypasses vcr
entirely.

Concretely: foreground LLM calls via httpx race with background
LangSmith trace uploads via requests. The race window is
`vcr/stubs/__init__.py:333,357`, and the global un-patch is
`vcr/patch.py:450-464`.

## What the issue claims vs. what I found

### Hypothesis 1 from the issue — refuted

> `langchain_openrouter` (via the speakeasy SDK underneath) constructs an
> httpx transport object before vcrpy's `httpcore_stubs` patches the
> relevant function table.

This is incorrect. `httpx.Client` is created **inside** the `with
my_vcr.use_cassette(…)` block. More importantly, Python resolves
`pool.handle_request` via class-attribute lookup at every call, so even
a pre-constructed pool would pick up the patched method. I verified the
class attribute `httpcore.ConnectionPool.handle_request` had vcr's
wrapper ID at every observable snapshot inside the with-block.

### Hypothesis 2 from the issue — refuted

> The first call goes through the patched transport, the connection is
> reused, and the second call bypasses the interception layer when the
> connection is replayed.

This is also incorrect. Both LLM calls in my reproducer use the same
`httpcore.ConnectionPool` instance (`id()` matches). vcrpy patches
`ConnectionPool.handle_request`, not the per-connection objects, so
connection reuse cannot bypass it. (The vcr wrapper sits in front of
`pool.handle_request`, which sits in front of `connection.handle_request`.)

The issue's observation that "which call escapes varies by import
order" is correct, but the underlying mechanism is the
`force_reset()` race, not pool reuse.

### Actual root cause

`vcr/stubs/__init__.py` (the urllib3 stub) opens `force_reset()` twice
per HTTP request:

```python
# __init__:
with force_reset():
    self.real_connection = self._baseclass(*args, **kwargs)

# connect():
with force_reset():
    return self.real_connection.connect(*args, **kwargs)
```

These are protective wrappers around the construction and socket-connect
of the real underlying urllib3 connection, to keep `super().__init__()`
chains from re-entering vcr's patched classes.

`vcr/patch.py`'s `force_reset()` is implemented as:

```python
@contextlib.contextmanager
def force_reset():
    with contextlib.ExitStack() as exit_stack:
        for patcher in reset_patchers():
            exit_stack.enter_context(patcher)
        yield
```

where `reset_patchers()` yields `mock.patch.object` patchers that
restore the **original** value for every library vcrpy supports —
httplib, urllib3, botocore, httplib2, tornado, **and httpcore**:

```python
yield mock.patch.object(
    httpcore.ConnectionPool, "handle_request",
    _HttpcoreConnectionPool_handle_request,  # the original, un-patched
)
```

So while a urllib3 connection is being set up — including the
network-level socket connect, which can take 50–300 ms — every other
vcr stub is also un-patched globally on the class. Any thread that
dispatches through `httpcore.ConnectionPool.handle_request` during this
window hits the real httpcore, bypasses vcr's wrapper, and never
appears in the cassette.

## Why LangSmith is involved (but is not the bug)

LangSmith's main `Client` uses `requests` (urllib3) for trace uploads,
not httpx. Its background thread pumps the trace queue continuously.
Each upload involves at least one urllib3 connection setup, which means
each upload opens `force_reset()` for the duration of its connection.
With `LANGSMITH_TRACING=true`, the rate of `force_reset()` windows is
high enough to coincide with at least one of the few openrouter calls
per LLM cell.

Disabling LangSmith (`LANGSMITH_TRACING=false`) eliminates the bug —
not because LangSmith was the cause, but because the only other
concurrent urllib3 traffic in the build is gone.

## Evidence — reproduction summary

| Scenario | urllib3 traffic? | openrouter calls captured |
|---|---|---|
| Minimal reproducer with LangSmith on | yes (LangSmith BG thread) | 1 of 2 (intermittent — varies which) |
| Same reproducer with `LANGSMITH_TRACING=false` | no | 2 of 2 |
| openrouter SDK directly, no langchain | no | 2 of 2 |
| openrouter SDK + manual BG thread doing `requests.get(...)` | yes | 1 of 2 |
| openrouter SDK + manual BG thread doing `httpx.get(...)` | no (httpcore only) | 2 of 2 |
| Sequential `requests.get(...)` then 2× openrouter | yes, but no concurrency | 2 of 2 |
| Hold `vcr.patch.force_reset()` open on BG thread, fire `httpx.get(...)` on FG | n/a — direct | call escapes cassette |
| Replace `vcr.patch.force_reset` with `nullcontext` and re-run the BG-thread reproducer | n/a — bug removed | 2 of 2 |

The last two rows are the clinching evidence: holding `force_reset()`
open directly causes an httpx call to escape, and replacing it with a
no-op causes the bug to disappear under concurrent load.

## Why this is intermittent

vcr's class-level patching is correct in the steady state — Python's
method resolution finds vcr's wrapper, vcr's wrapper calls the original.
The bug exists only in the brief window where `force_reset()` has
swapped `httpcore.ConnectionPool.handle_request` back to the original.
Whether the bug triggers for a given LLM call depends on:

- whether a urllib3 connection setup is in progress on another thread
  at the moment the LLM call resolves `pool.handle_request`,
- which socket setups are slow (TLS handshake to a fresh host is slowest),
- and how many LangSmith uploads are queued.

This matches the issue's observation that the escape "varies depending on
what else has been imported earlier in the session" — import order
affects timing.

## Proposed paths forward

Three options in increasing order of effort. They are complementary;
(1) is for immediate Phase D unblock and (2) is for durable safety.

### 1. Recommended for immediate Phase D unblock — set `LANGSMITH_TRACING=false` for cassette rebuilds

A one-line env var change. The teaching-material build does not need
LangSmith tracing during cassette capture. Where to set it:

- For ad-hoc rebuilds: prefix the build command, or `setx
  LANGSMITH_TRACING false` for the session.
- For automated rebuilds: `clm build` could unset `LANGSMITH_TRACING`
  (and `LANGCHAIN_TRACING_V2`) in the worker subprocess environment
  when `--http-replay=new-episodes` and `--ignore-cache` are both
  active. Reasonable default — these flags are inherently a "capture
  only" mode where tracing adds nothing.

Verified to eliminate the bug in `repro_no_langsmith.py`. No code
changes to vcr or notebook_worker required.

### 2. Implemented — scope `reset_patchers` in the notebook bootstrap

**Shipped 2026-05-25** in `src/clm/workers/notebook/notebook_processor.py`
as part of `_HTTP_REPLAY_BOOTSTRAP_TEMPLATE`. The bootstrap, which is
prepended as the first cell of every HTTP-replay notebook, now replaces
`vcr.patch.reset_patchers` with a filtered generator that yields all the
patchers *except* the httpcore ones:

```python
_clm_original_reset_patchers = _clm_vcr_patch.reset_patchers
_clm_force_reset_skip = (
    (_clm_httpcore.ConnectionPool, "handle_request"),
    (_clm_httpcore.AsyncConnectionPool, "handle_async_request"),
)
def _clm_scoped_reset_patchers():
    for _p in _clm_original_reset_patchers():
        if (_p.getter(), _p.attribute) in _clm_force_reset_skip:
            continue
        yield _p
_clm_scoped_reset_patchers._clm_scoped = True
_clm_vcr_patch.reset_patchers = _clm_scoped_reset_patchers
```

`force_reset()` itself resolves `reset_patchers` via the module globals
at call time, so this swap propagates to every callsite (both
`__init__` and `connect` in `vcr/stubs/__init__.py`). The
recursion-avoidance guard `force_reset()` exists for still works because
all urllib3 patchers are still yielded — only httpcore (and the
analogous AsyncConnectionPool) is dropped.

A `_clm_scoped = True` marker makes the bootstrap idempotent: if the
same kernel exec's the bootstrap twice (or tests exec the template
repeatedly), the second pass short-circuits instead of stacking
wrappers and losing the true upstream original.

Regression tests live at
`tests/workers/notebook/test_notebook_processor.py::TestBootstrapDurability::test_bootstrap_scopes_vcr_force_reset_to_urllib3`
(checks the swap is in place and the yielded patcher set is correct)
and `…::test_bootstrap_force_reset_does_not_strip_httpcore_patch` (opens
a real cassette, enters `force_reset()`, and confirms httpcore stays
patched). Both tests `importlib.reload(vcr.patch)` first so they
observe the bootstrap's actual effect rather than state left over by a
previous test in the same xdist worker.

#### Removal checklist (when vcrpy fixes this upstream)

1. Confirm the vcrpy release includes a scoped `force_reset()` (either
   a new `libs=` parameter or per-stub helpers like `force_reset_urllib3()`).
2. Bump the vcrpy minimum in `pyproject.toml`.
3. Delete the `if not getattr(_clm_vcr_patch.reset_patchers, "_clm_scoped", …)`
   block in `_HTTP_REPLAY_BOOTSTRAP_TEMPLATE` and the
   `import vcr.patch as _clm_vcr_patch` line above it.
4. Delete the two regression tests in `TestBootstrapDurability`.
5. Delete this section of this document (leave the investigation as
   historical context).

### 3. Upstream fix in vcrpy

The right long-term fix is to scope `force_reset()` per-stub upstream.
Open an issue/PR against `kevin1024/vcrpy` proposing a `libs=("urllib3",)`
parameter to `force_reset()`, and have `vcr/stubs/__init__.py` pass it.
Several months out before a tagged release the user could pin, so this
should not block CLM work.

## Files in `C:\Users\tc\Programming\Python\Tests\clm-bug-repros\issue-129-vcrpy-force-reset-race\`

See the `README.md` in that directory for a per-script tour. The two
files worth pointing at:

- `verify_root_cause.py` — direct proof that holding
  `vcr.patch.force_reset()` open causes httpx calls to escape.
- `repro_thread.py` — minimal real-world reproducer (no langchain, no
  langsmith) using just `openrouter` SDK + a BG thread doing
  `requests.get(...)`.

## References

- Issue: <https://github.com/hoelzl/clm/issues/129>
- PR #126 (merged): partial fix — closed the `--ignore-cache` SQLite
  job-cache gate path that was masking workers entirely. This
  investigation is the residual bug PR #126 mentions in its commit
  message.
- vcrpy source paths (pin: `vcrpy 8.1.1`, `httpcore 1.0.9`, `httpx 0.28.1`):
  - `vcr/patch.py:99` — captures `_HttpcoreConnectionPool_handle_request` at import.
  - `vcr/patch.py:307-322` — `_httpcore` method, applies the patch on enter.
  - `vcr/patch.py:398-464` — `reset_patchers()`, yields the *un-patching* patchers.
  - `vcr/patch.py:467-472` — `force_reset()` context manager.
  - `vcr/stubs/__init__.py:333,357` — the two `with force_reset():` callsites in the urllib3 stub.
