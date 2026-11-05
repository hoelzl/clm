"""Which recordings dashboards are running on this machine (#1093).

``clm recordings serve`` keeps every course's state in memory and writes it
back whole, and it may be recording right now. A CLI verb that changes the
state file or moves video files behind its back would race the session and be
overwritten by the dashboard's next save. So a dashboard leaves a marker file
beside the state files for as long as it runs, and such a verb refuses to run
while a live one exists.

The marker is a hint with a liveness check, never a lock: a dashboard that was
killed leaves a stale marker, recognised by its pid (and the process start time,
against pid reuse) and removed by the next reader. A failure to write one must
never stop a dashboard from serving.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]
from loguru import logger

from clm.recordings import state as recording_state

__all__ = ["dashboard_marker", "live_dashboards"]

_MARKER_GLOB = "dashboard-*.marker.json"


def _process_start_time(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError, ValueError):
        return None


@contextmanager
def dashboard_marker(url: str, root: Path) -> Iterator[None]:
    """Mark this process as a running dashboard for the duration of the block."""
    pid = os.getpid()
    path = recording_state.get_recordings_dir() / f"dashboard-{pid}.marker.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "pid": pid,
                    "started": _process_start_time(pid),
                    "url": url,
                    "root": str(root),
                }
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Could not write dashboard marker {}: {}", path, exc)
    try:
        yield
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove dashboard marker {}: {}", path, exc)


def live_dashboards() -> list[dict[str, Any]]:
    """The dashboards running on this machine; stale markers are removed on the way."""
    directory = recording_state.get_recordings_dir()
    if not directory.is_dir():
        return []
    live: list[dict[str, Any]] = []
    for path in sorted(directory.glob(_MARKER_GLOB)):
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
            pid = int(marker["pid"])
            recorded = marker.get("started")
        except (OSError, ValueError, KeyError, TypeError):
            _discard(path)
            continue
        started = _process_start_time(pid)
        if started is None or (recorded is not None and abs(started - float(recorded)) > 2.0):
            _discard(path)
            continue
        live.append(marker)
    return live


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove stale dashboard marker {}: {}", path, exc)
