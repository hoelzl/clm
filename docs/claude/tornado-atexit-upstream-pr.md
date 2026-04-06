# Tornado upstream PR: fix `_atexit_callback` set-mutation race

This document contains all information needed to file a bug report and/or
prepare a PR against the [tornadoweb/tornado](https://github.com/tornadoweb/tornado)
repository.

## Bug summary

`_atexit_callback` in `tornado/platform/asyncio.py` iterates the module-level
`_selector_loops` set with a bare `for loop in _selector_loops:`.  When a
`SelectorThread` is concurrently closed (e.g. by pyzmq's cleanup during
interpreter shutdown), `SelectorThread.close()` calls
`_selector_loops.discard(self)`, mutating the set during iteration.  This
raises:

```
RuntimeError: Set changed size during iteration
```

## Affected versions

- Confirmed on **tornado 6.5.5** (latest as of 2026-04-06).
- Present in all versions since `SelectorThread` was introduced (tornado 6.x).

## Reproduction

**Platform**: Windows (any version with `ProactorEventLoop` as default).
The bug does not manifest on Linux/macOS because `SelectorEventLoop` is the
default and pyzmq doesn't need to create a `SelectorThread`.

**Minimal reproduction**:

```python
# Requires: pyzmq, tornado, jupyter_client
import zmq.asyncio
import asyncio

async def main():
    ctx = zmq.asyncio.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.close()
    ctx.term()

asyncio.run(main())
# On exit: RuntimeError: Set changed size during iteration
```

**In practice**: Any test suite using `nbclient` or `jupyter_client` on
Windows triggers this.  The error appears during `atexit` cleanup and does
not affect correctness — all work has completed by that point.

### Call chain

1. `nbclient` starts a Jupyter kernel via `jupyter_client`.
2. `jupyter_client` uses `zmq.asyncio`, which on Windows calls
   `zmq.asyncio._get_selector_windows()` to wrap the `ProactorEventLoop`
   in a `tornado.platform.asyncio.AddThreadSelectorEventLoop`.
3. `AddThreadSelectorEventLoop.__init__` creates a `SelectorThread`, whose
   `__init__` calls `_selector_loops.add(self)` (line 509).
4. At interpreter shutdown, two things race:
   - `_atexit_callback` (line 73) iterates `_selector_loops`.
   - pyzmq's `_close_selector_and_loop` (in `zmq/asyncio.py`) calls
     `SelectorThread.close()`, which calls `_selector_loops.discard(self)`
     (line 521).
5. The `discard` mutates the set during iteration → `RuntimeError`.

## The fix

One-line change: iterate a snapshot instead of the live set.

### Diff

```diff
--- a/tornado/platform/asyncio.py
+++ b/tornado/platform/asyncio.py
@@ -71,7 +71,7 @@ _selector_loops: Set["SelectorThread"] = set()
 
 
 def _atexit_callback() -> None:
-    for loop in _selector_loops:
+    for loop in list(_selector_loops):
         with loop._select_cond:
             loop._closing_selector = True
             loop._select_cond.notify()
```

### Why `list()` is correct

- `_atexit_callback` already calls `_selector_loops.clear()` at the end, so
  it doesn't rely on the set being unmodified after iteration.
- A concurrent `discard` on the original set is harmless: the loop variable
  already holds a reference to the `SelectorThread` instance, and the cleanup
  logic (notify + join) is idempotent.
- `list()` is O(n) where n is the number of `SelectorThread` instances, which
  is typically 1.

### Alternative considered

Wrapping the `discard` in `SelectorThread.close()` with a try/except would
suppress the symptom but not the cause — the iterator in `_atexit_callback`
could still skip elements if the set shrinks during iteration.

## Test approach

This is difficult to unit-test deterministically because it requires
concurrent `atexit` + GC timing.  A regression test could:

1. Create two `SelectorThread` instances.
2. From a separate thread, call `.close()` on one while the main thread
   iterates `_selector_loops`.
3. Assert no `RuntimeError` is raised.

However, the one-line `list()` fix is trivially correct and the existing
`SelectorThread` tests cover the non-concurrent paths.

## Related issues

- pyzmq side: `zmq.asyncio._close_selector_and_loop` calls
  `selector_loop.close()` during interpreter shutdown.  This is correct
  behavior — pyzmq is cleaning up its resources.  The bug is in tornado's
  unprotected iteration.
- Similar pattern existed in CPython's `threading._shutdown` and was fixed
  by iterating a copy.

## PR checklist (for the tornado repo)

- [ ] Branch from `main`
- [ ] Edit `tornado/platform/asyncio.py` line 74: `for loop in _selector_loops:` → `for loop in list(_selector_loops):`
- [ ] Add a changelog entry (tornado uses `docs/releases/vnext.rst`)
- [ ] Run tornado's test suite: `python -m pytest tornado/test/`
- [ ] Reference this document's reproduction steps in the PR description
