"""Lifecycle manager for the mitmproxy subprocess.

The manager handles the operational mechanics of running ``mitmdump`` as
a child of the ``clm build`` parent process: locating the executable,
picking a free port, loading our addon, waiting for the proxy to accept
TCP connections before workers spawn, and graceful shutdown.

Integrated into ``clm build`` as the HTTP-replay transport (issue #165): the
build starts one manager for the whole run and stops it in its ``finally``.
The bind host is ``127.0.0.1`` for Direct-only builds and
``0.0.0.0`` when Docker workers must reach the proxy via ``host.docker.internal``
(P4); same-host clients and the readiness poll always connect via loopback
(:meth:`_client_host`).
"""

from __future__ import annotations

import collections
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import TracebackType

logger = logging.getLogger(__name__)

# Path to the addon module so we can pass it to ``mitmdump --scripts``.
_ADDON_PATH = Path(__file__).parent / "addon.py"

# How long we wait for the proxy to accept TCP connections before giving up.
# Local mitmdump startup is typically <500ms, but a loaded host — heavy xdist
# parallelism, or background OS work like Windows installing updates — can push
# the port-bind well past that. The readiness poll returns the instant the port
# accepts, so a generous default is nearly free on the happy path; the full
# budget only elapses for an *alive-but-not-yet-listening* process (a genuine
# crash short-circuits via ``poll()``). ``CLM_MITM_STARTUP_TIMEOUT`` (seconds)
# overrides it for CI or build hosts that need more headroom.
_DEFAULT_STARTUP_TIMEOUT_SECONDS = 30.0
_STARTUP_TIMEOUT_ENV = "CLM_MITM_STARTUP_TIMEOUT"


def _startup_timeout_seconds() -> float:
    """Resolve the proxy-readiness budget, honouring ``CLM_MITM_STARTUP_TIMEOUT``.

    Returns the env override when it parses to a positive number, otherwise
    ``_DEFAULT_STARTUP_TIMEOUT_SECONDS``. An unparseable or non-positive value
    is ignored with a warning so a typo can't silently disable the wait.
    """
    raw = os.environ.get(_STARTUP_TIMEOUT_ENV)
    if raw is None:
        return _DEFAULT_STARTUP_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Ignoring non-numeric %s=%r; using default %.1fs.",
            _STARTUP_TIMEOUT_ENV,
            raw,
            _DEFAULT_STARTUP_TIMEOUT_SECONDS,
        )
        return _DEFAULT_STARTUP_TIMEOUT_SECONDS
    if value <= 0:
        logger.warning(
            "Ignoring non-positive %s=%r; using default %.1fs.",
            _STARTUP_TIMEOUT_ENV,
            raw,
            _DEFAULT_STARTUP_TIMEOUT_SECONDS,
        )
        return _DEFAULT_STARTUP_TIMEOUT_SECONDS
    return value


# How long we wait for graceful shutdown before sending SIGKILL/terminate.
# mitmdump flushes flow streams on exit, so we want enough time for that.
_SHUTDOWN_GRACE_SECONDS = 5.0

# Bounded ring buffer of mitmdump stdout lines. A reader thread drains the
# pipe continuously so a multi-hour build can never deadlock on a full
# OS pipe buffer (~64 KiB) while mitmdump blocks on write; we keep only the
# tail, which is all startup-failure / shutdown diagnostics need.
_OUTPUT_RING_LINES = 1000

# Addon log lines carrying this sentinel are re-logged through CLM's own
# logger as they are drained, so the addon's once-per-build untagged-flow
# warning reaches the build log instead of dying in the ring buffer (which is
# only ever shown on a startup failure). Must match
# ``addon.UNTAGGED_FLOW_SENTINEL`` — duplicated as a literal because the addon
# module is also loaded standalone inside the mitmdump interpreter; a
# drift-guard test pins the two together.
_UNTAGGED_FLOW_SENTINEL = "CLM-HTTP-REPLAY-UNTAGGED"


class MitmproxyError(RuntimeError):
    """Raised when the proxy subprocess fails to start or exits unexpectedly."""


