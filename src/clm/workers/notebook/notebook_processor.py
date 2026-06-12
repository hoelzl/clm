import asyncio
import copy
import logging
import os
import re
import time
import warnings
from base64 import b64decode
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha3_224
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast

import jupytext.config as jupytext_config  # type: ignore[import-untyped]
import psutil  # type: ignore[import-untyped]
import traitlets.log
from jinja2 import (
    ChoiceLoader,
    DictLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    StrictUndefined,
)
from jupyter_client.manager import AsyncKernelManager
from jupytext import jupytext
from nbconvert import HTMLExporter
from nbconvert.preprocessors import ExecutePreprocessor
from nbformat import NotebookNode
from nbformat.validator import normalize

from clm.infrastructure.database.worker_heartbeats import WorkerHeartbeatStore
from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.infrastructure.workers.process_reaper import terminate_then_kill_procs

from .output_spec import (
    POST_WORKSHOP_TAG,
    OutputSpec,
    PartialOutput,
    _is_in_workshop,
    find_workshop_ranges,
)

if TYPE_CHECKING:
    from typing import Protocol

    class ExecutedNotebookCacheLike(Protocol):
        """Structural interface for the executed_notebooks cache.

        Both the SQLite-backed :class:`ExecutedNotebookCache` (direct mode)
        and ``ApiExecutedNotebookCache`` (Docker mode) satisfy this shape.
        Only ``get`` and ``store`` are required — the maintenance methods on
        the SQLite implementation are not used inside ``NotebookProcessor``.
        """

        def get(
            self,
            input_file: str,
            content_hash: str,
            language: str,
            prog_lang: str,
        ) -> "NotebookNode | None": ...

        def store(
            self,
            input_file: str,
            content_hash: str,
            language: str,
            prog_lang: str,
            executed_notebook: "NotebookNode",
        ) -> None: ...


from clm.infrastructure.messaging.base_classes import ProcessingWarning
from clm.slides.cpp_code_emitter import emit_cpp_translation_unit

from .utils.jupyter_utils import (
    Cell,
    get_cell_type,
    get_conflicting_slide_tags,
    get_invalid_code_tags,
    get_invalid_markdown_tags,
    get_slide_tag,
    get_tags,
    is_answer_cell,
    is_code_cell,
    is_markdown_cell,
)
from .utils.prog_lang_utils import (
    jinja_prefix_for,
    jupytext_format_for,
    kernelspec_for,
    language_info,
)


def string_to_list(string: str) -> list[str]:
    return [s.strip() for s in string.split(",")]


# Configuration
JINJA_LINE_STATEMENT_PREFIX = os.environ.get("JINJA_LINE_STATEMENT_PREFIX", "# j2")
JINJA_TEMPLATES_PREFIX = os.environ.get("JINJA_TEMPLATES_PATH", "templates")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
LOG_CELL_PROCESSING = os.environ.get("LOG_CELL_PROCESSING", "False") == "True"
NUM_RETRIES_FOR_HTML = 6

# Cells slower than this are logged at INFO so a stalling notebook is
# visible without enabling DEBUG (issue #143). Configurable via
# CLM_SLOW_CELL_LOG_THRESHOLD_SECONDS for diagnosing builds with an
# unusual I/O profile. Default 60s is well above the ~20s slowest cell
# observed in the issue's direct jupyter-execute baseline.
try:
    _SLOW_CELL_LOG_THRESHOLD_SECONDS = float(
        os.environ.get("CLM_SLOW_CELL_LOG_THRESHOLD_SECONDS", "60")
    )
except ValueError:
    _SLOW_CELL_LOG_THRESHOLD_SECONDS = 60.0

# Optional per-cell execution timeout for the build worker, in seconds.
# CLM normally runs cells with timeout=None (no per-cell limit), which
# means a cell whose kernel never returns to idle blocks the worker
# forever until the build-level job timeout fires (issue #143). Setting
# CLM_CELL_TIMEOUT_SECONDS to a positive value passes that value as
# nbclient's per-cell ``timeout`` so a stuck cell raises a
# CellTimeoutError (surfaced as a normal cell error) instead of hanging
# the whole build. Unset / non-positive keeps the historical no-timeout
# behavior so existing builds are unaffected.
try:
    _raw_cell_timeout = float(os.environ.get("CLM_CELL_TIMEOUT_SECONDS", "0"))
    CELL_EXECUTION_TIMEOUT: int | None = int(_raw_cell_timeout) if _raw_cell_timeout > 0 else None
except ValueError:
    CELL_EXECUTION_TIMEOUT = None

# Defense-in-depth (issues #143/#165): when HTTP replay is engaged, default a
# generous per-cell timeout for replay-engaged jobs only, so any replay-layer
# hang surfaces as a clean CellTimeoutError instead of stalling silently until
# the build-level job timeout fires (the legacy in-kernel vcrpy transport
# deadlocked exactly that way). Real cells in the LLM/RAG decks that use replay
# finish in seconds, so only a genuine hang reaches this ceiling. An explicit
# CLM_CELL_TIMEOUT_SECONDS always wins; set
# CLM_HTTP_REPLAY_CELL_TIMEOUT_SECONDS=0 to opt out of the default.
#
# A strict-replay miss is engineered to fail FAST on its own: the proxy addon
# returns a non-retryable 4xx (404), so the SDK raises on the first attempt
# rather than retrying it as a 5xx (an earlier 599 was retried, amplifying a
# miss across a deck's ``.batch()`` calls until this ceiling fired — measured
# at ~1200s). NOTE: langchain's agent/retry layer ABOVE the SDK may still
# re-issue a missed request, so this timeout remains the load-bearing backstop
# for stale-cassette misses in such decks — keep it even though the transport
# itself never hangs.
try:
    _raw_replay_cell_timeout = float(os.environ.get("CLM_HTTP_REPLAY_CELL_TIMEOUT_SECONDS", "600"))
    _HTTP_REPLAY_DEFAULT_CELL_TIMEOUT: int | None = (
        int(_raw_replay_cell_timeout) if _raw_replay_cell_timeout > 0 else None
    )
except ValueError:
    _HTTP_REPLAY_DEFAULT_CELL_TIMEOUT = 600


def _effective_cell_timeout(payload: "NotebookPayload") -> int | None:
    """Resolve the per-cell nbclient timeout for a single notebook job.

    An explicit ``CLM_CELL_TIMEOUT_SECONDS`` always wins. Otherwise, when HTTP
    replay is engaged for this job (any mode but ``disabled``), fall back to the
    generous replay default so a replay-layer hang surfaces as a clean
    ``CellTimeoutError`` instead of stalling to the build-level job timeout
    (issue #143). Non-replay builds keep the historical no-timeout behavior.
    """
    if CELL_EXECUTION_TIMEOUT is not None:
        return CELL_EXECUTION_TIMEOUT
    mode = getattr(payload, "http_replay_mode", None)
    if mode and mode != "disabled":
        return _HTTP_REPLAY_DEFAULT_CELL_TIMEOUT
    return None


# Replay modes for which the cassette-routing tag bootstrap is injected.
# "disabled" is intentionally absent — in that mode the bootstrap cell is
# not injected at all. The proxy-side semantics of each mode live in
# clm.infrastructure.http_replay_mitm.addon (MODE_* constants).
_HTTP_REPLAY_VALID_MODES = frozenset({"replay", "once", "new-episodes", "refresh"})

_HTTP_REPLAY_BOOTSTRAP_MARKER = "http_replay"

# Hosts whose traffic the replay proxy should not record into the cassette. Defaults
# cover LangSmith's telemetry upload endpoint (request bodies contain
# per-build timestamps + UUIDs, defeating the body matcher and causing
# stale-source builds to grow cassettes indefinitely). Add more here as
# we encounter other telemetry endpoints with the same shape, or override
# at build time via ``CLM_HTTP_REPLAY_IGNORE_HOSTS`` (comma-separated).
_DEFAULT_HTTP_REPLAY_IGNORE_HOSTS = ("api.smith.langchain.com",)


def resolve_http_replay_ignore_hosts() -> tuple[str, ...]:
    """Resolve the http-replay ignore-hosts list from the environment.

    Unset ``CLM_HTTP_REPLAY_IGNORE_HOSTS`` → the default
    (:data:`_DEFAULT_HTTP_REPLAY_IGNORE_HOSTS`); set (even to the empty string)
    → the comma-separated override, where an empty string means "record every
    host". Consumed by ``build._maybe_start_mitmproxy_transport``, which passes
    it to the replay proxy addon as ``clm_ignore_hosts``.
    """
    raw = os.environ.get("CLM_HTTP_REPLAY_IGNORE_HOSTS")
    if raw is None:
        return _DEFAULT_HTTP_REPLAY_IGNORE_HOSTS
    return tuple(host.strip() for host in raw.split(",") if host.strip())


# The cassette-routing tag bootstrap (issue #165, P2), injected into the
# kernel when a topic opts into HTTP replay. It does not patch httpcore or
# enter any cassette context — it only tags every outgoing request with
# the destination cassette path so the single shared mitmproxy can demux
# flows to the correct per-(topic,language,kind) cassette. The proxy
# strips the ``X-CLM-Cassette`` header before recording or forwarding.
# Three client stacks are tagged (the same ones decks actually use; an
# untagged client records into the build's catch-all cassette instead of
# the topic's canonical one and replay silently breaks):
#
# * ``httpx`` — ``Client.send``/``AsyncClient.send`` (openai/langchain
#   route through these);
# * ``requests`` — ``Session.send`` (the module-level ``requests.get``
#   helpers create a ``Session`` and funnel through it, so one class
#   patch covers everything);
# * ``aiohttp`` — ``ClientSession._request`` (every verb helper funnels
#   through it; a plain-def wrapper returning the coroutine keeps the
#   ``_RequestContextManager`` protocol intact).
#
# requests/aiohttp are optional in the kernel env, hence the import
# guards. All patches are on the *classes*, so clients created before or
# after this cell are covered. One tag per kernel (fresh kernel per
# notebook), captured in the closure. No literal curly braces in the body
# so ``str.format`` only substitutes ``{tag!r}``.
_HTTP_REPLAY_TAG_BOOTSTRAP_TEMPLATE = """\
# CLM HTTP REPLAY TAG BOOTSTRAP - DO NOT EDIT
import httpx as _clm_httpx
_CLM_CASSETTE_TAG = {tag!r}
if not getattr(_clm_httpx.Client.send, "_clm_tagged", False):
    _clm_orig_send = _clm_httpx.Client.send
    def _clm_tagged_send(self, request, *args, **kwargs):
        request.headers["x-clm-cassette"] = _CLM_CASSETTE_TAG
        return _clm_orig_send(self, request, *args, **kwargs)
    _clm_tagged_send._clm_tagged = True
    _clm_httpx.Client.send = _clm_tagged_send
if not getattr(_clm_httpx.AsyncClient.send, "_clm_tagged", False):
    _clm_orig_asend = _clm_httpx.AsyncClient.send
    async def _clm_tagged_asend(self, request, *args, **kwargs):
        request.headers["x-clm-cassette"] = _CLM_CASSETTE_TAG
        return await _clm_orig_asend(self, request, *args, **kwargs)
    _clm_tagged_asend._clm_tagged = True
    _clm_httpx.AsyncClient.send = _clm_tagged_asend
try:
    import requests as _clm_requests
except ImportError:
    _clm_requests = None
if _clm_requests is not None and not getattr(
    _clm_requests.Session.send, "_clm_tagged", False
):
    _clm_orig_rsend = _clm_requests.Session.send
    def _clm_tagged_rsend(self, request, *args, **kwargs):
        request.headers["x-clm-cassette"] = _CLM_CASSETTE_TAG
        return _clm_orig_rsend(self, request, *args, **kwargs)
    _clm_tagged_rsend._clm_tagged = True
    _clm_requests.Session.send = _clm_tagged_rsend
try:
    import aiohttp as _clm_aiohttp
    from multidict import CIMultiDict as _clm_CIMultiDict
except ImportError:
    _clm_aiohttp = None
if _clm_aiohttp is not None and not getattr(
    _clm_aiohttp.ClientSession._request, "_clm_tagged", False
):
    _clm_orig_aio_request = _clm_aiohttp.ClientSession._request
    def _clm_tagged_aio_request(self, method, str_or_url, **kwargs):
        _headers = kwargs.pop("headers", None)
        _merged = _clm_CIMultiDict(_headers) if _headers else _clm_CIMultiDict()
        _merged["x-clm-cassette"] = _CLM_CASSETTE_TAG
        return _clm_orig_aio_request(self, method, str_or_url, headers=_merged, **kwargs)
    _clm_tagged_aio_request._clm_tagged = True
    _clm_aiohttp.ClientSession._request = _clm_tagged_aio_request
"""


