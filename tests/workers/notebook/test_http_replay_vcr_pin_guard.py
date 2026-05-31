"""Pin-guard regression tests for the forked vcrpy internals (issue #143).

The HTTP-replay bootstrap in ``clm/workers/notebook/notebook_processor.py``
does not just *use* vcrpy -- it **forks vcrpy internals** to compensate for
upstream bugs:

* The issue #143 connection-leak fix reinstalls vcrpy 8.1.x's
  ``vcr.stubs.httpcore_stubs._vcr_handle_request`` /
  ``_vcr_handle_async_request`` verbatim, with one change: an explicit
  ``real_response.close()`` / ``aclose()`` before the ``.stream`` swap, so the
  pooled httpcore connection is actually returned. Without that the pool is
  exhausted by a ``.batch()`` burst and every worker deadlocks in
  ``httpcore.connection_pool.wait_for_connection``.
* The forked functions call upstream internals (``_vcr_request``,
  ``_record_responses``, ``ByteStream``) **by name**.

The danger is *silent rot*: if a future vcrpy renames/refactors those
functions, our fork would silently stop closing the leaked connection and the
deadlock would return with no error and no test failure. These tests are the
loud tripwire. The primary defense is the tight ``pyproject.toml`` ``[replay]``
pin (``vcrpy>=8.1.1,<8.2``); this file backs it up by:

1. asserting the pin keeps an upper bound (runs even without vcrpy installed);
2. asserting the *installed* vcrpy is the validated 8.1.x line;
3. asserting the upstream stub functions still match the baseline we forked
   from (the real drift detector);
4. asserting the in-kernel guard actually raises on a drifted vcrpy.

If a deliberate vcrpy upgrade is being made: re-read upstream
``_vcr_handle_request`` / ``_vcr_handle_async_request``, re-confirm they still
never ``close()`` the response (or that the leak is fixed upstream and the fork
can be dropped), update the baselines + the bootstrap fork + the pin together.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from clm.workers.notebook.notebook_processor import _inject_http_replay_bootstrap

# Repo root: tests/workers/notebook/<this file> -> parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# The validated vcrpy line. Keep in sync with the ``[replay]`` pin and the
# in-kernel guard in ``notebook_processor.py``.
_VALIDATED_MAJOR_MINOR = "8.1"

# Normalized (comment- and blank-line-stripped) baseline of the upstream
# vcrpy 8.1.1 stub functions that the bootstrap forks. Captured from
# ``inspect.getsource`` on vcrpy 8.1.1. A change here means upstream refactored
# the functions we copied -- re-validate the fork before bumping the pin.
_UPSTREAM_SYNC_BASELINE = [
    "def _vcr_handle_request(cassette, real_handle_request, self, real_request):",
    'real_request_body = b"".join(real_request.stream)',
    "real_request.stream = ByteStream(real_request_body)",
    "vcr_request, vcr_response = _vcr_request(cassette, real_request, real_request_body)",
    "if vcr_response:",
    "return vcr_response",
    "real_response = real_handle_request(self, real_request)",
    'real_response_content = b"".join(real_response.stream)',
    "real_response.stream = ByteStream(real_response_content)",
    "_record_responses(cassette, vcr_request, real_response, real_response_content)",
    "return real_response",
]
_UPSTREAM_ASYNC_BASELINE = [
    "async def _vcr_handle_async_request(cassette, real_handle_async_request, self, real_request):",
    'real_request_body = b"".join([part async for part in real_request.stream])',
    "real_request.stream = ByteStream(real_request_body)",
    "vcr_request, vcr_response = _vcr_request(cassette, real_request, real_request_body)",
    "if vcr_response:",
    "return vcr_response",
    "real_response = await real_handle_async_request(self, real_request)",
    'real_response_content = b"".join([part async for part in real_response.stream])',
    "real_response.stream = ByteStream(real_response_content)",
    "_record_responses(cassette, vcr_request, real_response, real_response_content)",
    "return real_response",
]


def _normalize(source: str) -> list[str]:
    """Strip comments, blank lines, and indentation for a semantic comparison.

    Comment/whitespace churn upstream should not trip the drift detector, but
    any change to the actual statements (which is what would break our fork)
    will.
    """
    out: list[str] = []
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def test_replay_pin_keeps_upper_bound() -> None:
    """The ``[replay]`` vcrpy pin must keep an upper bound.

    Runs without vcrpy installed (parses pyproject only), so it guards in CI
    even though CI does not install the ``[replay]`` extra. An unbounded
    ``vcrpy>=...`` would let an unvalidated release silently break the forked
    stubs (issue #143).
    """
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    replay = data["project"]["optional-dependencies"]["replay"]
    vcr_specs = [s for s in replay if s.replace("_", "-").lower().startswith("vcrpy")]
    assert vcr_specs, f"no vcrpy requirement found in [replay]: {replay}"
    spec = vcr_specs[0]
    assert "<" in spec, (
        f"vcrpy pin {spec!r} has no upper bound. The bootstrap forks vcrpy "
        "internals (issue #143); an unbounded pin lets an unvalidated release "
        "silently resurrect the connection-pool deadlock. Keep a '<' ceiling."
    )
    assert _VALIDATED_MAJOR_MINOR in spec, (
        f"vcrpy pin {spec!r} no longer references the validated {_VALIDATED_MAJOR_MINOR}.x "
        "line; re-validate the forked stubs before changing it."
    )


def test_installed_vcrpy_is_validated_line() -> None:
    """The installed vcrpy (where present) must be the validated 8.1.x line."""
    vcr = pytest.importorskip("vcr")
    version = getattr(vcr, "__version__", "0")
    major_minor = ".".join(str(version).split(".")[:2])
    assert major_minor == _VALIDATED_MAJOR_MINOR, (
        f"installed vcrpy {version} is outside the validated {_VALIDATED_MAJOR_MINOR}.x line "
        "the issue #143 fork was forked from. Re-validate the fork + pin."
    )


def test_upstream_httpcore_stubs_match_forked_baseline() -> None:
    """The upstream stub functions must still match what the bootstrap forked.

    This is the real drift detector. If vcrpy changes ``_vcr_handle_request`` /
    ``_vcr_handle_async_request``, our verbatim fork in the bootstrap is stale:
    it may no longer close the leaked connection, or may call internals that
    moved. Fail loudly so a human re-validates before the deadlock returns.
    """
    pytest.importorskip("vcr")
    import inspect

    import vcr.stubs.httpcore_stubs as hcs

    for name in ("_vcr_request", "_record_responses", "ByteStream"):
        assert hasattr(hcs, name), (
            f"vcrpy httpcore_stubs lost {name!r}; the issue #143 fork calls it by "
            "name and would break. Re-validate the fork."
        )

    sync_actual = _normalize(inspect.getsource(hcs._vcr_handle_request))
    async_actual = _normalize(inspect.getsource(hcs._vcr_handle_async_request))
    assert sync_actual == _UPSTREAM_SYNC_BASELINE, (
        "upstream vcrpy _vcr_handle_request changed vs the 8.1.1 baseline the "
        "bootstrap forked. Re-read it, re-confirm the response is still not "
        "closed upstream, and update the fork + baseline + pin together (#143)."
    )
    assert async_actual == _UPSTREAM_ASYNC_BASELINE, (
        "upstream vcrpy _vcr_handle_async_request changed vs the 8.1.1 baseline "
        "the bootstrap forked. Re-read it and update the fork + baseline + pin (#143)."
    )


def test_upstream_still_leaks_so_the_fork_is_still_needed() -> None:
    """Sanity check that upstream still does NOT close the response.

    If a future vcrpy adds the ``close()``/``aclose()`` itself, the fork is
    redundant and should be retired (and the upstream PR landed). This test
    documents that assumption: it passes while the leak exists and starts
    failing -- prompting cleanup -- once upstream fixes it.
    """
    pytest.importorskip("vcr")
    import inspect

    import vcr.stubs.httpcore_stubs as hcs

    sync_src = inspect.getsource(hcs._vcr_handle_request)
    async_src = inspect.getsource(hcs._vcr_handle_async_request)
    assert ".close()" not in sync_src, (
        "upstream vcrpy _vcr_handle_request now closes the response -- the "
        "issue #143 fork may be redundant. Verify and retire it."
    )
    assert ".aclose()" not in async_src, (
        "upstream vcrpy _vcr_handle_async_request now closes the response -- the "
        "issue #143 fork may be redundant. Verify and retire it."
    )


def _extract_guard_snippet(bootstrap_src: str) -> str:
    """Slice the in-kernel pin-guard block out of the rendered bootstrap."""
    start_marker = '_clm_vcr_version = getattr(_clm_vcr, "__version__", "0")'
    end_marker = "def _clm_vcr_handle_request("
    start = bootstrap_src.index(start_marker)
    end = bootstrap_src.index(end_marker, start)
    return bootstrap_src[start:end]


_FORKED_SYMBOLS = (
    "_vcr_handle_request",
    "_vcr_handle_async_request",
    "_vcr_request",
    "_record_responses",
    "ByteStream",
)


def _fake_hcs(missing: tuple[str, ...] = ()) -> object:
    """A stand-in for ``vcr.stubs.httpcore_stubs`` with selected symbols absent.

    Built as a fresh namespace per call so ``del``/omission actually makes
    ``hasattr`` return False (a subclass can't shadow-delete an inherited attr).
    """
    from types import SimpleNamespace

    attrs = {
        name: (object if name == "ByteStream" else (lambda *a, **k: None))
        for name in _FORKED_SYMBOLS
        if name not in missing
    }
    return SimpleNamespace(**attrs)


def _run_guard(version: str, hcs: object) -> None:
    """Exec the shipped guard snippet against a fake vcr/hcs namespace.

    Renders the real bootstrap cell, slices out the in-kernel guard, and execs
    only that slice -- so we test the exact code that ships in the kernel,
    without opening a cassette or hitting the network.
    """
    from nbformat.v4 import new_notebook

    nb = new_notebook()
    _inject_http_replay_bootstrap(nb, "/abs/c.yaml", "replay")
    snippet = _extract_guard_snippet(nb["cells"][0]["source"])
    ns: dict = {"_clm_vcr": type("V", (), {"__version__": version}), "_clm_hcs": hcs}
    exec(snippet, ns)


def test_bootstrap_guard_passes_on_validated_vcrpy() -> None:
    """The guard must not raise on the validated 8.1.x line with all symbols."""
    pytest.importorskip("vcr")
    _run_guard("8.1.1", _fake_hcs())


def test_bootstrap_guard_raises_on_version_drift() -> None:
    """A vcrpy outside the validated line must trip the in-kernel guard."""
    pytest.importorskip("vcr")
    with pytest.raises(RuntimeError, match="issue #143"):
        _run_guard("8.2.0", _fake_hcs())


def test_bootstrap_guard_raises_on_missing_symbol() -> None:
    """A missing forked-internals symbol must trip the in-kernel guard."""
    pytest.importorskip("vcr")
    with pytest.raises(RuntimeError, match="_vcr_request"):
        _run_guard("8.1.1", _fake_hcs(missing=("_vcr_request",)))
