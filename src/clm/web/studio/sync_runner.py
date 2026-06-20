"""P3b — Sync-to-other-language as a streamed subprocess.

Runs ``python -m clm slides sync <de_path> --yes`` as a subprocess and streams
its stdout/stderr lines over the Studio WS channel, so the phone sees progress
while the server-side LLM reconciliation runs. The sync writes **both** halves
and advances the watermark; on completion both halves are clean, so the language
lock (P3a) releases on the next open.

Decision (user, 2026-06-20): **subprocess**, not in-process ``apply_plan`` — the
heavy LLM/network imports stay out of the serve process and it matches CLM's
``clm run`` subprocess pattern. The subprocess **inherits the serve process's
cwd**, so it resolves the same ``.clm-cache`` watermark DB that
:meth:`StudioService.compute_lock` reads in-process — lock and sync stay in
lockstep.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

logger = logging.getLogger(__name__)

#: WS channel the Studio frontend subscribes to (shared with the watcher).
SYNC_CHANNEL = "studio"

#: Type of the per-line callback ``run_sync`` feeds the stream helper.
LineCallback = Callable[[str], Awaitable[None]]

#: Type of the (injectable, for tests) stream helper.
Streamer = Callable[[Sequence[str], LineCallback], Awaitable[int]]


async def _default_stream(cmd: Sequence[str], on_line: LineCallback) -> int:
    """Spawn ``cmd`` and feed each combined stdout/stderr line to ``on_line``.

    Returns the process exit code. Inherits the parent cwd/env so the child sees
    the same cache + project config the serve process does.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    async for raw in proc.stdout:
        await on_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
    return await proc.wait()


async def run_sync(
    service,  # noqa: ANN001 - StudioService (avoid an import cycle)
    deck_id: str,
    *,
    stream: Streamer = _default_stream,
) -> None:
    """Run a streamed sync for ``deck_id``'s split pair, broadcasting progress.

    Emits ``sync-started`` → ``sync-progress`` (per line) → ``sync-done`` on the
    Studio WS channel. Marks both halves as self-writes throughout so the
    external-change watcher does not report the sync's own writes back to the
    phone as a conflict. ``stream`` is injectable so tests can drive it without a
    real subprocess.
    """
    from clm.web.api.websocket import ws_manager

    cmd, de_id, en_id = service.resolve_sync_command(deck_id)

    def mark() -> None:
        # Refresh the self-write window on both halves; a sync can outlive the
        # 2s window, and the writes land near the end of the run.
        service.mark_self_write(de_id)
        service.mark_self_write(en_id)

    async def broadcast(message: dict) -> None:
        await ws_manager.broadcast(message, channel=SYNC_CHANNEL)

    mark()
    await broadcast({"type": "sync-started", "deck_id": deck_id})

    async def on_line(line: str) -> None:
        mark()
        await broadcast({"type": "sync-progress", "deck_id": deck_id, "line": line})

    try:
        code = await stream(cmd, on_line)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the phone
        logger.exception("Studio sync failed for %s", deck_id)
        await broadcast(
            {
                "type": "sync-done",
                "deck_id": deck_id,
                "ok": False,
                "exit_code": -1,
                "error": str(exc),
            }
        )
        return

    mark()
    await broadcast({"type": "sync-done", "deck_id": deck_id, "ok": code == 0, "exit_code": code})