class MitmproxyManager:
    """Manage a single ``mitmdump`` subprocess for an HTTP-replay session.

    Used as a context manager — the proxy is started on ``__enter__``
    and stopped (gracefully, with a force-kill fallback) on ``__exit__``.

    Worker subprocesses spawned while the manager is active should have
    :meth:`env_vars` merged into their environment so their HTTP traffic
    is routed through the proxy.
    """

    def __init__(
        self,
        cassette_path: Path,
        mode: str = "replay",
        listen_host: str = "127.0.0.1",
        listen_port: int | None = None,
        confdir: Path | None = None,
        extra_args: list[str] | None = None,
        ignore_hosts: tuple[str, ...] | list[str] = (),
        trace_dir: Path | None = None,
    ) -> None:
        self.cassette_path = Path(cassette_path)
        self.mode = mode
        self.listen_host = listen_host
        self._configured_port = listen_port
        # Forensic HTTP-replay trace directory (issue #165 P5). When set, the
        # addon writes per-flow proxy events to ``proxy-<pid>.jsonl`` there so
        # ``scripts/analyze_http_replay_trace.py`` can cross-reference the
        # worker socket stream against the proxy's interception decisions.
        self.trace_dir = Path(trace_dir) if trace_dir is not None else None
        self.listen_port: int | None = None  # set on start
        # mitmproxy stores its CA + config under ``confdir``. Per-build
        # isolation keeps the CA out of the user's home directory and gives
        # each build its own short-lived CA.
        self.confdir = Path(confdir) if confdir is not None else None
        self.extra_args = extra_args or []
        # Hosts the addon forwards but never records (LangSmith telemetry by
        # default); passed through to the addon as ``clm_ignore_hosts``.
        self.ignore_hosts = tuple(ignore_hosts)
        # Per-build id: the addon names its staging files
        # ``<cassette>.staging-mitm-<build_id>`` so the host can mark
        # exactly this build's recordings complete after the proxy stops
        # (see ``Course.merge_mitmproxy_cassette_staging``).
        self.build_id = uuid.uuid4().hex
        self._process: subprocess.Popen | None = None
        # Reader thread + bounded ring buffer draining mitmdump stdout so the
        # pipe never fills and blocks the proxy on a long build.
        self._output: collections.deque[str] = collections.deque(maxlen=_OUTPUT_RING_LINES)
        self._reader_thread: threading.Thread | None = None

    def _client_host(self) -> str:
        """Loopback-reachable host clients use to connect to the proxy.

        When we bind the IPv4 wildcard (``0.0.0.0`` / empty, for Docker
        reachability — see :meth:`start`), clients on the same machine must
        still connect via loopback — ``connect("0.0.0.0")`` is invalid on
        Windows and unreliable elsewhere. Direct-mode workers and our own
        readiness poll therefore use ``127.0.0.1``. When we bind a concrete
        host we connect to exactly that host.

        Only IPv4 is supported: ``build`` only ever emits ``0.0.0.0`` or
        ``127.0.0.1``, and :func:`_pick_free_port` binds an ``AF_INET`` socket.
        """
        if self.listen_host in ("0.0.0.0", ""):
            return "127.0.0.1"
        return self.listen_host

    @property
    def proxy_url(self) -> str:
        if self.listen_port is None:
            raise MitmproxyError("Proxy not started; listen_port unknown")
        return f"http://{self._client_host()}:{self.listen_port}"

    @property
    def ca_cert_path(self) -> Path:
        """Filesystem path to mitmproxy's CA certificate.

        Workers that need to validate HTTPS traffic through the proxy
        should trust this file (typically by setting ``SSL_CERT_FILE``
        and ``REQUESTS_CA_BUNDLE``).
        """
        confdir = self.confdir if self.confdir is not None else (Path.home() / ".mitmproxy")
        return confdir / "mitmproxy-ca-cert.pem"

    def env_vars(self, *, include_ca: bool = False) -> dict[str, str]:
        """Loopback proxy (and optionally CA) env vars for a worker process.

        Convenience helper; the integrated ``clm build`` path does not call it
        — it splices a combined certifi+proxy-CA bundle into ``os.environ`` for
        Direct workers (``build._maybe_start_mitmproxy_transport``) and mounts
        the CA per Docker container (``worker_executor._mitmproxy_docker_env``).
        ``include_ca`` defaults off because the CA cert is only written once
        mitmdump has started; callers that need HTTPS interception must ensure
        the cert exists first.
        """
        env: dict[str, str] = {
            "HTTP_PROXY": self.proxy_url,
            "HTTPS_PROXY": self.proxy_url,
            "http_proxy": self.proxy_url,
            "https_proxy": self.proxy_url,
        }
        if include_ca:
            cert = str(self.ca_cert_path)
            env.update(
                {
                    "SSL_CERT_FILE": cert,
                    "REQUESTS_CA_BUNDLE": cert,
                    "CURL_CA_BUNDLE": cert,
                }
            )
        return env

    def __enter__(self) -> MitmproxyManager:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> None:
        if self._process is not None:
            raise MitmproxyError("Manager already started")

        mitmdump = _locate_mitmdump()
        self.listen_port = self._configured_port or _pick_free_port(self.listen_host)

        cmd: list[str] = [
            mitmdump,
            "--listen-host",
            self.listen_host,
            "--listen-port",
            str(self.listen_port),
            "--scripts",
            str(_ADDON_PATH),
            "--set",
            f"clm_cassette_path={self.cassette_path}",
            "--set",
            f"clm_mode={self.mode}",
            "--set",
            f"clm_build_id={self.build_id}",
            "--set",
            f"clm_ignore_hosts={','.join(self.ignore_hosts)}",
            # Quiet flow logging — the addon emits its own structured logs.
            "--set",
            "termlog_verbosity=warn",
            "--set",
            "flow_detail=0",
        ]
        if self.confdir is not None:
            cmd.extend(["--set", f"confdir={self.confdir}"])
        if self.trace_dir is not None:
            cmd.extend(["--set", f"clm_trace_dir={self.trace_dir}"])
        cmd.extend(self.extra_args)

        env = os.environ.copy()
        # Make the addon's logger visible.
        env.setdefault("PYTHONUNBUFFERED", "1")

        # On Windows we need CREATE_NEW_PROCESS_GROUP so we can send
        # CTRL_BREAK_EVENT for graceful shutdown without killing the
        # parent.
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        logger.info("Starting mitmdump: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        self._start_reader()

        try:
            self._wait_for_ready()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        if self._process is None:
            return
        proc = self._process
        self._process = None

        if proc.poll() is not None:
            # Already exited.
            return

        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGTERM)
        except (ProcessLookupError, OSError) as exc:
            logger.warning("Failed to send graceful shutdown to mitmdump: %s", exc)

        try:
            proc.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("mitmdump did not exit within %.1fs; forcing", _SHUTDOWN_GRACE_SECONDS)
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                logger.error("mitmdump survived SIGKILL; leaking process")

        # The reader thread exits when the pipe hits EOF (process gone). Join
        # it so we don't leak a thread per build and the ring buffer is final.
        reader = self._reader_thread
        self._reader_thread = None
        if reader is not None:
            reader.join(timeout=2.0)

    def _start_reader(self) -> None:
        """Spawn a daemon thread that drains mitmdump stdout into the ring buffer.

        Without a continuous drain, a long build can fill the OS pipe buffer;
        mitmdump then blocks on its next write and the proxy stalls — a silent
        multi-hour-build deadlock. The thread reads until EOF (process exit).
        """
        if self._process is None or self._process.stdout is None:
            return

        stream = self._process.stdout

        def _pump() -> None:
            try:
                for raw in iter(stream.readline, b""):
                    self._handle_output_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            except (ValueError, OSError):
                # Stream closed underneath us during shutdown — expected.
                return

        self._reader_thread = threading.Thread(
            target=_pump, name="mitmdump-stdout-reader", daemon=True
        )
        self._reader_thread.start()

    def _handle_output_line(self, line: str) -> None:
        """Buffer one drained mitmdump stdout line; surface flagged addon warnings.

        Every line goes into the diagnostic ring buffer. Lines the addon marked
        with the untagged-flow sentinel are additionally re-logged at WARNING
        through CLM's logger immediately — the addon runs inside mitmdump, so
        without this relay its warning would only ever be visible in the ring
        buffer dump of a startup *failure*, i.e. never for the builds that
        actually have the problem.
        """
        self._output.append(line)
        if _UNTAGGED_FLOW_SENTINEL in line:
            logger.warning("mitmproxy replay: %s", line)

    def _wait_for_ready(self) -> None:
        """Poll the listen port until it accepts a TCP connection or we time out."""
        assert self._process is not None
        assert self.listen_port is not None

        timeout = _startup_timeout_seconds()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                output = self._drain_output()
                raise MitmproxyError(
                    f"mitmdump exited during startup (rc={self._process.returncode}):\n{output}"
                )
            try:
                with socket.create_connection((self._client_host(), self.listen_port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        output = self._drain_output()
        # Distinguish the two ways the budget can elapse so the error is
        # actionable. A still-running process bound too slowly — the host is
        # likely overloaded and the remedy is more time, not a code change. A
        # process that has since exited is a genuine startup failure.
        if self._process.poll() is None:
            raise MitmproxyError(
                f"mitmdump did not become ready within {timeout:.1f}s and is still "
                f"starting — the host may be overloaded. Set {_STARTUP_TIMEOUT_ENV} "
                f"(seconds) to allow more time if this recurs.\n{output}"
            )
        raise MitmproxyError(
            f"mitmdump exited (rc={self._process.returncode}) without becoming ready "
            f"within {timeout:.1f}s:\n{output}"
        )

    def _drain_output(self) -> str:
        """Return mitmdump's recent stdout (the ring buffer's tail).

        The reader thread owns the pipe, so we must not call ``communicate()``
        here (it would race the thread on the same fd). When the process has
        exited we give the reader a brief moment to flush the final lines, then
        snapshot the buffer.
        """
        reader = self._reader_thread
        if reader is not None and self._process is not None and self._process.poll() is not None:
            reader.join(timeout=1.0)
        return "\n".join(self._output)


def _locate_mitmdump() -> str:
    """Find the mitmdump executable for the current Python environment.

    Preference order: explicit ``CLM_MITMDUMP`` env override, then
    ``shutil.which`` (honors PATH), then the scripts directory of the
    running interpreter. Raises ``MitmproxyError`` if none find it.

    The ``CLM_MITMDUMP`` override is the recommended hook for the settled
    ``uv tool install mitmproxy`` model (and CI provisioning), where
    ``mitmdump`` lives in its own isolated environment rather than the
    worker venv's scripts directory.
    """
    override = os.environ.get("CLM_MITMDUMP")
    if override:
        if Path(override).exists():
            return override
        raise MitmproxyError(f"CLM_MITMDUMP={override!r} does not exist")

    found = shutil.which("mitmdump")
    if found:
        return found

    interpreter_dir = Path(sys.executable).parent
    candidates: list[Path] = []
    if sys.platform == "win32":
        candidates.append(interpreter_dir / "mitmdump.exe")
        candidates.append(interpreter_dir / "Scripts" / "mitmdump.exe")
    else:
        candidates.append(interpreter_dir / "mitmdump")
        candidates.append(interpreter_dir / "bin" / "mitmdump")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise MitmproxyError(
        "Could not locate mitmdump executable. Install the out-of-process proxy with: "
        "uv tool install mitmproxy --with vcrpy "
        "(the addon needs vcrpy in the mitmdump environment to read/write cassettes), "
        "then point CLM at it via the CLM_MITMDUMP env var if it is not on PATH."
    )


def _pick_free_port(host: str) -> int:
    """Bind to port 0 and let the OS pick a free port, then release.

    There is a small TOCTOU window between releasing the port here and
    mitmdump binding it; on a single host this is acceptable. ``host`` is
    always an IPv4 address (``0.0.0.0`` or ``127.0.0.1``), matching the
    ``AF_INET`` socket below.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        port: int = sock.getsockname()[1]
        return port