# Socket-only forensic trace for the replay proxy (issue #165 P5). Appended
# to the tag bootstrap when CLM_HTTP_REPLAY_TRACE=1. Fully self-contained —
# it imports its own json/atexit and only installs the ``socket.connect``
# audit hook (Stream 1, the ground truth). The proxy-side ``proxy`` stream
# (Stream 2′, written by the addon) supplies the interception evidence.
# It writes ``worker-<pid>.jsonl`` in the layout
# ``analyze_http_replay_trace.py`` expects. No literal ``{}`` in the body
# except the doubled ``{{}}`` so ``str.format`` only fills ``{trace_dir!r}``.
_HTTP_REPLAY_SOCKET_TRACE_TEMPLATE = """\
# CLM HTTP REPLAY SOCKET TRACE - DO NOT EDIT
_clm_strace_dir = {trace_dir!r}
if _clm_strace_dir:
    import atexit as _clm_st_atexit
    import json as _clm_st_json
    import os as _clm_st_os
    import socket as _clm_st_socket  # noqa: F401
    import sys as _clm_st_sys
    import threading as _clm_st_threading
    import time as _clm_st_time
    from datetime import datetime as _clm_st_datetime, timezone as _clm_st_timezone

    _clm_st_os.makedirs(_clm_strace_dir, exist_ok=True)
    _clm_strace_path = _clm_st_os.path.join(
        _clm_strace_dir, "worker-" + str(_clm_st_os.getpid()) + ".jsonl"
    )
    _clm_strace_fh = open(_clm_strace_path, "a", encoding="utf-8", newline="\\n")
    _clm_strace_lock = _clm_st_threading.Lock()
    _clm_strace_start = _clm_st_time.monotonic()

    def _clm_strace_emit(stream, event, data=None):
        try:
            record = {{
                "ts_mono": _clm_st_time.monotonic() - _clm_strace_start,
                "ts_wall": _clm_st_datetime.now(_clm_st_timezone.utc).isoformat(),
                "pid": _clm_st_os.getpid(),
                "tid": _clm_st_threading.get_ident(),
                "stream": stream,
                "event": event,
                "data": data or {{}},
            }}
            line = _clm_st_json.dumps(record) + "\\n"
            with _clm_strace_lock:
                _clm_strace_fh.write(line)
                _clm_strace_fh.flush()
        except Exception:
            pass

    def _clm_saudit_hook(event, args):
        try:
            if event == "socket.connect" and len(args) >= 2:
                address = args[1]
                if isinstance(address, tuple) and len(address) >= 2:
                    host, port = address[0], address[1]
                else:
                    host, port = repr(address), None
                _clm_strace_emit("socket", "connect", {{"host": host, "port": port}})
        except Exception:
            pass

    _clm_st_sys.addaudithook(_clm_saudit_hook)
    _clm_strace_emit("socket", "bootstrap.complete", {{"transport": "mitmproxy"}})

    def _clm_strace_close():
        try:
            _clm_strace_fh.flush()
            _clm_strace_fh.close()
        except Exception:
            pass

    _clm_st_atexit.register(_clm_strace_close)
"""


def _inject_http_replay_tag_bootstrap(nb: NotebookNode, tag: str, *, trace_dir: str = "") -> None:
    """Prepend the mitmproxy cassette-routing tag cell to ``nb``.

    ``tag`` is the absolute canonical cassette path the host-side merge
    will fold into. The cell carries the ``clm_injected`` marker so
    :func:`_strip_injected_cells` removes it before the notebook reaches
    HTML / the execution cache.

    When ``trace_dir`` is non-empty (CLM_HTTP_REPLAY_TRACE=1), the
    self-contained socket-only forensic trace is appended so the kernel emits
    the ``socket`` ground-truth stream to ``<trace_dir>/worker-<pid>.jsonl``
    (issue #165 P5). Empty string (the common case) leaves the cell as just
    the tag bootstrap.
    """
    from nbformat.v4 import new_code_cell

    source = _HTTP_REPLAY_TAG_BOOTSTRAP_TEMPLATE.format(tag=tag)
    if trace_dir:
        source += "\n" + _HTTP_REPLAY_SOCKET_TRACE_TEMPLATE.format(trace_dir=trace_dir)
    cell = new_code_cell(
        source=source,
        metadata={
            "tags": ["del"],
            "clm_injected": _HTTP_REPLAY_BOOTSTRAP_MARKER,
        },
    )
    nb["cells"].insert(0, cell)


def _strip_injected_cells(nb: NotebookNode) -> None:
    """Remove any cell previously added by ``_inject_http_replay_tag_bootstrap``."""
    cells = nb.get("cells", [])
    nb["cells"] = [
        c
        for c in cells
        if (c.get("metadata") or {}).get("clm_injected") != _HTTP_REPLAY_BOOTSTRAP_MARKER
    ]


# Jupytext builds metadata.jupytext.cell_metadata_filter (and
# notebook_metadata_filter) by joining a Python ``set`` of metadata keys.
# Set iteration order varies across processes because PYTHONHASHSEED is
# randomized by default, so the same .py source produces .ipynb files that
# differ on this one line (e.g. ``"tags,lang,-all"`` vs ``"lang,tags,-all"``).
# Sorting the CSV entries makes the field byte-stable across processes
# without affecting jupytext semantics (the filter is order-independent).
_JUPYTEXT_FILTER_FIELDS = ("cell_metadata_filter", "notebook_metadata_filter")


def _normalize_jupytext_metadata_filters(nb: NotebookNode) -> None:
    """Sort the CSV entries in jupytext's metadata-filter fields in-place."""
    jupytext_meta = nb.get("metadata", {}).get("jupytext")
    if not isinstance(jupytext_meta, dict):
        return
    for field in _JUPYTEXT_FILTER_FIELDS:
        value = jupytext_meta.get(field)
        if not isinstance(value, str) or "," not in value:
            continue
        entries = [e.strip() for e in value.split(",") if e.strip()]
        jupytext_meta[field] = ",".join(sorted(entries))


def _strip_lines_to_next_cell(cells: Iterable[Cell]) -> None:
    """Drop jupytext's ``lines_to_next_cell`` hint from every cell in-place.

    ``lines_to_next_cell`` is a layout artifact that jupytext records when the
    actual blank-line count between two cells differs from what its PEP 8
    heuristic expects. That heuristic looks *ahead* into the next cell, so the
    same logical cell receives a different value depending on the identity of
    its physical neighbour in the source ``.py`` file.

    A bilingual deck interleaves DE/EN cells; ``clm slides split`` emits a
    single-language deck. After CLM filters cells by language the two forms
    yield the *same* surviving cell sequence, but their ``lines_to_next_cell``
    metadata diverges because jupytext computed it against different physical
    neighbours upstream (see GitHub issue #133). The value carries no semantic
    meaning for the executed ``.ipynb``/HTML output — it only influences the
    blank-line count jupytext writes back out — so we strip it from the build
    output to make split and bilingual builds byte-equivalent. Author spacing
    intent in *source* files is untouched; this only normalizes build output.
    """
    for cell in cells:
        metadata = cell.get("metadata")
        if isinstance(metadata, dict):
            metadata.pop("lines_to_next_cell", None)


# Regex pattern to match img and video tags with src="img/..." paths
# Captures: prefix (before img/), filename (after img/), suffix (rest of tag)
MEDIA_SRC_PATTERN = re.compile(r'(<(?:img|video)\s+[^>]*src=["\'])img/([^"\']+)(["\'][^>]*>)')

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - notebook-processor - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class CellContext:
    """Context for the currently executing cell.

    This is used to track which cell is being executed so that
    error messages can include accurate cell information even
    when the error occurs before the notebook outputs are populated.
    """

    cell_index: int
    cell_source: str
    cell_type: str = "code"


def classify_execution_failure(error: BaseException) -> tuple[str, str]:
    """Classify one failed execution attempt for telemetry (issue #330).

    Distinguishes the failure classes the xeus-cpp crash triage cares
    about: a cell raising (``cell_execution_error``), the kernel process
    dying (``dead_kernel``), the kernel never becoming ready
    (``startup_timeout``), a cell exceeding its timeout (``cell_timeout``),
    and the kernelspec not being installed at all (``missing_kernel`` —
    permanent for the lifetime of the build, so the retry loop fails fast
    on it, issue #348). Everything else is ``other``.

    Returns:
        ``(failure_type, error_class_name)``.
    """
    from nbclient.exceptions import CellExecutionError, CellTimeoutError, DeadKernelError

    error_class = type(error).__name__
    if isinstance(error, CellExecutionError):
        return "cell_execution_error", error_class
    if isinstance(error, DeadKernelError):
        return "dead_kernel", error_class
    if isinstance(error, CellTimeoutError):
        return "cell_timeout", error_class
    message = str(error)
    # Matched by class name (jupyter_client.kernelspec.NoSuchKernel) and
    # message so wrapped/re-raised forms classify too.
    if error_class == "NoSuchKernel" or "No such kernel" in message:
        return "missing_kernel", error_class
    # jupyter_client raises plain RuntimeErrors for these kernel-level
    # failures, so the message is the only classification signal.
    if "Kernel died" in message:
        return "dead_kernel", error_class
    if "Kernel didn't respond" in message:
        return "startup_timeout", error_class
    if isinstance(error, TimeoutError):
        return "cell_timeout", error_class
    return "other", error_class


