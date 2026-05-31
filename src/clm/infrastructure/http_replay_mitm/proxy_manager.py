"""Lifecycle manager for the mitmproxy subprocess.

The manager handles the operational mechanics of running ``mitmdump`` as
a child of the ``clm build`` parent process: locating the executable,
picking a free port, loading our addon, waiting for the proxy to accept
TCP connections before workers spawn, and graceful shutdown.

This is prototype scope — the manager is functional and used by the
smoke test, but is not yet integrated into ``clm build``. Wiring that up
is follow-up work; see the design doc for the integration plan.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import TracebackType

logger = logging.getLogger(__name__)

# Path to the addon module so we can pass it to ``mitmdump --scripts``.
_ADDON_PATH = Path(__file__).parent / "addon.py"

# How long we wait for the proxy to accept TCP connections before
# giving up. Local startup is typically <500ms; cap conservatively.
_STARTUP_TIMEOUT_SECONDS = 10.0

# How long we wait for graceful shutdown before sending SIGKILL/terminate.
# mitmdump flushes flow streams on exit, so we want enough time for that.
_SHUTDOWN_GRACE_SECONDS = 5.0


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
    ) -> None:
        self.cassette_path = Path(cassette_path)
        self.mode = mode
        self.listen_host = listen_host
        self._configured_port = listen_port
        self.listen_port: int | None = None  # set on start
        # mitmproxy stores its CA + config under ``confdir``. Per-build
        # isolation keeps the CA out of the user's home directory and
        # makes the prototype easier to reason about.
        self.confdir = Path(confdir) if confdir is not None else None
        self.extra_args = extra_args or []
        self._process: subprocess.Popen | None = None
        self._log_file = None

    @property
    def proxy_url(self) -> str:
        if self.listen_port is None:
            raise MitmproxyError("Proxy not started; listen_port unknown")
        return f"http://{self.listen_host}:{self.listen_port}"

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
        """Environment variables to merge into worker subprocesses.

        When ``include_ca`` is true, also exports cert-bundle paths so
        Python HTTP libraries trust the proxy for HTTPS interception.
        We default this off because the CA cert is only generated on
        first run; the smoke test exercises HTTP-only paths and doesn't
        need it.
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
            # Quiet flow logging — the addon emits its own structured logs.
            "--set",
            "termlog_verbosity=warn",
            "--set",
            "flow_detail=0",
        ]
        if self.confdir is not None:
            cmd.extend(["--set", f"confdir={self.confdir}"])
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

    def _wait_for_ready(self) -> None:
        """Poll the listen port until it accepts a TCP connection or we time out."""
        assert self._process is not None
        assert self.listen_port is not None

        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                output = self._drain_output()
                raise MitmproxyError(
                    f"mitmdump exited during startup (rc={self._process.returncode}):\n{output}"
                )
            try:
                with socket.create_connection((self.listen_host, self.listen_port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        output = self._drain_output()
        raise MitmproxyError(
            f"mitmdump did not become ready within {_STARTUP_TIMEOUT_SECONDS:.1f}s:\n{output}"
        )

    def _drain_output(self) -> str:
        if self._process is None or self._process.stdout is None:
            return ""
        try:
            data, _ = self._process.communicate(timeout=1.0)
            return (data or b"").decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return ""


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
    mitmdump binding it — acceptable for a prototype on localhost. A
    production version could keep the socket open and hand the fd to
    the child (Unix) or accept the small risk on Windows.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        port: int = sock.getsockname()[1]
        return port
