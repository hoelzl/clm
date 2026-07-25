"""API-backed adapter for the executed_notebooks cache.

In Docker / API mode, notebook workers cannot open ``clm_cache.db``
directly (SQLite WAL mode is unreliable over Windows bind-mounts, and the
host owns the cache). This adapter satisfies the same surface as
:class:`ExecutedNotebookCache` (``get`` and ``store``) by going through the
Worker REST API instead of a local SQLite connection.

The host's :class:`WorkerApiServer` exposes
``GET /api/worker/cache/executed_notebook`` and
``POST /api/worker/cache/executed_notebook`` for these reads/writes; the
on-the-wire payload is gzip-compressed nbformat JSON.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from clm.infrastructure.api.client import WorkerApiClient
from clm.infrastructure.notebook_serialization import (
    NotebookSerializationError,
    deserialize_notebook,
    serialize_notebook,
)

if TYPE_CHECKING:
    from nbformat import NotebookNode

logger = logging.getLogger(__name__)


class ApiExecutedNotebookCache:
    """``ExecutedNotebookCache``-shaped adapter that hits the Worker API.

    Only the ``get`` and ``store`` methods are implemented — these are the
    only entry points :class:`NotebookProcessor` uses on its ``cache``
    attribute. The other ``ExecutedNotebookCache`` methods (clear, vacuum,
    stats) are host-side maintenance and have no Docker-side analog.

    Usage::

        client = WorkerApiClient(api_url)
        cache = ApiExecutedNotebookCache(client)
        processor = NotebookProcessor(output_spec, cache=cache)
    """

    def __init__(self, client: WorkerApiClient):
        self._client = client

    def get(
        self,
        input_file: str,
        content_hash: str,
        language: str,
        prog_lang: str,
    ) -> NotebookNode | None:
        """Fetch a cached executed notebook from the host via the REST API.

        Returns ``None`` on cache miss or transport failure — both are
        treated as "fall back to direct execution" by the caller.
        """
        payload = self._client.get_executed_notebook(
            input_file=input_file,
            content_hash=content_hash,
            language=language,
            prog_lang=prog_lang,
        )
        if payload is None:
            return None
        try:
            return deserialize_notebook(payload)
        except NotebookSerializationError as e:
            logger.warning(
                f"Failed to parse executed_notebook for {input_file} "
                f"({language}, {prog_lang}); treating as cache miss: {e}"
            )
            return None

    def store(
        self,
        input_file: str,
        content_hash: str,
        language: str,
        prog_lang: str,
        executed_notebook: NotebookNode,
    ) -> None:
        """Send an executed notebook to the host's cache.

        Serializes the notebook locally and ships the bytes via the REST API.
        Failures are logged inside ``WorkerApiClient.store_executed_notebook``
        but do not raise — caching is best-effort, and so is a notebook that
        will not serialize.
        """
        try:
            payload = serialize_notebook(executed_notebook)
        except NotebookSerializationError as e:
            logger.warning(
                f"Not caching executed_notebook for {input_file} ({language}, {prog_lang}): {e}"
            )
            return
        self._client.store_executed_notebook(
            input_file=input_file,
            content_hash=content_hash,
            language=language,
            prog_lang=prog_lang,
            payload=payload,
        )