def summarize_execution_attempts(
    failed_attempts: list[dict],
    *,
    attempts_made: int,
    succeeded: bool,
) -> dict:
    """Build the telemetry record for one notebook execution (issue #330).

    ``failed_attempts`` holds one record per failed attempt (as produced in
    ``_execute_notebook_with_path``). The record's ``classification``
    separates deterministic crashes (every attempt failed with the same
    failure type at the same cell — retries are wasted on these) from
    flakes (``flaky`` when a retry passed, ``mixed`` when attempts failed
    in different ways).
    """
    last = failed_attempts[-1] if failed_attempts else {}
    if succeeded:
        classification = "flaky"
    else:
        signatures = {(a["failure_type"], a["failing_cell_index"]) for a in failed_attempts}
        classification = "deterministic" if len(signatures) == 1 else "mixed"
    return {
        "schema": 1,
        "attempts": attempts_made,
        "outcome": "passed_after_retry" if succeeded else "failed",
        "classification": classification,
        "failure_type": last.get("failure_type", ""),
        "failing_cell_index": last.get("failing_cell_index"),
        "error_message": last.get("message", ""),
        "attempts_detail": failed_attempts,
    }


def reap_kernel_descendants(
    kernel_pid: int | None,
    descendants: list[psutil.Process],
    log_prefix: str = "",
) -> int:
    """Terminate-and-kill any previously-snapshotted kernel descendants.

    Takes a pre-captured list of descendant ``psutil.Process`` objects
    (snapshot must be taken before ``shutdown_kernel`` so the parent-walk
    still works) and reaps any that are still alive. Delegates the actual
    terminate → wait → kill sequence to
    :func:`clm.infrastructure.workers.process_reaper.terminate_then_kill_procs`,
    which is the shared primitive used by both this path and Fix 5's
    ``clm workers reap`` command.

    Logs WARNING when anything actually had to be reaped — that warning
    is the diagnostic signal the team has been missing when orphaned
    ``python.exe`` processes pile up after notebook jobs.

    Args:
        kernel_pid: PID of the kernel whose tree was snapshotted (for logging).
        descendants: List of descendant Process objects captured before
            shutdown_kernel ran.
        log_prefix: Optional prefix for log lines (e.g., a correlation ID).

    Returns:
        Number of descendants that were actually found alive and reaped.
    """
    live_descendants = [p for p in descendants if p.is_running()]
    if not live_descendants:
        return 0  # Clean shutdown — no orphans.

    prefix = f"{log_prefix}: " if log_prefix else ""
    logger.warning(
        f"{prefix}Kernel (pid={kernel_pid}) shutdown left "
        f"{len(live_descendants)} live descendants; reaping via psutil "
        f"(pids={[p.pid for p in live_descendants]})"
    )

    return terminate_then_kill_procs(live_descendants, log_prefix=log_prefix)


class _ReapingKernelManager(AsyncKernelManager):
    """AsyncKernelManager that reaps kernel grandchildren on shutdown.

    jupyter_client's ``LocalProvisioner.kill`` ultimately calls
    ``TerminateProcess`` on Windows, which only kills the kernel pid —
    any subprocesses the kernel spawned (cells using ``subprocess.Popen``,
    ``multiprocessing``, etc.) survive as orphan ``python.exe`` processes
    that accumulate over the worker's lifetime and wedge WMI / Windows
    Terminal at scale.

    This subclass intercepts ``shutdown_kernel`` to:

    1. Snapshot the kernel's descendants while the kernel is still alive
       (so the parent-walk via psutil still works).
    2. Run the normal graceful shutdown.
    3. Reap any descendants that outlived the kernel.

    The subclass is wired into :class:`TrackingExecutePreprocessor` via
    the ``kernel_manager_class`` traitlet, so nbclient's
    ``create_kernel_manager`` uses it automatically.
    """

    # mypy follows the sync ``KernelManager.shutdown_kernel -> None`` signature
    # from the grandparent class, so override-checking trips on the async
    # return type even though we are actually overriding
    # ``AsyncKernelManager.shutdown_kernel`` (itself async). Silence that.
    async def shutdown_kernel(  # type: ignore[override]
        self, now: bool = False, restart: bool = False
    ) -> None:
        # Snapshot descendants BEFORE super's shutdown. After shutdown the
        # kernel process is gone and psutil.Process(pid).children() cannot
        # walk the tree any more.
        kernel_pid: int | None = None
        descendants: list[psutil.Process] = []
        provisioner = getattr(self, "provisioner", None)
        if provisioner is not None:
            kernel_pid = getattr(provisioner, "pid", None)
        if kernel_pid is not None:
            try:
                descendants = psutil.Process(kernel_pid).children(recursive=True)
            except psutil.NoSuchProcess:
                descendants = []

        try:
            await super().shutdown_kernel(now=now, restart=restart)
        finally:
            # Always run the reap, even if super's shutdown raised. The
            # descendant list was captured while the kernel was alive, so
            # it is still valid regardless of what happened to the kernel.
            if descendants:
                reap_kernel_descendants(kernel_pid, descendants)


