"""API-backed adapter for the executed_notebooks cache.

In Docker / API mode, notebook workers cannot open ``clm_cache.db``
directly (SQLite WAL mode is unreliable over Windows bind-mounts, and the
host owns the cache). This adapter satisfies the same surface as
:class:`ExecutedNotebookCache` (``get`` and ``store``) by going through the
Worker REST API instead of a local SQLite connection.

The host's :class:`WorkerApiServer` exposes
``GET /api/worker/cache/executed_notebook`` and
``POST /api/worker/cache/executed_notebook`` for these reads/writes; the
on-the-wire payload is gzip-compressed pickle bytes.
"""

from __future__ import annotations

import logging
import pickle
from typing import TYPE_CHECKING, cast

from clm.infrastructure.api.client import WorkerApiClient

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
        pickle_bytes = self._client.get_executed_notebook(
            input_file=input_file,
            content_hash=content_hash,
            language=language,
            prog_lang=prog_lang,
        )
        if pickle_bytes is None:
            return None
        try:
            return cast("NotebookNode", pickle.loads(pickle_bytes))
        except Exception as e:
            logger.warning(
                f"Failed to unpickle executed_notebook for {input_file} "
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

        Pickles the notebook locally and ships the bytes via the REST API.
        Failures are logged inside ``WorkerApiClient.store_executed_notebook``
        but do not raise — caching is best-effort.
        """
        pickle_bytes = pickle.dumps(executed_notebook)
        self._client.store_executed_notebook(
            input_file=input_file,
            content_hash=content_hash,
            language=language,
            prog_lang=prog_lang,
            pickle_bytes=pickle_bytes,
        )
