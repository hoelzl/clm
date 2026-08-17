"""The #681 end-to-end replay gates: record → replay → byte-identical.

The mechanism behind issues #95/#115/#129/#143 and the S1 RCE had transport
smoke tests (``tests/infrastructure/test_http_replay_mitm.py``) but nothing
covering the actual build path::

    topic marked http-replay → build RECORDS → build REPLAYS → output identical

Two gates close that:

* **The round trip** — build the replay course against the live stub with
  ``--http-replay=refresh`` and ``--snapshot``; then build a *fresh* copy of
  the course, carrying only the recorded cassette, with the stub DOWN and
  ``--http-replay=replay`` + ``--verify-against``. Strict replay means every
  request was served from the cassette; the verify means the replayed build
  is byte-identical to the recorded one.
* **The committed cassette** — a fresh copy, no stub, no recording ever:
  strict replay must succeed purely from the cassette committed under the
  topic's ``.clm/cassettes/``, proving a fresh machine can rebuild from the
  committed traces. Re-record ritual: run
  ``python tests/e2e/http_replay_stub.py`` (fixed port 47113, the deck's
  fallback), rebuild with ``--http-replay=refresh`` in a copy, copy the
  cassette back, and re-run this test.

The course requests the three shapes that historically break the transport:
an OpenAI-style JSON POST with a bearer token, a chunked
``text/event-stream`` response, and a same-host redirect carrying an auth
header (see ``tests/test-data/.../slides_replay_shapes.py``).

Builds run as CLI subprocesses with a scrubbed environment (no inherited
``CLM_*`` — a developer shell's DB/cache overrides must not leak into the
build, the standing hermeticity landmine). ``serial("port")``: the stub
binds the fixed port the committed cassette was recorded against.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from http_replay_stub import DEFAULT_PORT, serve  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.serial("port")]

_TEST_DATA = Path(__file__).resolve().parent.parent / "test-data"
_SPEC_REL = Path("course-specs") / "test-spec-http-replay.xml"
_CASSETTE_REL = (
    Path("slides")
    / "module_060_http_replay"
    / "topic_100_replay_shapes"
    / ".clm"
    / "cassettes"
    / "slides_replay_shapes.http-cassette.yaml"
)
_BUILD_TIMEOUT = 600


def _build(course: Path, *args: str) -> subprocess.CompletedProcess:
    """Run ``clm build`` on ``course`` in a scrubbed environment."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLM_")}
    # CI=true flips replay-mode defaults; every mode here is explicit, but a
    # scrubbed, stable env keeps the two builds of one test identical.
    env.pop("CI", None)
    # Hermetic must not mean SHARED: scrubbing CLM_* also dropped the per-test
    # DB isolation, silently pointing every scrubbed build at the cwd default
    # `clm_jobs.db` — ONE jobs DB shared by every concurrently running
    # subprocess-build test. A concurrent build's cleanup_stale_workers then
    # deleted this build's seconds-old pre-registration and the worker died at
    # activation (flake doc §11.2). Give each build course-local DBs, and keep
    # the xdist worker's isolated log dir so the conftest failure diagnostics
    # can still harvest the worker logs.
    env["CLM_JOBS_DB_PATH"] = str(course / "clm_jobs.db")
    env["CLM_CACHE_DB_PATH"] = str(course / "clm_cache.db")
    if "CLM_LOG_DIR" in os.environ:
        env["CLM_LOG_DIR"] = os.environ["CLM_LOG_DIR"]
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "clm",
            "build",
            str(course / _SPEC_REL),
            "-d",
            str(course),
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_BUILD_TIMEOUT,
        env=env,
    )


def _copy_course(target: Path, *, with_committed_cassette: bool) -> Path:
    shutil.copytree(_TEST_DATA, target)
    if not with_committed_cassette:
        (target / _CASSETTE_REL).unlink()
    return target


def test_record_then_replay_is_byte_identical(tmp_path: Path) -> None:
    """The issue's own acceptance path, end to end."""
    record_course = _copy_course(tmp_path / "record", with_committed_cassette=False)
    snapshot = tmp_path / "snapshot"

    with serve(DEFAULT_PORT):
        recorded = _build(
            record_course,
            "--snapshot",
            str(snapshot),
            "--http-replay=refresh",
            "--fail-on-error",
        )
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    cassette = record_course / _CASSETTE_REL
    assert cassette.is_file(), "the record build must write the cassette"
    assert "interactions:" in cassette.read_text(encoding="utf-8")

    # A fresh course copy: only the recorded cassette carries over, the stub
    # is DOWN, and strict replay + fail-on-error make any live-network
    # fallback or unserved request a loud failure.
    replay_course = _copy_course(tmp_path / "replay", with_committed_cassette=False)
    (replay_course / _CASSETTE_REL).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cassette, replay_course / _CASSETTE_REL)

    replayed = _build(
        replay_course,
        "--verify-against",
        str(snapshot),
        "--http-replay=replay",
    )
    assert replayed.returncode == 0, replayed.stdout + replayed.stderr
    assert "Verification passed" in replayed.stdout


def test_committed_cassette_replays_on_a_fresh_machine(tmp_path: Path) -> None:
    """No stub, no recording, no network: the committed cassette alone must
    carry a strict-replay build — the property that lets any machine rebuild
    from the committed traces."""
    course = _copy_course(tmp_path / "fresh", with_committed_cassette=True)

    result = _build(course, "-o", str(tmp_path / "out"), "--http-replay=replay")
    assert result.returncode == 0, result.stdout + result.stderr

    # Execution results ship in the rendered HTML — the emitted .ipynb are
    # deliberately output-stripped, so the HTML is where replayed responses
    # become visible.
    pages = list((tmp_path / "out").rglob("*.html"))
    assert pages, "the build must produce rendered HTML"
    joined = "\n".join(p.read_text(encoding="utf-8") for p in pages)
    assert "Hello from the replay stub." in joined
    assert "text/event-stream: 3 events: alpha beta gamma" in joined
    assert "200 1 new True" in joined  # requests kept the auth header across the hop
