"""OBS WebSocket client wrapper for recording workflow integration.

Wraps ``obsws-python`` to provide a focused interface for recording-related
operations: connection lifecycle, recording status queries, and event
subscriptions for ``RecordStateChanged``.

The ``obsws-python`` package uses lazy imports so that the rest of the
recordings module can be used without OBS installed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@contextmanager
def _silence_obsws_connection_logging() -> Iterator[None]:
    """Temporarily mute the ``obsws_python.baseclient`` stdlib logger.

    ``obsws_python.baseclient.ObsClient.__init__`` calls
    ``self.logger.exception(...)`` on ``ConnectionRefusedError`` and
    ``TimeoutError``, which writes a full traceback to the root stdlib
    logger *before* the exception is re-raised. We catch that same
    exception at the wrapper level and log a friendlier warning, so
    suppress the library's noisier record to keep startup output clean
    when OBS simply isn't running.

    Only records at ERROR level and below are dropped; CRITICAL is still
    allowed through in case something truly catastrophic happens. The
    previous level is restored afterwards so user-tuned logging configs
    (e.g. enabling DEBUG for troubleshooting) are not clobbered.
    """
    # obsws_python.baseclient.ObsClient uses ``logger.getChild("ObsClient")``,
    # so the fully-qualified logger name is ``obsws_python.baseclient.ObsClient``.
    target = logging.getLogger("obsws_python.baseclient.ObsClient")
    previous_level = target.level
    previous_disabled = target.disabled
    target.setLevel(logging.CRITICAL)
    target.disabled = True
    try:
        yield
    finally:
        target.setLevel(previous_level)
        target.disabled = previous_disabled


@dataclass
class RecordingEvent:
    """Data from an OBS ``RecordStateChanged`` event."""

    output_active: bool
    output_state: str
    output_path: str | None = None


class ObsClient:
    """Thin wrapper around obsws-python for recording operations.

    Manages both a *request* client (for queries like ``get_record_status``)
    and an *event* client (for ``RecordStateChanged`` callbacks).

    Usage::

        client = ObsClient(host="localhost", port=4455)
        client.on_record_state_changed(my_callback)
        client.connect()
        ...
        client.disconnect()

    Or as a context manager::

        with ObsClient() as client:
            ...
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4455,
        password: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._req: Any | None = None  # obsws_python.ReqClient
        self._evt: Any | None = None  # obsws_python.EventClient
        self._record_callbacks: list[Callable[[RecordingEvent], None]] = []
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        """Whether the client is currently connected to OBS."""
        return self._req is not None

    def connect(self) -> None:
        """Connect to OBS WebSocket (both request and event clients).

        Raises:
            ImportError: If ``obsws-python`` is not installed.
            ConnectionError: If OBS is not reachable.
        """
        import obsws_python  # type: ignore[import-untyped]

        with _silence_obsws_connection_logging():
            try:
                req = obsws_python.ReqClient(
                    host=self._host,
                    port=self._port,
                    password=self._password,
                )
            except Exception as exc:
                raise ConnectionError(
                    f"Cannot connect to OBS at {self._host}:{self._port}: {exc}"
                ) from exc

            try:
                evt = obsws_python.EventClient(
                    host=self._host,
                    port=self._port,
                    password=self._password,
                )
                evt.callback.register(self._make_record_handler())
            except Exception as exc:
                req.disconnect()
                raise ConnectionError(
                    f"Cannot connect OBS event client at {self._host}:{self._port}: {exc}"
                ) from exc

        with self._lock:
            self._req = req
            self._evt = evt

        logger.info("Connected to OBS at {}:{}", self._host, self._port)

    def disconnect(self) -> None:
        """Disconnect from OBS, cleaning up both clients."""
        with self._lock:
            evt, self._evt = self._evt, None
            req, self._req = self._req, None

        if evt is not None:
            try:
                evt.disconnect()
            except Exception:
                pass
        if req is not None:
            try:
                req.disconnect()
            except Exception:
                pass

        logger.info("Disconnected from OBS")

    def on_record_state_changed(self, callback: Callable[[RecordingEvent], None]) -> None:
        """Register a callback for OBS ``RecordStateChanged`` events.

        Callbacks are invoked on the obsws-python daemon thread.
        Register callbacks *before* calling :meth:`connect` to avoid
        missing early events.
        """
        self._record_callbacks.append(callback)

    def get_record_status(self) -> RecordingEvent:
        """Query the current recording status from OBS.

        Returns a :class:`RecordingEvent` with ``output_active`` and
        ``output_state`` populated.  ``output_path`` may be ``None``
        depending on OBS version.
        """
        req = self._require_connected()
        resp = req.get_record_status()
        return RecordingEvent(
            output_active=resp.output_active,
            output_state=getattr(resp, "output_state", "unknown"),
            output_path=getattr(resp, "output_path", None),
        )

    def get_record_directory(self) -> Path:
        """Get the directory where OBS saves recordings."""
        req = self._require_connected()
        resp = req.get_record_directory()
        return Path(resp.record_directory)

    def start_record(self) -> None:
        """Tell OBS to begin recording.

        Thin wrapper around ``obsws-python``'s ``start_record`` request.
        The STARTED event arrives asynchronously via the EventClient; do
        not rely on this call blocking until the recording is actually
        running. OBS itself rejects the request if a recording is already
        in progress — the underlying library surfaces that as an
        exception, which we re-raise as :class:`ConnectionError` with a
        friendly message so the web layer can present it cleanly.

        Raises:
            ConnectionError: If not connected to OBS or if OBS rejected
                the request (e.g. already recording, no scene configured).
        """
        req = self._require_connected()
        try:
            req.start_record()
        except Exception as exc:
            raise ConnectionError(f"OBS rejected start_record: {exc}") from exc
        logger.info("Requested OBS to start recording")

    def stop_record(self) -> None:
        """Tell OBS to stop the current recording.

        The STOPPED event arrives asynchronously; the existing session
        state machine handles the rename. Raises if OBS rejects the
        request — typically because no recording is in progress.

        Raises:
            ConnectionError: If not connected to OBS or if OBS rejected
                the request.
        """
        req = self._require_connected()
        try:
            req.stop_record()
        except Exception as exc:
            raise ConnectionError(f"OBS rejected stop_record: {exc}") from exc
        logger.info("Requested OBS to stop recording")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_connected(self) -> Any:
        """Return the request client or raise if not connected."""
        req = self._req
        if req is None:
            raise ConnectionError("Not connected to OBS. Call connect() first.")
        return req

    def _make_record_handler(self) -> Callable:
        """Create a handler whose name matches the obsws-python convention.

        ``obsws-python`` dispatches events by matching the callback function
        name to ``on_<event_name_snake_case>``.
        """

        def on_record_state_changed(data: Any) -> None:
            event = RecordingEvent(
                output_active=data.output_active,
                output_state=getattr(data, "output_state", "unknown"),
                output_path=getattr(data, "output_path", None),
            )
            for cb in self._record_callbacks:
                try:
                    cb(event)
                except Exception:
                    logger.exception("Error in record state callback")

        return on_record_state_changed

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> ObsClient:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()
