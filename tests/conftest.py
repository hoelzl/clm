"""Pytest configuration and fixtures.

Logging Configuration:
---------------------
Tests with 'e2e' or 'integration' markers automatically get live logging enabled.

To enable logging for any test:
1. Use the marker: @pytest.mark.e2e or @pytest.mark.integration
2. Explicitly use the fixture: def test_something(configure_test_logging): ...
3. Set environment variable: CLM_ENABLE_TEST_LOGGING=1
4. Use pytest option: pytest --log-cli

Environment variables:
- CLM_LOG_LEVEL: Set log level (DEBUG, INFO, WARNING, ERROR) - default: INFO
- CLM_ENABLE_TEST_LOGGING: Enable logging for all tests (set to any value)
- CLM_PROGRESS__UPDATE_INTERVAL: Seconds between progress updates (default: 5)
- CLM_PROGRESS__LONG_JOB_THRESHOLD: Seconds before warning about long jobs (default: 30)
"""

# ---------------------------------------------------------------------------
# Workaround: tornado SelectorThread atexit race on Windows
#
# On Windows, pyzmq (via nbclient/jupyter_client) creates a
# tornado.platform.asyncio.SelectorThread, which registers itself in the
# module-level set ``_selector_loops``.  At interpreter shutdown, tornado's
# ``_atexit_callback`` iterates that set with a bare ``for loop in
# _selector_loops:``.  Concurrently, pyzmq's cleanup calls
# ``SelectorThread.close()`` which does ``_selector_loops.discard(self)``,
# mutating the set during iteration and raising ``RuntimeError: Set changed
# size during iteration``.
#
# The fix is trivial: iterate a snapshot (``list(...)``).  We monkey-patch
# the atexit handler here so the noisy traceback disappears.  Remove this
# once tornado ships the fix upstream.
#
# Upstream issue: https://github.com/tornadoweb/tornado/issues/3409
# Affected versions: tornado <= 6.5.5
# ---------------------------------------------------------------------------
import atexit
import sys

if sys.platform == "win32":
    try:
        import tornado.platform.asyncio as _tpa

        atexit.unregister(_tpa._atexit_callback)

        def _safe_atexit_callback() -> None:
            for loop in list(_tpa._selector_loops):
                with loop._select_cond:
                    loop._closing_selector = True
                    loop._select_cond.notify()
                try:
                    loop._waker_w.send(b"a")
                except BlockingIOError:
                    pass
                if loop._thread is not None:
                    loop._thread.join()
            _tpa._selector_loops.clear()

        atexit.register(_safe_atexit_callback)
    except (ImportError, AttributeError):
        pass  # tornado not installed or internals changed

import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ETree

import pytest

# Relax the heartbeat slow-write self-disable threshold for the whole test
# session. The production default (50ms, in
# ``clm.infrastructure.database.worker_heartbeats.SLOW_WRITE_THRESHOLD_SECONDS``)
# legitimately trips under pytest-xdist load — a single SQLite UPSERT can spike
# past 50ms on lock contention, a WAL checkpoint, or an antivirus scan — which
# silently turns subsequent heartbeat writes into no-ops and fails
# ``assert None == expected``. We set the env override HERE, before the first
# ``clm`` import below (which may transitively import worker_heartbeats and
# evaluate the module constant), so the relaxation lands in ONE place for
# in-process stores AND subprocess workers (which inherit the env). This
# replaces the per-file ``_relax_slow_write_threshold`` autouse fixtures, so a
# new heartbeat test cannot silently re-acquire the flake. The dedicated
# disable-path test re-patches the constant to 0.0 in its own scope, preserving
# that coverage. ``setdefault`` lets an operator override it explicitly.
#
# PRODUCTION VALUE COVERED BY (finding T8 — every neutraliser must name the
# single test that still exercises the real behaviour, or the neutraliser is
# indistinguishable from deleting the feature's coverage):
#   tests/infrastructure/database/test_worker_heartbeats.py
#     ::TestWorkerHeartbeatStore::test_slow_write_disables_further_writes
# **Never raise the 50ms production default** to make a test pass — relax it
# here, in the test environment only.
os.environ.setdefault("CLM_HEARTBEAT_SLOW_WRITE_THRESHOLD_SECONDS", "30")

# Strip ambient color-forcing before any Click/Rich console is created. Agent
# harnesses and some terminal setups export ``FORCE_COLOR`` (observed:
# ``FORCE_COLOR=3`` in the Claude Code shell), which makes Rich/Click emit ANSI
# escapes into *captured, non-TTY* CLI output — and 11 CLI tests assert plain
# substrings in that output (voiceover/recordings command tables, a JSON
# stdout), so the whole fast suite goes red on the pre-push hook in such a
# shell while CI (no FORCE_COLOR) stays green. Tests never rely on forced
# color, so popping is safe; ``NO_COLOR`` is deliberately left alone (it makes
# output *plainer*, which the assertions already expect). ``pop`` not
# ``setdefault``: the variable must be absent, not defaulted.
for _color_var in ("FORCE_COLOR", "CLICOLOR_FORCE"):
    os.environ.pop(_color_var, None)

# The stream type ``CliRunner.isolation()`` installs on ``sys.stdout``/
# ``sys.stderr``. Private to ``click.testing``, so resolve it defensively: if a
# future Click renames it, fall back to matching on the type name, and let
# ``tests/test_clirunner_capture_integrity.py`` be the thing that fails loudly.
try:
    from click.testing import _NamedTextIOWrapper as _ClickRunnerStream

    _CLICK_RUNNER_STREAM_TYPES: tuple[type, ...] = (_ClickRunnerStream,)
except ImportError:  # pragma: no cover - Click renamed its private wrapper
    _CLICK_RUNNER_STREAM_TYPES = ()

from clm.core.course_spec import TopicSpec
from clm.core.messaging.base_classes import Payload
from clm.core.operation import Operation
from clm.core.utils.text_utils import Text
from clm.infrastructure.backends.local_ops_backend import LocalOpsBackend

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.section import Section
    from clm.core.topic import Topic


# ====================================================================
# Per-worker log isolation
# ====================================================================


