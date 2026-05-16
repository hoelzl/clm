"""Tests for ApiExecutedNotebookCache (the Docker-side cache adapter).

The adapter satisfies the same ``get``/``store`` shape as the SQLite
:class:`ExecutedNotebookCache` so :class:`NotebookProcessor` can use either
backend transparently. These tests exercise the pickle and transport
boundary against a mocked ``WorkerApiClient`` — the real over-HTTP
round-trip is covered by ``test_executed_notebook_cache_endpoints``.
"""

from __future__ import annotations

import pickle
from unittest.mock import MagicMock

from nbformat.v4 import new_code_cell, new_notebook

from clm.infrastructure.api.api_executed_notebook_cache import (
    ApiExecutedNotebookCache,
)


def _sample_nb():
    nb = new_notebook()
    nb.cells.append(new_code_cell(source="z = 42"))
    return nb


def _key():
    return {
        "input_file": "/tmp/foo.py",
        "content_hash": "deadbeef",
        "language": "en",
        "prog_lang": "python",
    }


class TestGet:
    def test_returns_none_on_miss(self):
        client = MagicMock()
        client.get_executed_notebook.return_value = None
        cache = ApiExecutedNotebookCache(client)

        result = cache.get(**_key())

        assert result is None
        client.get_executed_notebook.assert_called_once_with(**_key())

    def test_unpickles_pickle_bytes_on_hit(self):
        client = MagicMock()
        nb = _sample_nb()
        client.get_executed_notebook.return_value = pickle.dumps(nb)
        cache = ApiExecutedNotebookCache(client)

        result = cache.get(**_key())

        assert result is not None
        assert result.cells[0].source == "z = 42"

    def test_returns_none_on_malformed_pickle(self):
        """A corrupted cache entry must not abort notebook processing —
        falling back to direct execution is always safe."""
        client = MagicMock()
        client.get_executed_notebook.return_value = b"not a pickle"
        cache = ApiExecutedNotebookCache(client)

        result = cache.get(**_key())

        assert result is None


class TestStore:
    def test_pickles_notebook_and_forwards_to_client(self):
        client = MagicMock()
        cache = ApiExecutedNotebookCache(client)
        nb = _sample_nb()

        cache.store(executed_notebook=nb, **_key())

        client.store_executed_notebook.assert_called_once()
        kwargs = client.store_executed_notebook.call_args.kwargs
        assert kwargs["input_file"] == "/tmp/foo.py"
        assert kwargs["content_hash"] == "deadbeef"
        assert kwargs["language"] == "en"
        assert kwargs["prog_lang"] == "python"

        round_tripped = pickle.loads(kwargs["pickle_bytes"])
        assert round_tripped.cells[0].source == "z = 42"