class TrackingExecutePreprocessor(ExecutePreprocessor):
    """ExecutePreprocessor that tracks the currently executing cell.

    This subclass updates the NotebookProcessor's _current_cell attribute
    before each cell is executed, enabling accurate error reporting even
    when errors occur before cell outputs are populated.

    It also wires in :class:`_ReapingKernelManager` as the kernel manager
    class so that ``shutdown_kernel`` snapshots and reaps any descendants
    the kernel spawned (see the docstring on ``_ReapingKernelManager``).
    """

    # Override nbclient's default AsyncKernelManager so every kernel created
    # via create_kernel_manager() uses our reaping subclass.
    kernel_manager_class = _ReapingKernelManager

    def __init__(
        self,
        processor: "NotebookProcessor",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.processor = processor
        # Total cells in the notebook currently being processed. Captured on
        # the first preprocess_cell call so the heartbeat store can publish a
        # stable "N/total" denominator without us threading it through the
        # public API.
        self._total_cells: int | None = None

    def preprocess(
        self, nb: NotebookNode, resources: dict | None = None, km=None
    ) -> tuple[NotebookNode, dict]:
        """Capture total cell count before delegating to nbconvert.

        nbconvert iterates ``nb.cells`` itself, so we record the length once
        here for the per-cell heartbeat rather than re-counting in every
        ``preprocess_cell`` invocation.
        """
        try:
            self._total_cells = len(nb.get("cells", []))
        except Exception:
            self._total_cells = None
        return cast(
            "tuple[NotebookNode, dict]",
            super().preprocess(nb, resources=resources, km=km),
        )

    def preprocess_cell(self, cell, resources, cell_index):
        """Execute a cell, tracking it for error reporting.

        Args:
            cell: The notebook cell to execute
            resources: Resources dict passed through preprocessing
            cell_index: Index of the cell in the notebook

        Returns:
            Tuple of (processed cell, resources)
        """
        # Set the current cell context before execution
        self.processor._current_cell = CellContext(
            cell_index=cell_index,
            cell_source=cell.get("source", ""),
            cell_type=cell.get("cell_type", "code"),
        )
        # Publish per-cell heartbeat (best-effort: failures inside the store
        # are logged and swallowed; no impact on cell execution).
        store = self.processor.heartbeat_store
        if store is not None:
            store.begin_cell(
                job_id=self.processor.heartbeat_job_id,
                cell_index=cell_index,
                total_cells=self._total_cells,
            )
        # Per-cell timing instrumentation (issue #143). The build worker
        # runs cells with timeout=None, so a cell whose kernel never returns
        # to idle (e.g. a burst of concurrent HTTP requests or a stalled
        # iopub drain) blocks here forever until the build-level job timeout
        # fires. Logging the start before and the elapsed time after every
        # cell means the build log pinpoints exactly which cell stalled —
        # the last "begin" line with no matching "done" line is the culprit.
        cid = getattr(self.processor, "_current_cid", None) or "?"
        total = self._total_cells if self._total_cells is not None else "?"
        cell_started = time.monotonic()
        logger.debug(
            "%s: cell %s/%s begin (%s)",
            cid,
            cell_index,
            total,
            cell.get("cell_type", "code"),
        )
        try:
            # Execute the cell - on success, clear context; on error, preserve it
            result = super().preprocess_cell(cell, resources, cell_index)
        finally:
            elapsed = time.monotonic() - cell_started
            logger.debug(
                "%s: cell %s/%s done in %.2fs",
                cid,
                cell_index,
                total,
                elapsed,
            )
            # Surface slow cells at INFO so they show up without DEBUG. A
            # cell that runs much longer than the direct-jupyter-execute
            # baseline (~20s for the slowest cell in issue #143) is a strong
            # signal for where a stall begins.
            if elapsed >= _SLOW_CELL_LOG_THRESHOLD_SECONDS:
                logger.info(
                    "%s: slow cell %s/%s took %.1fs (threshold %.0fs) — "
                    "if the build later times out, inspect this cell's I/O "
                    "profile (issue #143)",
                    cid,
                    cell_index,
                    total,
                    elapsed,
                    _SLOW_CELL_LOG_THRESHOLD_SECONDS,
                )
        # Only clear on success - preserve context for error reporting
        self.processor._current_cell = None
        return result

    def process_message(self, msg, cell, cell_index):
        """Intercept iopub messages to capture last stream output excerpt.

        Override of :meth:`nbclient.client.NotebookClient.process_message`.
        We only inspect ``stream`` messages (stdout/stderr); everything else
        passes straight through to the base implementation. The store handles
        ANSI stripping and truncation, and silently no-ops on failure so the
        kernel pipeline is never blocked.
        """
        store = self.processor.heartbeat_store
        if store is not None and msg.get("msg_type") == "stream":
            try:
                text = msg.get("content", {}).get("text", "")
                if text:
                    store.record_output(text)
            except Exception:
                # Defensive: never let heartbeat capture break execution.
                logger.debug("Worker heartbeat stream capture failed", exc_info=True)
        return super().process_message(msg, cell, cell_index)


class CellIdGenerator:
    def __init__(self):
        self.unique_ids: set[str] = set()
        self.id_uniquifier: int = 1

    def set_cell_id(self, cell: Cell, index: int) -> None:
        cell_hash = sha3_224()
        cell_source: str = cell["source"]
        hash_text = cell_source
        while True:
            cell_hash.update(hash_text.encode("utf-8"))
            cell_id = cell_hash.hexdigest()[:16]
            if cell_id in self.unique_ids:
                hash_text = f"{index}:{cell_source}"
                index += 1
            else:
                self.unique_ids.add(cell_id)
                cell.id = cell_id
                break


class DontWarnForMissingAltTags(logging.Filter):
    def filter(self, record):
        return "Alternative text is missing" not in record.getMessage()


class NotebookProcessor:
    def __init__(
        self,
        output_spec: OutputSpec,
        cache: "ExecutedNotebookCacheLike | None" = None,
        heartbeat_store: "WorkerHeartbeatStore | None" = None,
        heartbeat_job_id: int | None = None,
    ):
        self.output_spec = output_spec
        self.id_generator = CellIdGenerator()
        self.cache = cache
        self._warnings: list[ProcessingWarning] = []
        # Track the currently executing cell for accurate error reporting
        self._current_cell: CellContext | None = None
        # Correlation id of the notebook currently being executed, used by
        # the TrackingExecutePreprocessor's per-cell timing logs (issue #143).
        self._current_cid: str | None = None
        # Author/organization for Jinja templates (set from payload in process_notebook)
        self._author: str = "Dr. Matthias Hölzl"
        self._organization: str = ""
        # Optional heartbeat store, supplied by the worker. When set, the
        # TrackingExecutePreprocessor writes one row per cell + a partial
        # update per stdout/stderr stream message. None disables the feature
        # (e.g. unit tests that instantiate the processor directly).
        self.heartbeat_store: WorkerHeartbeatStore | None = heartbeat_store
        self.heartbeat_job_id: int | None = heartbeat_job_id

    def add_warning(
        self,
        category: str,
        message: str,
        file_path: str = "",
        severity: str = "medium",
        details: dict | None = None,
    ) -> None:
        """Add a processing warning to be reported to the user.

        Args:
            category: Category of the warning (e.g., "invalid_tags", "multiple_slide_tags")
            message: Human-readable warning message
            file_path: Path to the file being processed
            severity: Warning severity ("high", "medium", or "low")
            details: Optional dict with additional context
        """
        self._warnings.append(
            ProcessingWarning(
                category=category,
                message=message,
                file_path=file_path,
                severity=severity,  # type: ignore[arg-type]
                details=details or {},
            )
        )

    def get_warnings(self) -> list[ProcessingWarning]:
        """Return all collected warnings."""
        return self._warnings.copy()

    def clear_warnings(self) -> None:
        """Clear all collected warnings."""
        self._warnings.clear()

    async def process_notebook(
        self, payload: NotebookPayload, source_dir: Path | None = None
    ) -> str:
        """Process a notebook and return the result.

        Args:
            payload: Notebook payload with data and metadata
            source_dir: Optional path to source directory where supporting files
                are located (Docker mode with source mount). When set, files are
                read directly from this directory instead of from other_files.

        Returns:
            The processed notebook as a string (HTML, notebook, or code)
        """
        cid = payload.correlation_id
        logger.info(
            f"{cid}:Processing notebook '{payload.input_file_name}' "
            f"({payload.language}, {payload.kind}, {payload.format})"
        )

        # Set author/organization from payload for Jinja template globals
        self._author = payload.author
        self._organization = payload.organization

        # Check if we can reuse a cached executed notebook (Completed HTML).
        # ``payload.skip_evaluation`` short-circuits this path because the
        # topic opted out of evaluation: there is no producer to populate
        # the cache and no consumer that should depend on it.
        if (
            self.output_spec.can_reuse_execution
            and self.cache is not None
            and not payload.fallback_execute
            and not payload.skip_evaluation
        ):
            cached_result = await self._try_reuse_cached_execution(payload)
            if cached_result is not None:
                return cached_result
            # Cache miss - log warning and fall through to normal processing
            # This can happen when Speaker HTML was served from database cache
            # (not executed), so the execution cache was never populated
            logger.warning(
                f"{cid}:Execution cache miss for '{payload.input_file_name}'. "
                f"Falling back to direct execution."
            )

        # Normal processing path
        expanded_nb = await self.load_and_expand_jinja_template(
            payload.data, payload.input_file_name, cid, payload, source_dir
        )
        processed_nb = await self.process_notebook_for_spec(expanded_nb, payload)
        result = await self.create_contents(processed_nb, payload, source_dir=source_dir)
        if result:
            logger.debug(f"{cid}:Processed notebook. Result: {result[:100]}...")
        else:
            logger.error(f"{cid}:Could not process notebook: No contents.")
        return result

    async def _try_reuse_cached_execution(self, payload: NotebookPayload) -> str | None:
        """Try to reuse a cached executed notebook for Completed HTML.

        For Completed HTML, we can reuse the Speaker HTML's executed notebook
        by filtering out the "notes" cells (which are markdown, not code).

        Returns:
            The HTML result if cache hit, None if cache miss.
        """
        cid = payload.correlation_id
        cache_hash = payload.execution_cache_hash()

        logger.debug(f"{cid}:Trying to reuse cached execution for '{payload.input_file_name}'")

        assert self.cache is not None  # Checked by caller
        cached_nb = self.cache.get(
            input_file=payload.input_file,
            content_hash=cache_hash,
            language=payload.language,
            prog_lang=payload.prog_lang,
        )

        if cached_nb is None:
            logger.debug(f"{cid}:Cache miss for '{payload.input_file_name}'")
            return None

        logger.info(f"{cid}:Cache hit - reusing executed notebook for '{payload.input_file_name}'")

        # Translate the cached Speaker notebook into the consuming kind.
        # Completed drops notes/voiceover; Partial additionally blanks and
        # clears outputs for every cell at or after the workshop boundary so
        # no workshop code is ever executed under the Partial kind.
        if isinstance(self.output_spec, PartialOutput):
            filtered_nb = self._filter_cached_notebook_for_partial(cached_nb)
        else:
            filtered_nb = self._filter_notes_cells_from_cached(cached_nb)

        # Export to HTML (no execution needed)
        traitlets_logger = traitlets.log.get_logger()
        if hasattr(traitlets_logger, "addFilter"):
            traitlets_logger.addFilter(DontWarnForMissingAltTags())
        html_exporter = HTMLExporter(template_name="classic")
        (body, _resources) = html_exporter.from_notebook_node(filtered_nb)

        logger.debug(f"{cid}:Successfully reused cached execution for '{payload.input_file_name}'")
        return body

    def _filter_notes_cells_from_cached(self, nb: NotebookNode) -> NotebookNode:
        """Filter out notes and voiceover cells from a cached executed notebook.

        This is used when reusing Speaker's executed notebook for Completed HTML.
        Notes and voiceover cells are markdown cells that should not appear in
        Completed output.
        """
        # Make a deep copy to avoid modifying the cached notebook
        filtered_nb = copy.deepcopy(nb)
        filtered_nb.cells = [
            cell
            for cell in filtered_nb.get("cells", [])
            if not {"notes", "voiceover"}.intersection(get_tags(cell))
        ]
        return filtered_nb

    def _filter_cached_notebook_for_partial(self, nb: NotebookNode) -> NotebookNode:
        """Translate Speaker's cached executed notebook into Partial HTML.

        Pre-workshop: drop ``notes``/``voiceover`` (Completed-style).
        Post-workshop: drop ``alt``/``completed``/``notes``/``voiceover``/``del``;
        blank code source for cells without ``keep``/``start``; blank
        ``answer`` markdown; and clear ``outputs`` for every remaining
        post-workshop code cell so no workshop code is ever presented as
        executed — even ``keep``-tagged cells render as unevaluated.

        This post-processing replaces the previous approach of letting
        Partial execute the notebook with blanked post-workshop sources,
        which raised NameErrors whenever a post-workshop ``keep`` cell
        referenced symbols defined in the blanked non-``keep`` cells.
        """
        filtered_nb = copy.deepcopy(nb)
        cells = filtered_nb.get("cells", [])
        ranges = find_workshop_ranges(cells)

        pre_drop = {"notes", "voiceover"}
        post_drop = {"alt", "completed", "del", "notes", "voiceover"}
        post_retain_code = {"keep", "start"}
        post_blank_markdown = {"answer"}

        new_cells: list[NotebookNode] = []
        for idx, cell in enumerate(cells):
            tags = set(get_tags(cell))
            in_workshop = _is_in_workshop(idx, ranges)

            drop_tags = post_drop if in_workshop else pre_drop
            if drop_tags.intersection(tags):
                continue

            if in_workshop:
                if is_code_cell(cell):
                    if not post_retain_code.intersection(tags):
                        cell["source"] = ""
                    cell["outputs"] = []
                    if "execution_count" in cell:
                        cell["execution_count"] = None
                elif is_markdown_cell(cell):
                    if post_blank_markdown.intersection(tags):
                        cell["source"] = ""

            new_cells.append(cell)

        filtered_nb.cells = new_cells
        return filtered_nb

    async def load_and_expand_jinja_template(
        self,
        notebook_text: str,
        notebook_file: str,
        cid,
        payload: NotebookPayload | None = None,
        source_dir: Path | None = None,
    ) -> str:
        logger.debug(f"{cid}:Loading and expanding Jinja template")
        jinja_env = self._create_jinja_environment(cid, payload, source_dir)
        nb_template = jinja_env.from_string(
            notebook_text,
            globals=self._create_jinja_globals(
                self.output_spec,
                author=self._author,
                organization=self._organization,
            ),
        )
        logger.debug(f"{cid}:Jinja template created for {notebook_file}")
        expanded_nb = await nb_template.render_async()
        logger.debug(f"{cid}:Jinja template expanded for {notebook_file}")
        return cast(str, expanded_nb)

    def _create_jinja_environment(
        self,
        cid,
        payload: NotebookPayload | None = None,
        source_dir: Path | None = None,
    ):
        templates_path = f"{JINJA_TEMPLATES_PREFIX}_{self.output_spec.prog_lang}"
        logger.debug(f"{cid}:Creating Jinja environment with templates from {templates_path}")
        try:
            jinja_env = Environment(
                loader=self._create_jinja_loader(cid, templates_path, payload, source_dir),
                autoescape=False,
                undefined=StrictUndefined,
                line_statement_prefix=jinja_prefix_for(self.output_spec.prog_lang),
                keep_trailing_newline=True,
                enable_async=True,
            )
            logger.debug("Jinja environment created")
            return jinja_env
        except Exception as e:
            logger.error(
                f"Failed to create Jinja environment for "
                f"'{self.output_spec.prog_lang}' with template dir "
                f"'{templates_path}': {e}"
            )
            raise

    def _create_jinja_loader(
        self,
        cid,
        templates_path: str,
        payload: NotebookPayload | None,
        source_dir: Path | None,
    ):
        """Build the Jinja loader used to resolve ``{% include %}`` targets.

        The bundled ``PackageLoader`` (the per-language ``templates_<lang>``
        directory inside the ``clm`` package, source of ``macros.j2`` etc.)
        is searched *first*. So that a notebook can ``{% include %}`` a file
        that sits next to it in its topic (e.g. a ``add.h`` header shown in a
        slide), the notebook's sibling files are layered *behind* it as a
        fallback:

        * In Docker source-mount mode the topic directory is on disk, so a
          ``FileSystemLoader`` pointed at ``source_dir`` exposes the siblings.
        * Otherwise the siblings travel in ``payload.other_files`` (base64),
          which CLM already ships for kernel execution; a ``DictLoader`` built
          from their decoded text makes them includable. Entries that are not
          valid UTF-8 (e.g. binary assets) are skipped — they cannot be Jinja
          templates anyway.

        ``ChoiceLoader`` returns the *first* match, so keeping the
        ``PackageLoader`` ahead of the siblings means a sibling can never
        shadow a bundled macro file of the same name; it only supplies names
        the package does not already provide.

        Ordering trade-off (deliberate): if we ever want a course to be able
        to **override** a bundled template — e.g. ship its own ``macros.j2``
        next to a slide and have it win over the packaged one — move the
        sibling loaders *ahead* of ``package_loader`` in this list (siblings
        first, ``package_loader`` last). That was rejected here because it
        lets any topic silently redefine the title macro and diverge from the
        rest of the course; package-first is the safer default. Whoever flips
        the order must update the "bundled macro not shadowed" test in
        ``tests/workers/notebook/test_jinja_include_siblings.py`` and the
        "Jinja ``{% include %}`` in slide source" section in
        ``src/clm/cli/info_topics/commands.md``.
        """
        package_loader = PackageLoader("clm.workers.notebook", templates_path)
        loaders: list = [package_loader]

        if source_dir is not None:
            loaders.append(FileSystemLoader(str(source_dir)))

        if payload is not None and payload.other_files:
            sibling_templates: dict[str, str] = {}
            for name, encoded in payload.other_files.items():
                try:
                    sibling_templates[name] = b64decode(encoded).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    logger.debug(
                        f"{cid}:Skipping non-text sibling '{name}' for Jinja include resolution"
                    )
            if sibling_templates:
                loaders.append(DictLoader(sibling_templates))

        if len(loaders) == 1:
            return package_loader

        return ChoiceLoader(loaders)

    @staticmethod
    def _create_jinja_globals(
        output_spec,
        author: str = "Dr. Matthias Hölzl",
        organization: str = "",
    ):
        return {
            "is_notebook": output_spec.format == "notebook",
            "is_html": output_spec.format == "html",
            "lang": output_spec.language,
            "author": author,
            "organization": organization,
        }

    async def process_notebook_for_spec(
        self, expanded_nb: str, payload: NotebookPayload
    ) -> NotebookNode:
        jupytext_format = self._jupytext_read_format(payload)
        logger.debug(
            f"{payload.correlation_id}:Processing notebook for in format "
            f"'{self.output_spec.format}' with Jupytext format "
            f"'{jupytext_format}'"
        )
        loop = asyncio.get_running_loop()
        nb = await loop.run_in_executor(None, jupytext.reads, expanded_nb, jupytext_format)
        _normalize_jupytext_metadata_filters(nb)
        processed_nb = await self._process_notebook_node(nb, payload)
        return processed_nb

    @staticmethod
    def _jupytext_read_format(payload: NotebookPayload) -> str | dict[str, str]:
        """Determine the jupytext format for reading the input file.

        For .md files, we always use "md" so that jupytext auto-detects the
        markdown variant (standard markdown or MyST) from the file content.
        The programming language and kernel are set separately after reading.

        For all other files, we use the format derived from the programming
        language (e.g., "py:percent" for Python, "cpp:percent" for C++).
        """
        if payload.input_file_name.endswith(".md"):
            return "md"
        return jupytext_format_for(payload.prog_lang)

    async def _process_notebook_node(
        self, nb: NotebookNode, payload: NotebookPayload
    ) -> NotebookNode:
        source_cells = nb.get("cells", [])
        self.output_spec.annotate_cells(source_cells)
        new_cells = [
            await self._process_cell(cell, index, payload)
            for index, cell in enumerate(source_cells)
            if self.output_spec.is_cell_included(cell)
        ]
        # Strip slide_id and for_slide from cell metadata — these are
        # internal CLM metadata and must never appear in output.
        # Also strip the synthetic _post_workshop tag attached by
        # PartialOutput.annotate_cells.
        for cell in new_cells:
            cell["metadata"].pop("slide_id", None)
            cell["metadata"].pop("for_slide", None)
            tags = cell["metadata"].get("tags")
            if tags and POST_WORKSHOP_TAG in tags:
                cell["metadata"]["tags"] = [t for t in tags if t != POST_WORKSHOP_TAG]
        # Drop jupytext's ``lines_to_next_cell`` layout artifact so that split
        # and bilingual builds produce byte-equivalent output (issue #133).
        _strip_lines_to_next_cell(new_cells)
        nb.cells = new_cells
        nb.metadata["language_info"] = language_info(payload.prog_lang)
        nb.metadata["kernelspec"] = kernelspec_for(payload.prog_lang)
        _, normalized_nb = normalize(nb)
        return cast(NotebookNode, normalized_nb)

    async def _process_cell(self, cell: Cell, index: int, payload: NotebookPayload) -> Cell:
        cid = payload.correlation_id
        self._generate_cell_metadata(cell, index, payload.input_file)
        await asyncio.sleep(0)
        if LOG_CELL_PROCESSING:
            logger.debug(f"{cid}:Processing cell {cell} of {payload.input_file_name}")
        if is_code_cell(cell):
            return self._process_code_cell(cell, index, payload.input_file)
        elif is_markdown_cell(cell):
            return self._process_markdown_cell(
                cell, index, payload.input_file, payload.img_path_prefix, payload
            )
        else:
            logger.warning(f"{cid}:Keeping unknown cell type {get_cell_type(cell)!r}.")
            return cell

    def _generate_cell_metadata(self, cell: Cell, index: int, file_path: str = "") -> None:
        self.id_generator.set_cell_id(cell, index)
        self._process_slide_tag(cell, index, file_path)

    def _process_slide_tag(self, cell: Cell, index: int = 0, file_path: str = "") -> None:
        """Process slide tag for a cell and collect warnings for conflicts."""
        tags = get_tags(cell)

        # Check for conflicting slide tags
        conflicting_tags = get_conflicting_slide_tags(tags)
        if conflicting_tags:
            self.add_warning(
                category="multiple_slide_tags",
                message=f"Cell #{index} has multiple slide tags: {conflicting_tags}. One will be chosen arbitrarily.",
                file_path=file_path,
                severity="medium",
                details={"cell_index": index, "conflicting_tags": conflicting_tags},
            )

        slide_tag = get_slide_tag(cell)
        if slide_tag:
            cell["metadata"]["slideshow"] = {"slide_type": slide_tag}

    def _process_code_cell(self, cell: Cell, index: int = 0, file_path: str = "") -> Cell:
        if not self.output_spec.is_cell_contents_included(cell):
            cell["source"] = ""
            cell["outputs"] = []

        # Check for invalid tags and collect warnings
        tags = get_tags(cell)
        invalid_tags = get_invalid_code_tags(tags)
        for tag in invalid_tags:
            self.add_warning(
                category="invalid_tag",
                message=f"Unknown tag '{tag}' for code cell #{index}",
                file_path=file_path,
                severity="low",
                details={"cell_index": index, "tag": tag, "cell_type": "code"},
            )

        return cell

    def _process_markdown_cell(
        self,
        cell: Cell,
        index: int = 0,
        file_path: str = "",
        img_path_prefix: str = "img/",
        payload: NotebookPayload | None = None,
    ) -> Cell:
        tags = get_tags(cell)

        # Check for invalid tags and collect warnings
        invalid_tags = get_invalid_markdown_tags(tags)
        for tag in invalid_tags:
            self.add_warning(
                category="invalid_tag",
                message=f"Unknown tag '{tag}' for markdown cell #{index}",
                file_path=file_path,
                severity="low",
                details={"cell_index": index, "tag": tag, "cell_type": "markdown"},
            )

        self._process_markdown_cell_contents(cell, img_path_prefix, payload)
        return cell

    def _process_markdown_cell_contents(
        self,
        cell: Cell,
        img_path_prefix: str = "img/",
        payload: NotebookPayload | None = None,
    ):
        tags = get_tags(cell)
        if "notes" in tags:
            contents = cell["source"]
            cell["source"] = (
                "<div style='background: yellow; color: black;'>\n" + contents + "\n</div>"
            )
        elif "voiceover" in tags:
            contents = cell["source"]
            cell["source"] = (
                "<div style='background: #FFEEBA; color: black;'>\n" + contents + "\n</div>"
            )
        if is_answer_cell(cell):
            answer_text = "Answer" if self.output_spec.language == "en" else "Antwort"
            prefix = f"*{answer_text}:* "
            if self.output_spec.is_cell_contents_included(cell):
                cell["source"] = prefix + cell["source"]
            else:
                cell["source"] = prefix

        # Rewrite .png -> .svg for images that have SVG equivalents
        if payload and payload.svg_available_stems:
            cell["source"] = self._rewrite_png_to_svg(
                cell["source"], set(payload.svg_available_stems)
            )

        # Rewrite image paths from img/filename to the shared img/ folder location
        cell["source"] = self._rewrite_image_paths(cell["source"], img_path_prefix)

        # Rewrite cross-references (Issue #17). The href map was resolved at
        # payload-construction time; here the worker only does a mechanical
        # string substitution and needs no knowledge of other notebooks.
        if payload and payload.cross_references:
            from clm.core.cross_references import rewrite_cross_references

            cell["source"] = rewrite_cross_references(cell["source"], payload.cross_references)

        # Inject data URLs for images (if enabled and cell doesn't opt out)
        if payload and payload.inline_images and "nodataurl" not in tags:
            cell["source"] = self._inject_data_urls(cell["source"], payload)

    @staticmethod
    def _rewrite_image_paths(content: str, img_path_prefix: str) -> str:
        """Rewrite image/video paths from img/filename to use the shared img/ folder.

        Transforms paths like:
            <img src="img/diagram.png">
            <video src="img/demo.mp4">
        to:
            <img src="../../../../img/diagram.png">
            <video src="../../../../img/demo.mp4">

        where the prefix depends on how deep the output file is relative to the
        course directory.

        Args:
            content: Markdown cell content potentially containing img/video tags
            img_path_prefix: Relative path prefix to the shared img/ folder

        Returns:
            Content with rewritten image/video paths
        """
        # If img_path_prefix is already "img/", no rewriting needed
        if img_path_prefix == "img/":
            return content

        # Replace img/filename with {img_path_prefix}filename
        def replace_media_src(match):
            prefix = match.group(1)  # e.g., '<img src="' or '<video src="'
            filename = match.group(2)  # e.g., 'diagram.png' or 'demo.mp4'
            suffix = match.group(3)  # e.g., '">'
            return f"{prefix}{img_path_prefix}{filename}{suffix}"

        return MEDIA_SRC_PATTERN.sub(replace_media_src, content)

    @staticmethod
    def _rewrite_png_to_svg(content: str, svg_stems: set[str]) -> str:
        """Rewrite .png references to .svg for images that have SVG equivalents.

        Only rewrites image URLs whose stem (filename without extension) is in
        the svg_stems set. This ensures raw .png files that are not generated
        from DrawIO/PlantUML sources are left unchanged.

        Args:
            content: Markdown cell content
            svg_stems: Set of image stems that have SVG versions available

        Returns:
            Content with .png -> .svg rewrites where applicable
        """

        def replace_if_svg(match):
            prefix = match.group(1)
            filename = match.group(2)  # e.g., 'diagram.png'
            suffix = match.group(3)
            stem = Path(filename).stem
            if stem in svg_stems and filename.endswith(".png"):
                filename = stem + ".svg"
            return f"{prefix}{filename}{suffix}"

        return MEDIA_SRC_PATTERN.sub(replace_if_svg, content)

    # Regex to match <img> tags with src attribute (for data URL injection)
    _IMG_SRC_PATTERN = re.compile(r'<img\s+[^>]*src="(?P<image_url>[^"]+)"')

    # MIME type mapping for image inlining
    _EXTENSION_TO_MIME_TYPE = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
    }

    def _inject_data_urls(self, content: str, payload: NotebookPayload) -> str:
        """Replace image src attributes with base64 data URLs.

        Reads images from the filesystem (source topic directory) with fallback
        to the other_files payload data. Based on Stefan Behnel's implementation.

        Args:
            content: Markdown cell content with <img> tags
            payload: Notebook payload with source directory and other_files

        Returns:
            Content with images embedded as data URLs
        """
        import base64

        source_dir = Path(payload.source_topic_dir) if payload.source_topic_dir else None

        def replace_with_data_url(match: re.Match) -> str:
            match_tag: str = match.group()
            image_url: str = match.group("image_url")

            # Skip data URLs and HTTP(S) URLs
            if image_url.startswith(("data:", "http:", "https:")):
                return match_tag

            # Try reading from filesystem first
            image_data: bytes | None = None
            if source_dir:
                image_path = source_dir / image_url
                if image_path.is_file():
                    try:
                        image_data = image_path.read_bytes()
                    except OSError:
                        pass

            # Fall back to other_files payload
            if image_data is None and image_url in payload.other_files:
                raw = payload.other_files[image_url]
                if isinstance(raw, bytes):
                    image_data = raw
                else:
                    image_data = b64decode(raw)

            if image_data is None:
                return match_tag  # Image not available, keep original

            extension = Path(image_url).suffix.lower()
            mime_type = self._EXTENSION_TO_MIME_TYPE.get(extension)
            if mime_type is None:
                return match_tag  # Unknown format, keep original

            encoded = base64.b64encode(image_data).decode()
            data_url = f"data:{mime_type};base64,{encoded}"
            result: str = match_tag.replace(image_url, data_url)
            return result

        return self._IMG_SRC_PATTERN.sub(replace_with_data_url, content)

    async def create_contents(
        self,
        processed_nb: NotebookNode,
        payload: NotebookPayload,
        source_dir: Path | None = None,
    ) -> str:
        try:
            if self.output_spec.format == "html":
                result = await self._create_using_nbconvert(
                    processed_nb, payload, source_dir=source_dir
                )
            elif self.output_spec.format == "code" and payload.prog_lang == "cpp":
                result = self._create_cpp_code_export(processed_nb)
            else:
                result = await self._create_using_jupytext(processed_nb)
            return result
        except RuntimeError as e:
            logging.error(
                f"Failed to convert notebook '{payload.input_file_name}' to HTML: {e}",
            )
            logging.debug(f"Error traceback for '{payload.input_file_name}'", exc_info=True)
            raise

    async def _cleanup_kernel_resources(self, ep: ExecutePreprocessor, cid: str) -> None:
        """Cleanup kernel resources to prevent ZMQ connection leaks.

        This method ensures proper cleanup of:
        - Kernel client channels (ZMQ sockets)
        - Kernel process (via shutdown_kernel)
        - ZMQ context (via cleanup_resources)

        This prevents "Connection reset by peer [10054]" errors on Windows
        that occur when ZMQ sockets are left in an invalid state after
        kernel crashes or connection resets.

        Note: nbclient's ``setup_kernel`` context manager already calls
        ``shutdown_kernel`` and clears ``km``/``kc`` in its finally block
        before ``preprocess()`` returns, so by the time this method runs,
        ``ep.km`` and ``ep.kc`` are usually already ``None``. The live
        kernel-descendant reap (grandchildren that outlive the kernel)
        happens inside :class:`_ReapingKernelManager.shutdown_kernel` where
        the kernel process tree is still walkable. This method is retained
        as a defence-in-depth safety net for the narrow window where
        setup_kernel does not run its finally (e.g., a crash during
        ``start_new_kernel_client``).

        Args:
            ep: The ExecutePreprocessor instance to clean up
            cid: Correlation ID for logging
        """
        try:
            loop = asyncio.get_running_loop()

            # Stop kernel client channels first (ZMQ sockets)
            if hasattr(ep, "kc") and ep.kc is not None:
                try:
                    await loop.run_in_executor(None, ep.kc.stop_channels)
                    logger.debug(f"{cid}: Stopped kernel client channels")
                except Exception as e:
                    logger.debug(f"{cid}: Error stopping channels: {e}")

            # Shutdown kernel and cleanup ZMQ resources
            if hasattr(ep, "km") and ep.km is not None:
                km = ep.km  # Capture for type narrowing
                try:
                    if km.has_kernel:
                        await loop.run_in_executor(None, lambda: km.shutdown_kernel(now=True))
                        logger.debug(f"{cid}: Shutdown kernel")
                except Exception as e:
                    logger.debug(f"{cid}: Error shutting down kernel: {e}")

                # Cleanup ZMQ resources - this destroys the context
                try:
                    await loop.run_in_executor(None, km.cleanup_resources)
                    logger.debug(f"{cid}: Cleaned up kernel resources")
                except Exception as e:
                    logger.debug(f"{cid}: Error cleaning up resources: {e}")

        except Exception as e:
            logger.warning(f"{cid}: Unexpected error during kernel cleanup: {e}")

    async def _execute_notebook_with_path(
        self,
        cid: str,
        path: Path,
        processed_nb: NotebookNode,
        payload: NotebookPayload,
        loop: asyncio.AbstractEventLoop,
        source_dir: Path | None,
    ) -> None:
        """Execute notebook with supporting files at the given path.

        This handles the retry loop for notebook execution with kernel cleanup.

        Args:
            cid: Correlation ID for logging
            path: Directory containing supporting files (temp dir or source mount)
            processed_nb: The processed notebook to execute
            payload: Notebook payload
            loop: Event loop for running executor
            source_dir: Source directory if using source mount (for logging)
        """
        last_error: Exception | None = None
        attempts_made = 0
        # One record per FAILED attempt — the telemetry that distinguishes
        # deterministic crashes from transient flakes (issue #330).
        failed_attempts: list[dict] = []
        for attempt in range(1, NUM_RETRIES_FOR_HTML + 1):
            # Create FRESH TrackingExecutePreprocessor for each attempt
            # This ensures no stale ZMQ state from previous failures
            # TrackingExecutePreprocessor updates _current_cell for error reporting
            # Expose the correlation id to the preprocessor's per-cell
            # timing logs (issue #143 instrumentation).
            self._current_cid = cid
            attempts_made = attempt
            # Clear stale cell context from a previous attempt so a failure
            # before any cell runs (e.g. a startup timeout) is not blamed on
            # the previous attempt's failing cell.
            self._current_cell = None
            cell_timeout = _effective_cell_timeout(payload)
            if cell_timeout is not None:
                logger.info(
                    "%s: per-cell execution timeout active: %ss%s",
                    cid,
                    cell_timeout,
                    ""
                    if CELL_EXECUTION_TIMEOUT is not None
                    else " (http-replay default; set CLM_CELL_TIMEOUT_SECONDS to override)",
                )
            ep = TrackingExecutePreprocessor(
                self,
                timeout=cell_timeout,
                startup_timeout=300,
                allow_errors=payload.skip_errors,
            )
            try:

                def run_preprocess(
                    ep: TrackingExecutePreprocessor = ep,
                ) -> tuple[NotebookNode, dict]:
                    return ep.preprocess(
                        processed_nb,
                        resources={"metadata": {"path": path}},
                    )

                await loop.run_in_executor(None, run_preprocess)
                last_error = None
                break  # Success - exit retry loop
            except Exception as e:
                # Catch all execution errors including:
                # - RuntimeError (kernel died)
                # - CellExecutionError (cell failed to execute)
                # - DeadKernelError (kernel crashed)
                # - Other nbclient exceptions
                last_error = e
                error_type = type(e).__name__
                failure_type, error_class = classify_execution_failure(e)
                failed_attempts.append(
                    {
                        "attempt": attempt,
                        "failure_type": failure_type,
                        "error_class": error_class,
                        "failing_cell_index": (
                            self._current_cell.cell_index
                            if self._current_cell is not None
                            else None
                        ),
                        "message": str(e)[:500],
                    }
                )
                if not logger.isEnabledFor(logging.DEBUG):
                    logger.info(
                        f"{cid}: Execution failed ({error_type}, attempt {attempt}/{NUM_RETRIES_FOR_HTML})"
                    )
                logger.debug(f"{cid}: Execution failed ({error_type}, attempt {attempt}): {e}")
            finally:
                # ALWAYS cleanup kernel resources to prevent ZMQ leaks
                await self._cleanup_kernel_resources(ep, cid)

            # A missing kernelspec is a permanent condition for the lifetime
            # of the build — every retry would fail identically while paying
            # the kernel-startup and backoff cost each time (issue #348).
            if failed_attempts and failed_attempts[-1]["failure_type"] == "missing_kernel":
                logger.error(
                    f"{cid}: kernel is not installed — failing fast without retries: {last_error}"
                )
                break

            # Exponential backoff before next retry
            if attempt < NUM_RETRIES_FOR_HTML:
                await asyncio.sleep(1.0 * attempt)

        if last_error is not None:
            telemetry = summarize_execution_attempts(
                failed_attempts, attempts_made=attempts_made, succeeded=False
            )
            if payload.skip_errors:
                # The topic opted into error-tolerant execution. Kernel-level
                # failures (dead kernel, startup timeout, etc.) still raise;
                # cell-level exceptions were absorbed via allow_errors=True
                # and therefore would not reach this block.
                from nbclient.exceptions import CellExecutionError

                if isinstance(last_error, CellExecutionError):
                    logger.info(
                        f"{cid}: Suppressing CellExecutionError under skip_errors "
                        f"for '{payload.input_file_name}'"
                    )
                    telemetry["outcome"] = "suppressed_failure"
                    self._add_execution_telemetry_warning(payload, telemetry)
                    self._current_cell = None
                    return

            # Enhance the error message with more context
            # _current_cell may contain context from the failed cell
            enhanced_error = self._enhance_notebook_error(last_error, processed_nb, payload)
            # Attach the per-attempt telemetry so the worker's structured
            # error JSON carries it to the host (issue #330); worker_base
            # copies ``execution_telemetry`` into ``error_info``.
            enhanced_error.execution_telemetry = telemetry  # type: ignore[attr-defined]
            # Clear cell context after using it for error enhancement
            self._current_cell = None
            raise enhanced_error from last_error

        if failed_attempts:
            # Passed, but only after at least one failed attempt — the
            # transient-flake class the build summary surfaces (issue #330).
            self._add_execution_telemetry_warning(
                payload,
                summarize_execution_attempts(
                    failed_attempts, attempts_made=attempts_made, succeeded=True
                ),
            )

        if payload.skip_errors:
            self._clear_error_outputs(processed_nb, payload)

    def _add_execution_telemetry_warning(self, payload: NotebookPayload, telemetry: dict) -> None:
        """Attach an execution-telemetry record to the job result.

        Rides the existing ``ProcessingWarning`` channel because that is the
        only structured worker→host side channel that survives the job-table
        round trip for *completed* jobs. The host (``SqliteBackend``)
        intercepts the ``execution_telemetry`` category: it persists the
        record to the telemetry database and reports flakes to the build
        summary — the record is NOT shown or stored as a regular warning.
        """
        outcome = telemetry.get("outcome", "")
        if outcome == "passed_after_retry":
            message = (
                f"'{payload.input_file_name}' passed only after "
                f"{telemetry.get('attempts')} attempts (kernel flake)"
            )
        else:
            message = (
                f"'{payload.input_file_name}' execution telemetry: {outcome} "
                f"after {telemetry.get('attempts')} attempts"
            )
        self.add_warning(
            category="execution_telemetry",
            message=message,
            file_path=payload.input_file,
            severity="low",
            details=telemetry,
        )

    def _clear_error_outputs(self, processed_nb: NotebookNode, payload: NotebookPayload) -> None:
        """Clear outputs of cells that raised during skip-errors execution.

        When ``skip_errors`` is enabled, ``allow_errors=True`` lets nbclient
        execute every cell even when earlier cells raised. Downstream cells
        typically fail too (``NameError`` on variables that were never
        assigned), which would fill the HTML with tracebacks. We strip those
        tracebacks so the rendered slides stay readable and record a warning
        so the author knows which cells were affected.
        """
        affected: list[int] = []
        cells = processed_nb.get("cells", [])
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            outputs = cell.get("outputs") or []
            if not any(out.get("output_type") == "error" for out in outputs):
                continue
            cell["outputs"] = []
            cell["execution_count"] = None
            affected.append(idx)

        if not affected:
            return

        logger.info(
            f"{payload.correlation_id}: skip_errors cleared outputs of cells "
            f"{affected} in '{payload.input_file_name}'"
        )
        self.add_warning(
            category="skip_errors_cell_failed",
            message=(
                f"skip_errors suppressed exceptions in {len(affected)} cell(s) "
                f"of '{payload.input_file_name}'; their outputs were cleared."
            ),
            file_path=payload.input_file,
            severity="low",
            details={
                "cell_indices": affected,
                "format": payload.format,
                "kind": payload.kind,
                "language": payload.language,
            },
        )

    def _resolve_mitmproxy_tag(
        self, payload: NotebookPayload, source_dir: Path | None
    ) -> str | None:
        """Resolve the ``X-CLM-Cassette`` routing tag for the replay proxy.

        The tag is the absolute canonical cassette path this notebook's
        traffic belongs to. ``payload.http_replay_cassette_name`` already
        carries the split-deck base-cassette fallback for ``replay`` and the
        strict language-specific name for record modes (issue #159); the
        canonical path comes from the shared ``resolve_paths`` computation.

        **Host namespace (issue #165 P4):** the mitmproxy proxy and the
        ``merge_mitmproxy_cassette_staging`` host step run on the **host**,
        even when the kernel runs in a container. The tag must therefore name
        a **host** path so the proxy writes ``<tag>.staging-mitm-<build_id>``
        beside the real canonical cassette the host-side merge folds it into.
        We resolve against ``payload.source_topic_dir`` (the host topic dir in
        both Direct and Docker modes — Docker workers derive their container
        ``source_dir`` *from* it) and fall back to the container ``source_dir``
        only if no host path is available. In Direct mode the two are
        identical, so this is a no-op there. Returns ``None`` when the topic
        did not opt into a replay-capable mode or no cassette / target dir
        resolves.

        **Invariant:** an http-replay notebook must keep its cassette beside the
        notebook at the topic root (the ``_cassettes/`` dir under
        ``source_topic_dir``). ``cassette_name`` is resolved relative to the
        notebook's own parent while the tag here resolves against
        ``source_topic_dir``; for a notebook nested in a sub-directory of the
        topic the two diverge, so the proxy would write staging to a dir the
        host-side merge (``Course.merge_mitmproxy_cassette_staging``) does not
        scan and a record-mode recording would be misplaced (replay is
        unaffected — it reads the committed canonical). Converging nested
        layouts onto per-notebook cassette dirs is a separate follow-up.
        """
        mode = payload.http_replay_mode
        if not mode or mode == "disabled" or mode not in _HTTP_REPLAY_VALID_MODES:
            return None
        cassette_name = payload.http_replay_cassette_name
        if not cassette_name:
            return None
        if payload.source_topic_dir:
            target_dir: Path = Path(payload.source_topic_dir)
        elif source_dir is not None:
            target_dir = source_dir
        else:
            return None
        from .http_replay_cassette import resolve_paths

        return str(resolve_paths(target_dir, cassette_name).canonical)

    def _maybe_inject_http_replay(
        self,
        processed_nb: NotebookNode,
        payload: NotebookPayload,
        source_dir: Path | None = None,
    ) -> bool:
        """Inject the http-replay tag bootstrap cell when the topic opted in.

        The cassette-routing *tag* bootstrap patches httpx/requests/aiohttp to
        tag each request with its destination cassette so the shared replay
        proxy demuxes correctly; the kernel's httpcore is never patched (the
        structural fix for the issue #143 connection-pool deadlock). Returns
        ``True`` when a cell was injected so the caller runs the strip pass.
        """
        tag = self._resolve_mitmproxy_tag(payload, source_dir)
        if tag is None:
            return False
        # Forensic socket trace (issue #165 P5): the kernel emits its
        # ground-truth ``socket`` stream so the analyzer can confirm every
        # connect goes to the proxy (none escapes). Prefer the payload field;
        # fall back to the host-pinned env the Direct worker inherits, so the
        # socket stream is reliably written whenever CLM_HTTP_REPLAY_TRACE=1
        # even if the field was not threaded.
        trace_dir = (
            getattr(payload, "http_replay_trace_dir", "")
            or os.environ.get("CLM_HTTP_REPLAY_TRACE_INVOCATION_DIR", "")
            or ""
        )
        _inject_http_replay_tag_bootstrap(processed_nb, tag, trace_dir=trace_dir)
        logger.debug(
            f"{payload.correlation_id}: Injected http-replay tag bootstrap "
            f"(cassette='{tag}') for '{payload.input_file_name}'"
        )
        return True

    async def _create_using_nbconvert(
        self, processed_nb, payload: NotebookPayload, source_dir: Path | None = None
    ) -> str:
        cid = payload.correlation_id
        traitlets_logger = traitlets.log.get_logger()
        if hasattr(traitlets_logger, "addFilter"):
            traitlets_logger.addFilter(DontWarnForMissingAltTags())
        # ``payload.skip_evaluation`` is the per-topic ``evaluate="no"`` opt-out.
        # When set, we render HTML directly from the processed source (cells with
        # empty outputs) and never spawn a kernel or write to the executed-
        # notebook cache, regardless of which kind is being produced.
        if self.output_spec.evaluate_for_html and not payload.skip_evaluation:
            if any(is_code_cell(cell) for cell in processed_nb.get("cells", [])):
                logger.debug(f"Evaluating and writing notebook '{payload.input_file_name}'")
                # HTTP replay (issue #165): inject the cassette-routing tag
                # bootstrap when the topic opted in. Recording and merging
                # happen entirely outside the worker — the shared replay
                # proxy writes per-cassette staging files on the host and
                # the host build merges them after the proxy stops
                # (Course.merge_mitmproxy_cassette_staging).
                replay_injected = self._maybe_inject_http_replay(processed_nb, payload, source_dir)
                try:
                    # To silence warnings about frozen modules...
                    os.environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore",
                            "Proactor event loop does not implement add_reader",
                        )
                        ExecutePreprocessor.log_level = logging.DEBUG  # type: ignore[attr-defined]
                        loop = asyncio.get_running_loop()

                        # Determine execution path: use source_dir if available (Docker mode
                        # with source mount), otherwise create temp directory for other_files
                        if source_dir is not None:
                            # Docker mode with source mount: files already available
                            path = source_dir
                            logger.debug(f"{cid}:Using source mount for execution: {source_dir}")
                            await self._execute_notebook_with_path(
                                cid, path, processed_nb, payload, loop, source_dir
                            )
                        else:
                            # Standard mode: write other_files to temp directory.
                            # The kernel cwd is this temp dir but the http-replay
                            # bootstrap writes its cassette to an absolute path
                            # under the source tree, so the cassette survives
                            # destruction of the temp dir.
                            with TemporaryDirectory() as temp_dir:
                                path = Path(temp_dir)
                                await self.write_other_files(cid, path, payload)
                                await self._execute_notebook_with_path(
                                    cid, path, processed_nb, payload, loop, None
                                )
                except Exception as e:
                    file_name = payload.input_file_name
                    logger.error(
                        f"Notebook Processor (nbconvert): "
                        f"Error while processing notebook '{file_name}': {e}",
                    )
                    logger.debug(f"{cid}:Error traceback for {file_name}:", exc_info=e)
                    raise
                finally:
                    # Always strip injected cells so they never reach HTML,
                    # the execution cache, or any downstream consumer — even
                    # when execution raised above. Cassette persistence is
                    # host-side (the build writes completion markers and
                    # merges proxy staging files after the proxy stops), so
                    # there is nothing to persist here.
                    if replay_injected:
                        _strip_injected_cells(processed_nb)

                # Cache the executed notebook for later reuse by Completed HTML
                if self.output_spec.should_cache_execution and self.cache is not None:
                    self._cache_executed_notebook(processed_nb, payload)
            else:
                logger.debug(f"Notebook {payload.input_file_name} contains no code cells.")
                # Still cache the notebook for Completed HTML even without code cells
                # The "executed" notebook is just the processed notebook in this case
                if self.output_spec.should_cache_execution and self.cache is not None:
                    self._cache_executed_notebook(processed_nb, payload)
        html_exporter = HTMLExporter(template_name="classic")
        (body, _resources) = html_exporter.from_notebook_node(processed_nb)
        return body

    def _cache_executed_notebook(self, executed_nb: NotebookNode, payload: NotebookPayload) -> None:
        """Cache the executed notebook for reuse by Completed HTML.

        Speaker HTML caches its executed notebook so that Completed HTML can
        reuse it by simply filtering out the "notes" cells.
        """
        cid = payload.correlation_id
        cache_hash = payload.execution_cache_hash()

        logger.info(
            f"{cid}:Caching executed notebook for '{payload.input_file_name}' "
            f"(language={payload.language}, prog_lang={payload.prog_lang})"
        )

        assert self.cache is not None  # Checked by caller
        self.cache.store(
            input_file=payload.input_file,
            content_hash=cache_hash,
            language=payload.language,
            prog_lang=payload.prog_lang,
            executed_notebook=executed_nb,
        )

        logger.debug(f"{cid}:Successfully cached executed notebook")

    def _enhance_notebook_error(
        self,
        error: Exception,
        notebook: NotebookNode,
        payload: NotebookPayload,
    ) -> RuntimeError:
        """Enhance a notebook execution error with more context.

        Extracts the root cause, cell information, and code snippet from the
        error to create a more informative error message. For C++ notebooks,
        also tries to extract compiler error details.

        Args:
            error: The original exception
            notebook: The notebook being processed
            payload: The notebook payload

        Returns:
            A new RuntimeError with enhanced context
        """
        import traceback as tb_module

        # Get the original traceback string
        tb_str = "".join(tb_module.format_exception(type(error), error, error.__traceback__))
        error_str = str(error)

        # Extract the root cause (the innermost exception)
        root_cause: BaseException = error
        while root_cause.__cause__ is not None:
            root_cause = root_cause.__cause__

        # Try to extract cell number from error message or traceback
        cell_number: int | None = None
        cell_match = re.search(r"[Cc]ell\s*#?(\d+)", error_str + tb_str)
        if cell_match:
            cell_number = int(cell_match.group(1))

        # Try to find the error class and message.
        # Walk the exception chain looking for CellExecutionError (or similar)
        # which carries ename/evalue with the actual Python error details.
        # This avoids displaying the verbose CellExecutionError.__str__() output.
        exc_to_check: BaseException | None = error
        while exc_to_check is not None:
            if hasattr(exc_to_check, "ename") and hasattr(exc_to_check, "evalue"):
                error_class = exc_to_check.ename
                error_message = exc_to_check.evalue
                break
            exc_to_check = exc_to_check.__cause__
        else:
            error_class = type(root_cause).__name__
            error_message = str(root_cause)

        # For C++ notebooks, try to extract compiler error from error output
        # xeus-cling format: "input_line_X:Y:Z: error: message"
        cpp_error_info: dict[str, str] = {}
        cpp_error_match = re.search(
            r"input_line_\d+:(\d+):(\d+):\s*error:\s*(.+?)(?:\n|$)",
            error_str + tb_str,
        )
        if cpp_error_match:
            cpp_error_info["line"] = cpp_error_match.group(1)
            cpp_error_info["column"] = cpp_error_match.group(2)
            cpp_error_info["message"] = cpp_error_match.group(3).strip()
            error_class = "CompilationError"
            error_message = cpp_error_info["message"]

        # Also check for generic clang-style errors
        if not cpp_error_info:
            clang_error = re.search(
                r":\s*(\d+):\s*(\d+):\s*error:\s*(.+?)(?:\n|$)",
                error_str + tb_str,
            )
            if clang_error:
                cpp_error_info["line"] = clang_error.group(1)
                cpp_error_info["column"] = clang_error.group(2)
                cpp_error_info["message"] = clang_error.group(3).strip()
                error_class = "CompilationError"
                error_message = cpp_error_info["message"]

        # Try to find the failing cell - prioritize tracked cell context if available
        cells = notebook.get("cells", [])
        failing_cell = None
        cell_source: str | None = None

        # Priority 1: Use tracked cell context (most reliable)
        if self._current_cell is not None:
            cell_number = self._current_cell.cell_index
            cell_source = self._current_cell.cell_source
            if 0 <= cell_number < len(cells):
                failing_cell = cells[cell_number]
        # Priority 2: Use cell number from error message
        elif cell_number is not None and 0 <= cell_number < len(cells):
            failing_cell = cells[cell_number]
        else:
            # Priority 3: Try multiple strategies to find the failing cell
            failing_cell, cell_number = self._find_failing_cell(cells, error_str + tb_str)

        # Build the enhanced error message
        parts = [f"Notebook execution failed: {payload.input_file_name}"]

        if cell_number is not None:
            parts.append(f"  Cell: #{cell_number}")

        # Get cell source - prefer tracked context, fall back to notebook cell
        if cell_source is None and failing_cell is not None:
            cell_source = failing_cell.get("source", "")

        if cell_source:
            # Get first few lines of the cell
            source_lines = cell_source.split("\n")[:8]
            if source_lines:
                snippet = "\n    ".join(source_lines)
                if len(source_lines) < len(cell_source.split("\n")):
                    snippet += "\n    ..."
                parts.append(f"  Cell content:\n    {snippet}")

        parts.append(f"  Error: {error_class}: {error_message}")

        # A missing kernelspec has a known remedy — point at it instead of
        # leaving the user to decode the jupyter_client error (issue #348).
        if error_class == "NoSuchKernel" or "No such kernel" in error_str:
            parts.append(
                "  Hint: the Jupyter kernel is not installed on this machine; "
                "HTML generation needs it. Install the kernelspec, or use "
                "'clm build --no-html' for a kernel-free build."
            )

        # Include line/column number if found (especially useful for C++)
        if cpp_error_info:
            parts.append(f"  Line: {cpp_error_info['line']}, Column: {cpp_error_info['column']}")
        else:
            line_match = re.search(r"line\s+(\d+)", error_str + tb_str, re.IGNORECASE)
            if line_match:
                parts.append(f"  Line: {line_match.group(1)}")

        enhanced_message = "\n".join(parts)
        enhanced = RuntimeError(enhanced_message)
        enhanced.notebook_error_class = error_class  # type: ignore[attr-defined]
        enhanced.notebook_error_message = error_message  # type: ignore[attr-defined]
        enhanced.notebook_cell_number = cell_number  # type: ignore[attr-defined]
        enhanced.notebook_code_snippet = cell_source  # type: ignore[attr-defined]
        return enhanced

    def _find_failing_cell(self, cells: list, error_text: str) -> tuple[dict | None, int | None]:
        """Find the cell that caused an execution error.

        Uses multiple strategies:
        1. Look for cells with error output type
        2. Look for cells with stderr containing error patterns
        3. Find the cell with the highest execution_count (most recently executed)
        4. Return first code cell as fallback

        Args:
            cells: List of notebook cells
            error_text: Combined error message and traceback for pattern matching

        Returns:
            Tuple of (failing_cell, cell_index) or (None, None) if not found
        """
        # Strategy 1: Look for cells with error output type
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            outputs = cell.get("outputs", [])
            for output in outputs:
                if output.get("output_type") == "error":
                    return cell, idx

        # Strategy 2: Look for cells with stderr containing error patterns
        # C++ compilation errors often appear in stderr stream
        error_patterns = ["error:", "Error:", "ERROR:", "undefined", "undeclared"]
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            outputs = cell.get("outputs", [])
            for output in outputs:
                if output.get("output_type") == "stream" and output.get("name") == "stderr":
                    text = output.get("text", "")
                    if isinstance(text, list):
                        text = "".join(text)
                    if any(pattern in text for pattern in error_patterns):
                        return cell, idx

        # Strategy 3: Find the cell with the highest execution_count
        # This is likely the most recently executed cell where the error occurred
        max_exec_count = -1
        max_exec_idx = -1
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            exec_count = cell.get("execution_count")
            if exec_count is not None and exec_count > max_exec_count:
                max_exec_count = exec_count
                max_exec_idx = idx

        if max_exec_idx >= 0:
            return cells[max_exec_idx], max_exec_idx

        # Strategy 4: Return first code cell as fallback
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") == "code":
                return cell, idx

        return None, None

    async def write_other_files(
        self, cid: str, path: Path, payload: NotebookPayload, source_dir: Path | None = None
    ):
        """Write supporting files to the execution directory.

        In Docker mode with source mount (source_dir is set), files are already
        available at the source directory and don't need to be written.
        In other modes, files are decoded from base64 and written to temp directory.

        Args:
            cid: Correlation ID for logging
            path: Target directory to write files to (temp directory)
            payload: Notebook payload containing other_files
            source_dir: Optional source directory (Docker mode with source mount)
        """
        if source_dir is not None:
            # Docker mode with source mount: files are already available
            # No need to write anything
            logger.debug(f"{cid}:Source mount mode - files available at {source_dir}")
            return

        # Standard mode: decode and write files from payload
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.write_other_files_sync, cid, path, payload)

    @staticmethod
    def write_other_files_sync(cid: str, path: Path, payload: NotebookPayload):
        for extra_file, encoded_contents in payload.other_files.items():
            contents = b64decode(encoded_contents)
            logger.debug(f"{cid}:Writing extra file {extra_file}")
            file_path = path / extra_file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(contents)
        if hasattr(os, "sync"):
            os.sync()

    def _create_cpp_code_export(self, processed_nb) -> str:
        """Emit a compilable C++ translation unit for ``format="code"``.

        Replaces the jupytext concatenation for C++ decks (issue #333): the
        concatenation yields top-level statements and mid-file includes,
        which is not valid C++. ``processed_nb`` has already been filtered
        for this output spec, so the emitter sees exactly the cells of this
        (language × kind) view. For code-along-style specs, blanked cells
        become ``// TODO`` slide stubs.
        """
        sources = [cell.source for cell in processed_nb.cells if is_code_cell(cell)]
        return emit_cpp_translation_unit(
            sources, empty_cells_as_todo=self.output_spec.blanks_code_cells
        )

    async def _create_using_jupytext(self, processed_nb) -> str:
        config = jupytext_config.JupytextConfiguration(
            notebook_metadata_filter="-all", cell_metadata_filter="-all"
        )
        output = cast(
            str,
            jupytext.writes(
                processed_nb,
                fmt=self.output_spec.jupytext_format,
                config=config,
            ),
        )
        if not output.endswith("\n"):
            output += "\n"
        return output