@pytest.fixture(scope="session", autouse=True)
def _isolate_clm_log_dir(tmp_path_factory):
    """Give each pytest-xdist worker its own CLM log directory.

    Without this, every worker process writes to the single global
    ``clm.log`` (``%LOCALAPPDATA%/clm/Logs`` on Windows). When many workers
    open or rotate that one file concurrently, Windows intermittently raises
    ``PermissionError`` and fails whichever CLI test happened to be inside
    ``setup_logging`` at that moment — a pure cross-worker contention flake.
    Pointing each worker at its own temp directory removes the sharing at the
    root. ``tmp_path_factory.getbasetemp()`` is already per-worker under
    xdist, so the directories never collide. Honoured via the ``CLM_LOG_DIR``
    override in ``clm.infrastructure.logging.log_paths`` (which subprocess
    workers spawned during a test inherit through the environment too).
    """
    from clm.infrastructure.logging.log_paths import LOG_DIR_ENV_VAR

    log_dir = tmp_path_factory.mktemp("clm-logs")
    previous = os.environ.get(LOG_DIR_ENV_VAR)
    os.environ[LOG_DIR_ENV_VAR] = str(log_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(LOG_DIR_ENV_VAR, None)
        else:
            os.environ[LOG_DIR_ENV_VAR] = previous


@pytest.fixture(autouse=True)
def _isolate_http_replay_env():
    """Restore the ``CLM_HTTP_REPLAY_*`` env vars around every test.

    ``main_build`` pins the resolved HTTP-replay *mode* and *transport* into
    ``os.environ`` (so Direct-worker kernels inherit them via
    ``os.environ.copy()``). A test that runs the real build pipeline therefore
    leaks those values to later tests in the same process — a cross-test
    pollution flake under xdist. Snapshotting and restoring here keeps each
    test's ambient transport/mode deterministic regardless of execution
    order; tests that set these via ``monkeypatch`` are unaffected.
    """
    keys = ("CLM_HTTP_REPLAY_TRANSPORT", "CLM_HTTP_REPLAY_MODE")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def _isolate_db_path_env():
    """Clear the ``CLM_*_DB_PATH`` env vars for the duration of every test.

    The cache / jobs / telemetry DB paths *default* to a project-root-anchored
    location (issue #477). A developer who points one at a scratch disk in their
    shell — e.g. ``CLM_JOBS_DB_PATH=Z:\\clm_jobs.db`` — would otherwise have that
    value bleed into the test process and override the default *inside* every
    test that asserts the resolved default (``test_default_db_path_detection``,
    the db-path anchoring sweep, …). CI runs with a clean environment, so such a
    test passes in CI but fails on the configured dev machine — exactly the
    ambient-environment dependence a hermetic suite must not have.

    Snapshotting, clearing, and restoring here makes default-path resolution
    deterministic regardless of the developer's shell. A test that needs a
    specific path *set* does so in its own body via ``monkeypatch.setenv``
    (test_status_collector's env-var tests, test_sqlite_backend_resilience's
    decoy) — that runs after this fixture, so those are unaffected. Mirrors the
    ``_isolate_http_replay_env`` / ``_neutralise_pool_size_cap`` precedents.

    PRODUCTION VALUE COVERED BY (finding T8):
      tests/cli/test_status_collector.py::TestStatusCollector
        ::test_default_db_path_detection — the anchored default; and
        ::test_default_db_path_prefers_jobs_db_env — the env var winning over
        it, set inside the test body so this fixture cannot mask it.
    """
    keys = (
        "CLM_CACHE_DB_PATH",
        "CLM_JOBS_DB_PATH",
        "CLM_TELEMETRY_DB_PATH",
        "CLM_DB_PATH",
        # Retired worker-side spelling (A8): no clm code sets it any more,
        # but a value lingering in a dev shell would leak into the env dicts
        # the worker-executor tests inspect.
        "DB_PATH",
    )
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def _reset_effective_worker_identities():
    """Keep the #744 image-identity registry hermetic per test.

    ``configure_workers`` records the effective worker-image identities in
    a module-level registry; any test driving it (build-command tests, the
    config-loader tests) would otherwise leak the recording process-wide
    and change what later tests observe from the singleton-fallback path —
    same reasoning as ``_isolate_db_path_env``. The registry lives in core
    since Phase 8 S4 (#802); resetting it does not touch the registered
    fallback provider.
    """
    from clm.core.worker_identity import reset_effective_worker_identities

    reset_effective_worker_identities()
    yield
    reset_effective_worker_identities()


@pytest.fixture(autouse=True)
def _worker_api_env(request):
    """Isolate the Worker API env vars, and give each Docker test its own port.

    Two jobs, both about not sharing a port:

    **Hermeticity.** ``CLM_WORKER_API_HOST`` / ``CLM_WORKER_API_PORT`` /
    ``CLM_API_TOKEN`` are read by ``WorkerApiServer.__init__``. A developer who
    exports any of them in their shell would otherwise change what tests that
    assert the *default* bind, port, or token generation observe — passing in
    CI, failing on the configured machine. Same reasoning as
    ``_isolate_db_path_env``.

    **Per-test ports for the Docker tier.** Tests marked ``docker`` run real
    containers against a real ``WorkerApiServer``. On the fixed default port,
    consecutive tests in a tier can overlap — a server whose listening socket
    has not gone away yet, plus a fresh one binding the same port — and a
    container's callback then reaches whichever socket the OS picks, so its job
    is never claimed and the test fails asserting ``status 'pending'`` with no
    error. Requesting port 0 makes the OS hand every server a private port, and
    the container is told that port via ``CLM_API_URL``, so nothing has to agree
    on a number in advance. (``bind_socket`` no longer sets ``SO_REUSEADDR`` on
    Windows either, so an overlap would now be a loud error rather than a
    hijack; per-test ports mean there is no overlap to report.)

    PRODUCTION VALUE COVERED BY (finding T8 — a neutraliser must name the test
    that still exercises the real behaviour):
      tests/infrastructure/api/test_binding.py::TestResolvePort — the default
        port and the env var, both read straight from ``resolve_port``; and
      tests/infrastructure/api/test_server.py::TestConstruction
        ::test_defaults_to_standard_host_port.
    """
    keys = ("CLM_WORKER_API_HOST", "CLM_WORKER_API_PORT", "CLM_API_TOKEN")
    saved = {k: os.environ.pop(k, None) for k in keys}
    if "docker" in request.keywords:
        os.environ["CLM_WORKER_API_PORT"] = "0"
    try:
        yield
    finally:
        for key in keys:
            os.environ.pop(key, None)
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


# ====================================================================
# Tool Availability Detection
# ====================================================================


def _is_plantuml_available() -> bool:
    """Check if PlantUML is available and functional."""
    plantuml_jar = os.environ.get("PLANTUML_JAR")
    if not plantuml_jar or not Path(plantuml_jar).exists():
        return False

    # Check if file is a Git LFS pointer (not the actual JAR)
    try:
        with open(plantuml_jar, "rb") as f:
            header = f.read(100)
            if b"git-lfs.github.com" in header:
                return False
    except Exception:
        return False

    # Check if Java is available (1 second timeout for faster startup)
    try:
        result = subprocess.run(["java", "-version"], capture_output=True, timeout=1)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _is_drawio_available() -> bool:
    """Check if DrawIO is available and can render.

    DrawIO requires:
    1. DrawIO executable to be available
    2. On Unix/Linux: DISPLAY environment variable (real display or Xvfb)
    3. On Windows: No DISPLAY needed (native GUI)

    This works correctly in:
    - Windows desktop: DrawIO.exe available (no DISPLAY needed)
    - Linux/Mac desktop: DrawIO available + DISPLAY=:0 (real display)
    - Linux/Mac headless: DrawIO available + DISPLAY=:99 (Xvfb)
    """
    # Check if DrawIO executable exists
    drawio_exec = os.environ.get("DRAWIO_EXECUTABLE")

    # Try to find drawio in PATH if not set
    if not drawio_exec:
        drawio_exec = shutil.which("drawio")

    if not drawio_exec or not Path(drawio_exec).exists():
        return False

    # Check if file is a Git LFS pointer
    try:
        with open(drawio_exec, "rb") as f:
            header = f.read(100)
            if b"git-lfs.github.com" in header:
                return False
    except Exception:
        pass

    # On Unix/Linux, DISPLAY is required (X11)
    # On Windows, DISPLAY is not needed (native GUI)
    if sys.platform != "win32":
        if not os.environ.get("DISPLAY"):
            return False

    return True


def _is_xvfb_running() -> bool:
    """Check if Xvfb is running.

    Note: This is for informational/diagnostic purposes only.
    Tests should use requires_drawio marker, which checks for DISPLAY
    (works with both real displays and Xvfb).
    """
    if not os.environ.get("DISPLAY"):
        return False

    try:
        result = subprocess.run(["pgrep", "-x", "Xvfb"], capture_output=True, timeout=1)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# Store tool availability at module level (cached for performance)
_PLANTUML_AVAILABLE = None
_DRAWIO_AVAILABLE = None
_XVFB_RUNNING = None
_DOCKER_AVAILABLE = None


def _is_docker_available() -> bool:
    """Check if Docker daemon is available and responsive."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except ImportError:
        # docker package not installed
        return False
    except Exception:
        # Docker daemon not running or other error
        return False


def get_tool_availability():
    """Get cached tool availability status."""
    global _PLANTUML_AVAILABLE, _DRAWIO_AVAILABLE, _XVFB_RUNNING, _DOCKER_AVAILABLE

    if _PLANTUML_AVAILABLE is None:
        _PLANTUML_AVAILABLE = _is_plantuml_available()
    if _DRAWIO_AVAILABLE is None:
        _DRAWIO_AVAILABLE = _is_drawio_available()
    if _XVFB_RUNNING is None:
        _XVFB_RUNNING = _is_xvfb_running()
    if _DOCKER_AVAILABLE is None:
        _DOCKER_AVAILABLE = _is_docker_available()

    return {
        "plantuml": _PLANTUML_AVAILABLE,
        "drawio": _DRAWIO_AVAILABLE,
        "xvfb": _XVFB_RUNNING,  # For diagnostic purposes only
        "docker": _DOCKER_AVAILABLE,
    }


COURSE_1_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/my-course-de</de>
        <en>https://github.com/hoelzl/my-course-en</en>
    </github>
    <name>
        <de>Mein Kurs</de>
        <en>My Course</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Kurs über ein Thema</de>
        <en>A course about a topic</en>
    </description>
    <certificate>
        <de>...</de>
        <en>...</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>
                    some_topic_from_test_1
                    <dir-group>
                        <name>Code/Solutions</name>
                        <path>code/solutions</path>
                        <subdirs>
                            <subdir>Example_1</subdir>
                            <subdir>Example_3</subdir>
                        </subdirs>
                    </dir-group>
                </topic>
                <topic>a_topic_from_test_2</topic>
            </topics>
        </section>
        <section>
            <name>
                <de>Woche 2</de>
                <en>Week 2</en>
            </name>
            <topics>
                <topic>another_topic_from_test_1</topic>
            </topics>
        </section>
    </sections>
    <dir-groups>
        <dir-group>
            <name>Bonus</name>
            <path>div/workshops</path>
        </dir-group>
        <!-- We can have an empty name to copy files into the course root -->
        <dir-group>
            <name/>
            <path>root-files</path>
        </dir-group>
    </dir-groups>
</course>
"""

COURSE_2_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/my-course-de</de>
        <en>https://github.com/hoelzl/my-course-en</en>
    </github>
    <name>
        <de>Kurs 2</de>
        <en>Kurs 2</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Der zweite Kurs</de>
        <en>The second course</en>
    </description>
    <certificate>
        <de>...</de>
        <en>...</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>slides_in_test_3</topic>
            </topics>
        </section>
    </sections>
</course>
"""


DATA_DIR = Path(__file__).parent / "test-data"


# Repository root (this file lives in ``<repo>/tests/``).
REPO_ROOT = Path(__file__).parent.parent

# Where the PlantUML JAR lives in a checkout. The old discovery path pointed at
# ``tests/services/plantuml-converter/`` — the pre-PR-#239 vendored location,
# which no longer exists, so this fallback silently found nothing and local
# availability depended on an import-time ``os.environ`` mutation in
# ``tests/workers/plantuml/test_plantuml_converter.py`` (finding T10). That
# made PlantUML availability ordering-dependent under xdist. This is now the
# single fallback, and the test module no longer touches the environment.
PLANTUML_JAR_CANDIDATES = [
    REPO_ROOT / "docker" / "plantuml" / "plantuml-1.2024.6.jar",
]


# Configure external tool paths at module load time
# This ensures they're available before test collection
def _setup_external_tools():
    """Set up environment variables for external tools if not already set."""
    # PlantUML JAR path
    if "PLANTUML_JAR" not in os.environ:
        for plantuml_jar in PLANTUML_JAR_CANDIDATES:
            if plantuml_jar.exists():
                os.environ["PLANTUML_JAR"] = str(plantuml_jar)
                logging.info(f"PLANTUML_JAR set to: {plantuml_jar}")
                break

    # Draw.io executable path
    if "DRAWIO_EXECUTABLE" not in os.environ:
        common_drawio_paths = [
            r"C:\Program Files\draw.io\draw.io.exe",
            r"C:\Program Files (x86)\draw.io\draw.io.exe",
        ]
        for drawio_path in common_drawio_paths:
            if Path(drawio_path).exists():
                os.environ["DRAWIO_EXECUTABLE"] = drawio_path
                logging.info(f"DRAWIO_EXECUTABLE set to: {drawio_path}")
                break


# Run setup at module import time
_setup_external_tools()


@pytest.fixture
def course_1_xml():
    return ETree.fromstring(COURSE_1_XML)


@pytest.fixture
def course_2_xml():
    return ETree.fromstring(COURSE_2_XML)


@pytest.fixture(scope="session")
def course_1_spec():
    """Session-scoped CourseSpec for COURSE_1_XML.

    Session-scoped because CourseSpec is immutable and parsing is deterministic.
    This avoids re-parsing the same XML for every test.
    """
    from clm.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_1_XML)

    return CourseSpec.from_file(xml_stream)


@pytest.fixture(scope="session")
def course_2_spec():
    """Session-scoped CourseSpec for COURSE_2_XML.

    Session-scoped because CourseSpec is immutable and parsing is deterministic.
    """
    from clm.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_2_XML)

    return CourseSpec.from_file(xml_stream)


@pytest.fixture
def course_1(course_1_spec, tmp_path):
    from clm.core.course import Course

    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)
    return course


@pytest.fixture
def course_2(course_2_spec, tmp_path):
    from clm.core.course import Course

    course = Course.from_spec(course_2_spec, DATA_DIR, tmp_path)
    return course


@pytest.fixture
def section_1(course_1):
    from clm.core.course import Section

    return Section(name=Text(en="Week 1", de="Woche 1"), course=course_1)


@pytest.fixture
def topic_1(section_1):
    from clm.core.course import Topic

    path = DATA_DIR / "slides/module_000_test_1/topic_100_some_topic_from_test_1"
    spec = TopicSpec(id="some_topic")
    return Topic.from_spec(spec, section=section_1, path=path)


class PytestLocalOpsBackend(LocalOpsBackend):
    async def execute_operation(self, operation: "Operation", payload: Payload) -> None:
        pass

    async def wait_for_completion(self, all_submitted=None) -> bool:
        return True

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# E2E Test Fixtures


def _is_xdist_worker(config) -> bool:
    """Whether this process is a pytest-xdist worker rather than the controller."""
    return hasattr(config, "workerinput")


def _inside_click_runner() -> bool:
    """Whether a ``CliRunner`` isolation currently owns ``sys.stdout``/``sys.stderr``.

    ``CliRunner.isolation()`` swaps both streams for its own
    ``_NamedTextIOWrapper`` objects over the ``StreamMixer`` that backs
    ``result.output``. Nothing else in this suite installs that type, so its
    presence is an exact signal.
    """
    if _CLICK_RUNNER_STREAM_TYPES:
        return isinstance(sys.stdout, _CLICK_RUNNER_STREAM_TYPES) or isinstance(
            sys.stderr, _CLICK_RUNNER_STREAM_TYPES
        )
    return "_NamedTextIOWrapper" in (
        type(sys.stdout).__name__,
        type(sys.stderr).__name__,
    )


def _install_clirunner_live_log_guard() -> None:
    """Stop pytest's live-log handler from clobbering ``CliRunner`` capture.

    ``_LiveLoggingStreamHandler.emit`` wraps every record in
    ``CaptureManager.global_and_fixture_disabled()`` so the line reaches the real
    terminal instead of the capture buffer. That context manager suspends global
    capture and then *resumes* it -- and resuming does
    ``setattr(sys, "stdout", self.tmpfile)``, installing pytest's own stream
    object over whatever was there. When the record is emitted from inside a
    ``CliRunner.invoke()``, "whatever was there" is Click's isolation, which is
    destroyed for the remainder of that invocation: every later ``click.echo``
    lands in pytest's captured output instead of ``result.output``. The
    invocation's assertions then fail against a truncated (often empty) string.

    That cost the 2026-07-31 and 2026-08-08 nightlies six failing tests between
    them, and it is latent in every one of the ~90 ``CliRunner`` modules. Full
    analysis: ``docs/claude/design/test-flakiness-root-causes.md``.

    The fix is to drop the handler's ``capture_manager`` for exactly the records
    emitted inside an isolation. ``emit`` then leaves ``sys.stdout``/``sys.stderr``
    alone; the line still gets written, because the handler's stream is the
    terminal reporter, which holds its own file object and never consults
    ``sys.stdout``. Outside an isolation nothing changes at all.

    Idempotent: ``pytest_configure`` runs once per process, but a nested or
    re-entered configure must not stack wrappers.
    """
    from _pytest.logging import _LiveLoggingStreamHandler

    original_emit = _LiveLoggingStreamHandler.emit
    if getattr(original_emit, "_clm_clirunner_guarded", False):
        return

    def emit(self, record):
        if self.capture_manager is not None and _inside_click_runner():
            saved, self.capture_manager = self.capture_manager, None
            try:
                original_emit(self, record)
            finally:
                self.capture_manager = saved
        else:
            original_emit(self, record)

    emit._clm_clirunner_guarded = True  # type: ignore[attr-defined]
    _LiveLoggingStreamHandler.emit = emit  # type: ignore[method-assign]


def pytest_configure(config):
    """Configure pytest and set default log levels.

    By default, suppress application logs during tests unless explicitly enabled.
    Note: External tool paths are configured at module import via _setup_external_tools()
    to avoid duplicate initialization.
    """
    # Register custom markers
    config.addinivalue_line(
        "markers", "requires_plantuml: mark test as requiring PlantUML JAR and Java"
    )
    config.addinivalue_line(
        "markers",
        "requires_drawio: mark test as requiring DrawIO executable "
        "(Unix/Linux: also needs DISPLAY; Windows: no DISPLAY needed)",
    )
    config.addinivalue_line(
        "markers",
        "requires_xvfb: [DEPRECATED] use requires_drawio instead - "
        "it works with both real displays and Xvfb",
    )
    config.addinivalue_line(
        "markers",
        "docker: mark test as requiring Docker daemon to be running",
    )

    # External tool paths are already configured by _setup_external_tools() at module import
    # This avoids duplicate initialization and speeds up startup

    # Click's CliRunner isolates output at the Python level only, so pytest's
    # live-log handler can (and does) clobber it mid-invocation. Neutralise that
    # before the first test runs; see the helper for the mechanism.
    _install_clirunner_live_log_guard()

    # Enable live logging if explicitly requested -- but never on an xdist
    # worker, where it is pure cost: execnet swallows the worker's terminal
    # output, so the live lines are *invisible* (measured: 0 "live log"
    # sections in the nightly's ``-n auto`` job vs 35 in its ``-n0`` docker
    # job) while each record still suspends and resumes global capture.
    log_level_name = os.environ.get("CLM_LOG_LEVEL", "INFO")
    if os.environ.get("CLM_ENABLE_TEST_LOGGING") and not _is_xdist_worker(config):
        config.option.log_cli = True
        config.option.log_cli_level = log_level_name
        config.option.log_cli_format = "[%(asctime)s] %(levelname)-8s %(name)s - %(message)s"
        config.option.log_cli_date_format = "%H:%M:%S"
    else:
        # Disable live logging by default
        config.option.log_cli = False
        if os.environ.get("CLM_ENABLE_TEST_LOGGING"):
            # ``--log-cli-level=INFO`` on the command line (both workflows pass
            # it) re-enables live logging all by itself -- see
            # ``_pytest.logging.LoggingPlugin._log_cli_enabled``. Clearing
            # ``log_cli`` alone would not be enough.
            config.option.log_cli_level = None
            # ...but keep the *report* sections as rich as they were. The live
            # handler's ``catching_logs(level=INFO)`` used to pull the root
            # logger down to INFO for the whole run, which is what let
            # third-party records (urllib3, docker, asyncio) reach ``caplog``
            # and the "Captured log call" section of a failure report. Those
            # lines are load-bearing when triaging an e2e timeout, so set
            # ``log_level`` explicitly to keep them.
            if config.option.log_level is None:
                config.option.log_level = log_level_name

    # Set all application loggers to WARNING by default to suppress INFO logs during tests
    # This prevents log spam in test output
    loggers_to_quiet = [
        "clm",
        "clm_common",
        "clm_faststream_backend",
    ]

    for logger_name in loggers_to_quiet:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


# ``tryfirst`` so the ``serial`` -> ``xdist_group`` mapping below runs before
# pytest-xdist's own (unordered) ``pytest_collection_modifyitems`` in
# ``xdist/remote.py``, which appends the ``@group`` suffix to each nodeid by
# reading the ``xdist_group`` mark. If we added the mark after that hook, the
# suffix would be missing and ``--dist loadgroup`` would scatter the tests.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Auto-skip tests based on tool availability; map ``serial`` to a group."""
    from tests.xdist_group_helpers import serial_group_name

    tool_status = get_tool_availability()

    # Tally serial-marked items per resulting load group so the meta-test
    # (``tests/test_serial_xdist_groups.py``) can confirm the heavy families
    # land in DISTINCT groups. This hook sees the FULL collected item list on
    # every xdist worker (collection runs per-worker before distribution), so
    # the counts are complete wherever the meta-test happens to execute.
    serial_group_counts: dict[str, int] = {}

    # Count tests by marker for reporting
    docker_tests = []
    plantuml_tests = []
    drawio_tests = []

    for item in items:
        markers = [marker.name for marker in item.iter_markers()]
        if "docker" in markers:
            docker_tests.append(item)
        if "requires_plantuml" in markers:
            plantuml_tests.append(item)
        if "requires_drawio" in markers or "requires_xvfb" in markers:
            drawio_tests.append(item)

    # Report tool availability once at the start
    if items:  # Only report if there are tests to run
        print("\n" + "=" * 70)
        print("External Tool Availability:")
        print(f"  PlantUML: {'✓ Available' if tool_status['plantuml'] else '✗ Not available'}")
        print(f"  DrawIO:   {'✓ Available' if tool_status['drawio'] else '✗ Not available'}")
        print(f"  Docker:   {'✓ Available' if tool_status['docker'] else '✗ Not available'}")

        # Show display status (platform-aware)
        if sys.platform == "win32":
            print("  Display:  ✓ Windows (native GUI, no DISPLAY needed)")
        else:
            # Unix/Linux - show DISPLAY status
            display = os.environ.get("DISPLAY", "not set")
            if tool_status["xvfb"]:
                print(f"  Display:  ✓ {display} (Xvfb)")
            elif display != "not set":
                print(f"  Display:  ✓ {display} (real display)")
            else:
                print("  Display:  ✗ not set (DrawIO needs DISPLAY on Unix/Linux)")

        # Report tests that will be skipped
        skipped_info = []
        if plantuml_tests and not tool_status["plantuml"]:
            skipped_info.append(f"{len(plantuml_tests)} PlantUML tests")
        if drawio_tests and not tool_status["drawio"]:
            skipped_info.append(f"{len(drawio_tests)} DrawIO tests")
        if docker_tests and not tool_status["docker"]:
            skipped_info.append(f"{len(docker_tests)} Docker tests")

        if skipped_info:
            print("-" * 70)
            print("WARNING: The following tests will be skipped:")
            for info in skipped_info:
                print(f"  - {info}")
            print("Run with these tools available for full test coverage.")

        print("=" * 70 + "\n")

    skip_plantuml = pytest.mark.skip(
        reason="PlantUML not available - set PLANTUML_JAR and ensure Java is installed"
    )

    # Platform-specific skip message for DrawIO
    if sys.platform == "win32":
        skip_drawio = pytest.mark.skip(reason="DrawIO not available - install DrawIO on Windows")
    else:
        skip_drawio = pytest.mark.skip(
            reason="DrawIO not available - install DrawIO and set DISPLAY environment variable (Unix/Linux)"
        )

    skip_docker = pytest.mark.skip(reason="Docker not available - ensure Docker daemon is running")

    for item in items:
        markers = [marker.name for marker in item.iter_markers()]

        # Check for requires_plantuml marker
        if "requires_plantuml" in markers:
            if not tool_status["plantuml"]:
                item.add_marker(skip_plantuml)

        # Check for requires_drawio marker (or deprecated requires_xvfb)
        if "requires_drawio" in markers or "requires_xvfb" in markers:
            if not tool_status["drawio"]:
                item.add_marker(skip_drawio)

        # Check for docker marker
        if "docker" in markers:
            if not tool_status["docker"]:
                item.add_marker(skip_docker)

        # Map the project's ``serial`` marker onto a PER-RESOURCE xdist load
        # group so the ``--dist loadgroup`` scheduler (see pyproject ``addopts``)
        # pins each contention-prone family onto ONE worker. The optional marker
        # argument names the resource class: ``@pytest.mark.serial`` -> the
        # default ``"serial"`` group; ``@pytest.mark.serial("subproc")`` ->
        # ``"serial-subproc"``. Distinct classes get distinct groups, so e.g. the
        # subprocess-spawning tests and the worker-pool tests each run
        # one-at-a-time *within* their class while the two classes run on
        # DIFFERENT workers concurrently — instead of all serial tests stacking
        # onto one worker (the single-bucket bottleneck). A no-op under ``-n0``
        # (one worker anyway).
        serial_marker = item.get_closest_marker("serial")
        if serial_marker is not None:
            resource_class = serial_marker.args[0] if serial_marker.args else None
            group = serial_group_name(resource_class)
            item.add_marker(pytest.mark.xdist_group(group))
            serial_group_counts[group] = serial_group_counts.get(group, 0) + 1

        # Give the heavier non-fast suites the generous per-test timeout CI
        # grants them (.github/workflows/ci.yml: integration --timeout=240,
        # e2e/docker --timeout=600), so running them LOCALLY (a non-default
        # ``-m`` selection) never false-kills against the tight fast-suite
        # default in ``[tool.pytest.ini_options] timeout`` (120s). A ``timeout``
        # marker takes precedence over the ini/CLI value; an explicit per-test
        # ``@pytest.mark.timeout`` is left untouched.
        if item.get_closest_marker("timeout") is None:
            if {"e2e", "slow", "docker"} & set(markers):
                item.add_marker(pytest.mark.timeout(600))
            elif "integration" in markers:
                item.add_marker(pytest.mark.timeout(240))

    # Expose the per-group tally for the split-invariant meta-test.
    setattr(config, "_clm_serial_group_counts", serial_group_counts)


@pytest.fixture(scope="function")
def configure_test_logging(request):
    """Configure logging for individual tests.

    This fixture can be used explicitly in tests that need logging,
    and is automatically applied to tests with e2e or integration markers.

    Environment variables:
    - CLM_LOG_LEVEL: Log level (DEBUG, INFO, WARNING, ERROR) - default: INFO
    - CLM_LOGGING__TESTING__E2E_PROGRESS_INTERVAL: Seconds between progress updates (default: 5)
    - CLM_LOGGING__TESTING__E2E_LONG_JOB_THRESHOLD: Seconds before warning about long jobs (default: 30)
    """
    # Get log level from environment, default to INFO
    log_level_name = os.environ.get("CLM_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Enable live logging for this test
    request.config.option.log_cli = True
    request.config.option.log_cli_level = log_level_name
    if not request.config.option.log_cli_format:
        request.config.option.log_cli_format = (
            "[%(asctime)s] %(levelname)-8s %(name)s - %(message)s"
        )
    if not request.config.option.log_cli_date_format:
        request.config.option.log_cli_date_format = "%H:%M:%S"

    # Store original log levels to restore after test
    original_levels = {}
    loggers_to_configure = [
        "clm",
        "clm_common",
        "clm_faststream_backend",
    ]

    for logger_name in loggers_to_configure:
        logger = logging.getLogger(logger_name)
        original_levels[logger_name] = logger.level
        logger.setLevel(log_level)

    # Log configuration for this test
    logging.info(f"Test logging configured for {request.node.name}: level={log_level_name}")

    yield

    # Restore original log levels
    for logger_name, original_level in original_levels.items():
        logging.getLogger(logger_name).setLevel(original_level)

    # Disable live logging after test
    if not os.environ.get("CLM_ENABLE_TEST_LOGGING"):
        request.config.option.log_cli = False


@pytest.fixture(scope="function", autouse=True)
def auto_configure_logging_for_marked_tests(request):
    """Automatically configure logging for tests with specific markers."""
    # Check if test has e2e or integration marker
    markers = [marker.name for marker in request.node.iter_markers()]

    if "e2e" in markers or "integration" in markers:
        # Invoke the configure_test_logging fixture
        request.getfixturevalue("configure_test_logging")


@pytest.fixture(scope="function", autouse=True)
def _restore_worker_global_state():
    """Restore worker-global logging/config state mutated by a test (#694).

    The 2026-07-26 nightly flaked on a three-hop chain across one xdist
    worker: a test reloaded the process-global ``ClmConfig`` singleton under
    a monkeypatched ``CLM_LOGGING__LOG_LEVEL=ERROR`` (monkeypatch reverts the
    env var, not the singleton); a later in-process ``clm build`` resolved
    the poisoned value and applied it via ``setup_logging`` →
    ``getLogger("clm").setLevel(ERROR)``; every later ``clm.*`` WARNING on
    that worker died at the gate and a ``caplog`` assertion 300 tests
    downstream failed. Snapshot the clm logger chain (level/disabled/
    propagate) and the config singleton before each test and restore both
    after it, so no test can poison its successors. Pinned by
    ``tests/test_global_state_isolation.py``.

    The root logger's *handler list* is snapshotted for the same reason. pytest
    attaches its live-log and log-file handlers to the root logger once, for the
    whole run loop, so a test that clears the root logger removes them for every
    later test in that worker process — permanently, because nothing re-adds
    them. ``clm.cli.commands.shared.setup_logging`` used to do exactly that, and
    the resulting immunity is what made an unrelated capture bug look like a
    1-in-5 nightly flake (``docs/claude/design/test-flakiness-root-causes.md``).
    That function is fixed; this is the belt to its braces. Handlers are only
    detached and re-attached here, never closed — closing a handler this fixture
    does not own is the very mistake being guarded against.
    """
    import clm.infrastructure.config as config_module

    chain = (
        "clm",
        "clm_cli",
        "clm_common",
        "clm_faststream_backend",
        "clm.workers",
        "clm.workers.notebook",
        "clm.workers.notebook.notebook_processor",
        "clm.infrastructure",
        "clm.infrastructure.database",
        "clm.infrastructure.database.executed_notebook_cache",
        "clm.release",
    )
    loggers = [logging.getLogger(name) for name in chain]
    snapshot = [(lg.level, lg.disabled, lg.propagate) for lg in loggers]
    root_logger = logging.getLogger()
    root_handlers = list(root_logger.handlers)
    previous_config = config_module._config
    try:
        yield
    finally:
        for lg, (level, disabled, propagate) in zip(loggers, snapshot, strict=True):
            lg.setLevel(level)
            lg.disabled = disabled
            lg.propagate = propagate
        if root_logger.handlers != root_handlers:
            root_logger.handlers[:] = root_handlers
        config_module._config = previous_config


@pytest.fixture(scope="function", autouse=True)
def _neutralise_pool_size_cap(monkeypatch, request):
    """Pin the Fix 4 pool-size cap helpers to effectively-unlimited values.

    Fix 4 (``compute_pool_size_cap``) clamps worker counts against the
    host's CPU count and total RAM so a spec file tuned for a build
    farm cannot saturate a dev laptop. That is *operational*
    behaviour; inside the test suite it is pure noise — a test that
    requests 8 notebook workers on a 4-core CI runner gets 2 instead
    of 8 and fails for reasons unrelated to what it is checking.

    This autouse fixture pins ``_compute_cpu_cap`` and
    ``_compute_mem_cap`` to a very large number (128) for every test
    by default, so the clamp is a no-op unless the test explicitly
    re-patches those helpers with smaller values.

    Exception: the dedicated ``test_pool_size_cap`` module probes the
    real helper implementations directly (``pool_size_cap._compute_cpu_cap()``)
    via ``patch`` on ``os.cpu_count`` and ``psutil.virtual_memory``.
    Replacing the helpers with lambdas there would short-circuit the
    very code under test, so this fixture is a no-op for that file.
    Tests in ``test_config.py::TestWorkerManagementConfig`` that
    exercise the clamp through ``get_worker_config`` also pin their
    own values via ``monkeypatch`` and simply override what this
    fixture set — that works because pytest applies fixture patches
    in order.

    Also clears ``CLM_MAX_WORKERS`` from the environment so an
    operator's personal cap (set in their shell profile) cannot leak
    into test runs. Tests that want to assert the env-var plumbing
    can ``monkeypatch.setenv`` it back.

    PRODUCTION VALUE COVERED BY (finding T8):
      tests/infrastructure/workers/test_pool_size_cap.py — the whole module,
      exempted above by the ``test_pool_size_cap`` nodeid check.
    INTERACTION COVERED BY:
      tests/infrastructure/workers/test_pool_size_cap_interaction.py — the
      clamp firing *during managed-worker startup*, which unit tests of the
      helper in isolation cannot see. This was the widest gap the review
      identified: every neutraliser here is individually justified, but the
      suite could not observe what happens when the clamp actually engages on
      the path that uses it.
    """
    # Match the module FILE, not a bare substring. The old check was
    # ``"test_pool_size_cap" in nodeid``, which silently exempted any module
    # whose name merely started that way — including
    # ``test_pool_size_cap_interaction.py``, which then saw the *real* host CPU
    # and RAM caps and became environment-dependent (3 requested workers
    # resolved to 3 on a 32-core dev box and to 2 on a 2-core CI runner).
    if "test_pool_size_cap.py" in request.node.nodeid:
        return

    monkeypatch.setattr(
        "clm.infrastructure.workers.pool_size_cap._compute_cpu_cap",
        lambda: 128,
    )
    monkeypatch.setattr(
        "clm.infrastructure.workers.pool_size_cap._compute_mem_cap",
        lambda: 128,
    )
    monkeypatch.delenv("CLM_MAX_WORKERS", raising=False)


# ====================================================================
# Test Failure Diagnostics
# ====================================================================


def _dump_job_queue_state(db_path: Path) -> str:
    """Generate a diagnostic dump of job queue state.

    This is called when tests fail to help diagnose the root cause.

    Args:
        db_path: Path to the SQLite database

    Returns:
        str: Formatted diagnostic output
    """
    import sqlite3

    lines = []
    lines.append("=" * 70)
    lines.append("JOB QUEUE DIAGNOSTIC DUMP")
    lines.append("=" * 70)

    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row

        # Job summary by status
        cursor = conn.execute(
            "SELECT status, COUNT(*) as count FROM jobs GROUP BY status ORDER BY status"
        )
        rows = cursor.fetchall()
        lines.append("\nJob Summary by Status:")
        for row in rows:
            lines.append(f"  {row['status']}: {row['count']}")

        # Failed jobs with details
        cursor = conn.execute(
            """SELECT id, job_type, input_file, output_file, error, created_at, completed_at
               FROM jobs WHERE status = 'failed' ORDER BY id"""
        )
        failed_jobs = cursor.fetchall()
        if failed_jobs:
            lines.append(f"\nFailed Jobs ({len(failed_jobs)}):")
            for job in failed_jobs:
                lines.append(f"\n  Job #{job['id']} ({job['job_type']}):")
                lines.append(f"    Input:  {job['input_file']}")
                lines.append(f"    Output: {job['output_file']}")
                lines.append(f"    Error:  {job['error'] or 'No error message'}")
                lines.append(f"    Created: {job['created_at']}")
                lines.append(f"    Completed: {job['completed_at']}")

        # Pending/Processing jobs (might indicate stuck jobs)
        cursor = conn.execute(
            """SELECT id, job_type, input_file, worker_id, status, created_at
               FROM jobs WHERE status IN ('pending', 'processing') ORDER BY id"""
        )
        stuck_jobs = cursor.fetchall()
        if stuck_jobs:
            lines.append(f"\nPending/Processing Jobs ({len(stuck_jobs)}):")
            for job in stuck_jobs:
                lines.append(f"\n  Job #{job['id']} ({job['job_type']}) - {job['status']}:")
                lines.append(f"    Input: {job['input_file']}")
                lines.append(f"    Worker: {job['worker_id'] or 'Not assigned'}")
                lines.append(f"    Created: {job['created_at']}")

        # Worker status
        cursor = conn.execute(
            """SELECT container_id, worker_type, status, execution_mode, last_heartbeat
               FROM workers ORDER BY worker_type, container_id"""
        )
        workers = cursor.fetchall()
        if workers:
            lines.append(f"\nWorker Status ({len(workers)}):")
            for worker in workers:
                lines.append(
                    f"  {worker['container_id']}: {worker['status']} "
                    f"(type={worker['worker_type']}, mode={worker['execution_mode']}, "
                    f"heartbeat={worker['last_heartbeat']})"
                )

        conn.close()

    except Exception as e:
        lines.append(f"\nError reading database: {e}")

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


def _dump_worker_logs(workspace_path: Path) -> str:
    """Dump any worker log files that might exist.

    Worker subprocesses spawned by the executor redirect their stderr to
    ``CLM_LOG_DIR/workers/<worker>-<n>.log`` — under pytest that's the
    per-test isolated log dir (``_isolate_clm_log_dir``), not the workspace.
    Harvest both surfaces so a failed integration test shows what its
    workers actually said (boot lines, registration, job processing).

    Args:
        workspace_path: Path to the workspace directory

    Returns:
        str: Formatted log content or empty string if no logs
    """
    lines = []

    # Look for log files in workspace
    log_patterns = ["*.log", "worker*.log", "clm*.log"]
    for pattern in log_patterns:
        for log_file in workspace_path.glob(pattern):
            try:
                content = log_file.read_text(encoding="utf-8", errors="replace")
                if content.strip():
                    lines.append(f"\n--- {log_file.name} ---")
                    # Limit to last 100 lines
                    log_lines = content.strip().split("\n")
                    if len(log_lines) > 100:
                        lines.append(f"[... {len(log_lines) - 100} lines omitted ...]")
                        log_lines = log_lines[-100:]
                    lines.extend(log_lines)
            except Exception as e:
                lines.append(f"\nError reading {log_file}: {e}")

    # Harvest worker logs from the isolated CLM_LOG_DIR as well (the executor
    # writes ``workers/<name>-<n>.log`` there; before the logging fix these
    # files were empty, which is why failed worker tests had nothing to show)
    log_dir = os.environ.get("CLM_LOG_DIR")
    if log_dir:
        workers_dir = Path(log_dir) / "workers"
        for log_file in sorted(workers_dir.glob("*.log")) if workers_dir.is_dir() else []:
            try:
                content = log_file.read_text(encoding="utf-8", errors="replace")
                lines.append(f"\n--- workers/{log_file.name} ---")
                log_lines = content.strip().split("\n") if content.strip() else ["<empty>"]
                if len(log_lines) > 100:
                    lines.append(f"[... {len(log_lines) - 100} lines omitted ...]")
                    log_lines = log_lines[-100:]
                lines.extend(log_lines)
            except Exception as e:
                lines.append(f"\nError reading {log_file}: {e}")

    if lines:
        header = ["=" * 70, "WORKER LOG FILES", "=" * 70]
        return "\n".join(header + lines)
    return ""


@pytest.fixture(scope="function")
def diagnostic_on_failure(request, tmp_path):
    """Fixture that dumps diagnostic information when a test fails.

    This fixture is automatically used by e2e and integration tests
    via the auto_diagnose_on_failure fixture.

    It captures:
    - Job queue state (pending, failed, completed jobs)
    - Worker status
    - Any log files in the workspace
    """
    # Store db_path and workspace_path if set by the test
    diagnostic_context = {"db_path": None, "workspace_path": None}

    def set_db_path(path: Path):
        diagnostic_context["db_path"] = path

    def set_workspace_path(path: Path):
        diagnostic_context["workspace_path"] = path

    # Expose setters for tests to use
    request.node.set_diagnostic_db_path = set_db_path
    request.node.set_diagnostic_workspace_path = set_workspace_path

    yield diagnostic_context

    # After test - if failed, dump diagnostics
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        print("\n" + "!" * 70)
        print("TEST FAILED - DUMPING DIAGNOSTICS")
        print("!" * 70)

        if diagnostic_context["db_path"]:
            print(_dump_job_queue_state(diagnostic_context["db_path"]))

        if diagnostic_context["workspace_path"]:
            logs = _dump_worker_logs(diagnostic_context["workspace_path"])
            if logs:
                print(logs)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Hook to capture test results for diagnostic output."""
    outcome = yield
    rep = outcome.get_result()

    # Store the result on the test item for the diagnostic fixture to use
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture(scope="function", autouse=True)
def auto_diagnose_on_failure(request):
    """Automatically enable diagnostic output for e2e and integration tests."""
    markers = [marker.name for marker in request.node.iter_markers()]

    if "e2e" in markers or "integration" in markers or "docker" in markers:
        # Request the diagnostic fixture
        request.getfixturevalue("diagnostic_on_failure")


@pytest.fixture(scope="session")
def e2e_test_data_template(tmp_path_factory):
    """Session-scoped template of test data (copied once per test session).

    This reduces E2E test overhead by copying DATA_DIR only once per session
    instead of once per test. Individual tests then copy from this template
    using hardlinks when possible for near-instant copies.

    Returns:
        Path: Path to the template directory containing test-data
    """
    template_dir = tmp_path_factory.mktemp("test-data-template")
    # Some tests write transient, per-test spec files under the shared
    # ``tests/test-data`` tree (the outline-command fixtures in
    # ``tests/cli/test_outline.py`` — a spec must live one level under
    # test-data so ``resolve_course_paths``' grandparent rule finds the shared
    # ``slides/`` sibling). Under xdist that races this copytree on Windows:
    # ``os.scandir`` sees a file that has been unlinked by the time copytree
    # opens it (``[WinError 2]``). Those fixtures write into a dedicated
    # ``_volatile_specs/`` subdirectory (M-2); excluding that ONE directory
    # wholesale is robust to whatever filename a future fixture chooses — unlike
    # the previous ``test-spec-*-test_*.xml`` filename-substring glob, which a
    # differently-named volatile spec would silently defeat. The old pattern is
    # kept too, as a belt-and-suspenders for any stray writer outside that dir.
    shutil.copytree(
        DATA_DIR,
        template_dir / "test-data",
        ignore=shutil.ignore_patterns("_volatile_specs", "test-spec-*-test_*.xml"),
    )
    return template_dir / "test-data"


def _link_or_copy(src, dst, *, follow_symlinks=True):
    """``copytree`` copy_function: hardlink for speed, byte-copy per file on failure.

    The old approach tried ``copytree(copy_function=os.link)`` wholesale and, on
    ``OSError``, retried with a plain ``copytree`` — but the first attempt may
    have already created ``dst`` and linked some files before failing on one,
    so the retry hit ``FileExistsError`` against the now-partial directory (and
    the trigger scales with worker count: Windows handle pressure under parallel
    load makes a mid-copy ``os.link`` failure load-correlated). Degrading only
    the *single* failing file to ``shutil.copy2`` keeps the hardlink speed win
    for every other file and can never leave a partial tree for a retry to trip
    on. ``copy2`` preserves metadata, so the fallback is faithful.
    """
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst, follow_symlinks=follow_symlinks)


@pytest.fixture
def e2e_test_data_copy(tmp_path, e2e_test_data_template):
    """Copy test-data to temp directory for E2E testing.

    Uses hardlinks from the session-scoped template for fast per-test copies,
    falling back to a byte copy per file (see :func:`_link_or_copy`) when a
    hardlink can't be made.

    Returns:
        tuple: (data_dir, output_dir) where data_dir is the copied test-data
               and output_dir is a separate directory for output files.
    """
    data_dir = tmp_path / "test-data"
    output_dir = tmp_path / "output"

    # Hardlink each file (O(n) files, not O(n) bytes); a file that can't be
    # linked degrades to a byte copy without aborting the whole tree.
    shutil.copytree(e2e_test_data_template, data_dir, copy_function=_link_or_copy)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    return data_dir, output_dir


@pytest.fixture
def e2e_course_1(course_1_spec, e2e_test_data_copy):
    """Course 1 instance for E2E testing with temp directories."""
    from clm.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_1_spec, data_dir, output_dir)
    return course


@pytest.fixture
def e2e_course_2(course_2_spec, e2e_test_data_copy):
    """Course 2 instance for E2E testing with temp directories."""
    from clm.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_2_spec, data_dir, output_dir)
    return course


# New course specs for testing edge cases

COURSE_3_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/simple-notebook-de</de>
        <en>https://github.com/hoelzl/simple-notebook-en</en>
    </github>
    <name>
        <de>Einfaches Notebook</de>
        <en>Simple Notebook</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Kurs mit nur einem einfachen Notebook</de>
        <en>A course with just a simple notebook</en>
    </description>
    <certificate>
        <de>Zertifikat für Einfaches Notebook</de>
        <en>Certificate for Simple Notebook</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Einziger Abschnitt</de>
                <en>Single Section</en>
            </name>
            <topics>
                <topic>simple_notebook</topic>
            </topics>
        </section>
    </sections>
</course>
"""

COURSE_4_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/simple-plantuml-de</de>
        <en>https://github.com/hoelzl/simple-plantuml-en</en>
    </github>
    <name>
        <de>Einfaches PlantUML</de>
        <en>Simple PlantUML</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Kurs mit nur einer PlantUML-Datei</de>
        <en>A course with just a PlantUML file</en>
    </description>
    <certificate>
        <de>Zertifikat für Einfaches PlantUML</de>
        <en>Certificate for Simple PlantUML</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Einziger Abschnitt</de>
                <en>Single Section</en>
            </name>
            <topics>
                <topic>simple_plantuml</topic>
            </topics>
        </section>
    </sections>
</course>
"""

COURSE_5_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/simple-drawio-de</de>
        <en>https://github.com/hoelzl/simple-drawio-en</en>
    </github>
    <name>
        <de>Einfaches Drawio</de>
        <en>Simple Drawio</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Kurs mit nur einer Draw.io-Datei</de>
        <en>A course with just a Draw.io file</en>
    </description>
    <certificate>
        <de>Zertifikat für Einfaches Drawio</de>
        <en>Certificate for Simple Drawio</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Einziger Abschnitt</de>
                <en>Single Section</en>
            </name>
            <topics>
                <topic>simple_drawio</topic>
            </topics>
        </section>
    </sections>
</course>
"""


@pytest.fixture(scope="session")
def course_3_spec():
    """Session-scoped CourseSpec for COURSE_3_XML (simple notebook)."""
    from clm.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_3_XML)
    return CourseSpec.from_file(xml_stream)


@pytest.fixture(scope="session")
def course_4_spec():
    """Session-scoped CourseSpec for COURSE_4_XML (simple plantuml)."""
    from clm.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_4_XML)
    return CourseSpec.from_file(xml_stream)


