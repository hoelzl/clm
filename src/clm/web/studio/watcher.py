"""External-change watcher — the cross-tool half of the two-editor guard.

``watchfiles`` watches the course ``slides/`` dir. When a deck ``.py`` changes
on disk *and the change was not our own write*, the server pushes a
``deck-changed-on-disk`` event over the WebSocket so the phone can show
"changed on disk — reload" and disable Save until the user re-fetches. This
closes the fetch→write gap for the VS Code case that optimistic concurrency
alone cannot see until a write is attempted.
"""

from __future__ import annotations

import asyncio
import logging

from clm.web.studio.service import StudioService

logger = logging.getLogger(__name__)


async def watch_slides_dir(service: StudioService) -> None:
    """Watch the slides dir and broadcast external deck changes over WS.

    Runs until cancelled (the ``clm serve`` lifespan cancels it at shutdown).
    Self-writes (recent edits made through the Studio API) are filtered out so
    the phone is not told its own save was an external change.
    """
    try:
        from watchfiles import awatch
    except ImportError:  # pragma: no cover - watchfiles ships with the [web] extra
        logger.warning("watchfiles unavailable; Studio external-change watcher disabled")
        return

    from clm.web.api.websocket import ws_manager

    slides_dir = service.slides_dir
    if not slides_dir.exists():
        logger.warning("Studio watcher: slides dir %s does not exist", slides_dir)
        return

    logger.info("Studio watcher: watching %s", slides_dir)
    try:
        async for changes in awatch(slides_dir):
            seen: set[str] = set()
            for _change, raw_path in changes:
                from pathlib import Path

                path = Path(raw_path)
                if path.suffix != ".py":
                    continue
                try:
                    deck_id = service._rel(path)
                except (ValueError, OSError):
                    continue
                if deck_id in seen:
                    continue
                seen.add(deck_id)
                if service.is_self_write(deck_id):
                    continue
                await ws_manager.broadcast(
                    {"type": "deck-changed-on-disk", "deck_id": deck_id},
                    channel="studio",
                )
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("Studio watcher: stopped")
        raise