@pytest.fixture(scope="session")
def course_5_spec():
    """Session-scoped CourseSpec for COURSE_5_XML (simple drawio)."""
    from clm.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_5_XML)
    return CourseSpec.from_file(xml_stream)


@pytest.fixture
def e2e_course_3(course_3_spec, e2e_test_data_copy):
    """Course 3 instance for E2E testing with temp directories (single notebook only)."""
    from clm.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_3_spec, data_dir, output_dir)
    return course


@pytest.fixture
def e2e_course_4(course_4_spec, e2e_test_data_copy):
    """Course 4 instance for E2E testing with temp directories (single plantuml only)."""
    from clm.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_4_spec, data_dir, output_dir)
    return course


@pytest.fixture
def e2e_course_5(course_5_spec, e2e_test_data_copy):
    """Course 5 instance for E2E testing with temp directories (single draw.io only)."""
    from clm.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_5_spec, data_dir, output_dir)
    return course


# =============================================================================
# Mock Worker Fixtures
# =============================================================================


@pytest.fixture
def mock_db_path(tmp_path):
    """Create a temporary database for mock worker tests."""
    from clm.infrastructure.database.schema import init_database

    db_path = tmp_path / "mock_test.db"
    init_database(db_path)
    return db_path


@pytest.fixture
def mock_workspace_path(tmp_path):
    """Create a temporary workspace directory for mock worker tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def mock_worker_pool(mock_db_path):
    """Create a mock worker pool for testing.

    This fixture provides a MockWorkerPool instance that can be used to
    start mock workers for fast integration testing. The pool is automatically
    cleaned up after the test.

    Example:
        def test_worker_lifecycle(mock_worker_pool):
            workers = mock_worker_pool.start_workers("notebook", count=2)
            assert len(workers) == 2
            # Workers are automatically stopped after the test
    """
    from tests.fixtures.mock_workers import MockWorkerPool

    pool = MockWorkerPool(mock_db_path)
    yield pool
    pool.stop_all()


@pytest.fixture
def mock_notebook_workers(mock_worker_pool):
    """Start 2 mock notebook workers for testing.

    Returns a list of 2 MockWorker instances already started and ready
    to process jobs.
    """
    workers = mock_worker_pool.start_workers("notebook", count=2)
    assert mock_worker_pool.wait_for_workers_registered(), "Mock notebook workers did not register"
    return workers
