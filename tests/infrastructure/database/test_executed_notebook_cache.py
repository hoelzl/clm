"""Tests for the ExecutedNotebookCache class."""

import tempfile
from pathlib import Path

import pytest
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from clx.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache


@pytest.fixture
def temp_db_path():
    """Create a temporary database file path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_cache.db"


@pytest.fixture
def sample_notebook():
    """Create a sample NotebookNode for testing."""
    nb = new_notebook()
    nb.cells = [
        new_markdown_cell("# Test Notebook"),
        new_code_cell("print('hello')"),
        new_code_cell("x = 1 + 1"),
    ]
    # Simulate execution outputs
    nb.cells[1]["outputs"] = [{"output_type": "stream", "name": "stdout", "text": "hello\n"}]
    nb.cells[2]["outputs"] = []
    nb.cells[2]["execution_count"] = 2
    return nb


class TestExecutedNotebookCache:
    """Tests for ExecutedNotebookCache."""

    def test_cache_creation(self, temp_db_path):
        """Test that cache creates database and table."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            assert temp_db_path.exists()
            # Check table exists
            cursor = cache.conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='executed_notebooks'"
            )
            assert cursor.fetchone() is not None

    def test_store_and_retrieve(self, temp_db_path, sample_notebook):
        """Test storing and retrieving a notebook."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            cache.store(
                input_file="/path/to/notebook.py",
                content_hash="abc123",
                language="en",
                prog_lang="python",
                executed_notebook=sample_notebook,
            )

            result = cache.get(
                input_file="/path/to/notebook.py",
                content_hash="abc123",
                language="en",
                prog_lang="python",
            )

            assert result is not None
            assert len(result.cells) == 3
            assert result.cells[0]["source"] == "# Test Notebook"
            assert result.cells[1]["outputs"][0]["text"] == "hello\n"

    def test_cache_miss(self, temp_db_path):
        """Test that cache miss returns None."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            result = cache.get(
                input_file="/path/to/nonexistent.py",
                content_hash="xyz789",
                language="de",
                prog_lang="python",
            )
            assert result is None

    def test_cache_key_uniqueness(self, temp_db_path, sample_notebook):
        """Test that different cache keys are stored separately."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            # Store with different languages
            cache.store("/path/nb.py", "hash1", "en", "python", sample_notebook)
            cache.store("/path/nb.py", "hash1", "de", "python", sample_notebook)

            # Modify notebook for de version
            de_notebook = new_notebook()
            de_notebook.cells = [new_markdown_cell("# German")]
            cache.store("/path/nb.py", "hash1", "de", "python", de_notebook)

            # Retrieve and verify they're different
            en_result = cache.get("/path/nb.py", "hash1", "en", "python")
            de_result = cache.get("/path/nb.py", "hash1", "de", "python")

            assert en_result.cells[0]["source"] == "# Test Notebook"
            assert de_result.cells[0]["source"] == "# German"

    def test_content_hash_invalidation(self, temp_db_path, sample_notebook):
        """Test that different content hash means cache miss."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            cache.store("/path/nb.py", "hash_v1", "en", "python", sample_notebook)

            # Same file, different hash should miss
            result = cache.get("/path/nb.py", "hash_v2", "en", "python")
            assert result is None

            # Original hash should still hit
            result = cache.get("/path/nb.py", "hash_v1", "en", "python")
            assert result is not None

    def test_replace_on_duplicate(self, temp_db_path, sample_notebook):
        """Test that INSERT OR REPLACE updates existing entries."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            # Store initial version
            cache.store("/path/nb.py", "hash1", "en", "python", sample_notebook)

            # Store updated version with same key
            updated_nb = new_notebook()
            updated_nb.cells = [new_markdown_cell("# Updated")]
            cache.store("/path/nb.py", "hash1", "en", "python", updated_nb)

            # Should get updated version
            result = cache.get("/path/nb.py", "hash1", "en", "python")
            assert result.cells[0]["source"] == "# Updated"

            # Should have only one entry (not two)
            cursor = cache.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM executed_notebooks")
            assert cursor.fetchone()[0] == 1

    def test_clear_all(self, temp_db_path, sample_notebook):
        """Test clearing all cache entries."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            cache.store("/path/nb1.py", "hash1", "en", "python", sample_notebook)
            cache.store("/path/nb2.py", "hash2", "de", "python", sample_notebook)

            deleted = cache.clear()
            assert deleted == 2

            assert cache.get("/path/nb1.py", "hash1", "en", "python") is None
            assert cache.get("/path/nb2.py", "hash2", "de", "python") is None

    def test_clear_specific_file(self, temp_db_path, sample_notebook):
        """Test clearing cache entries for a specific file."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            cache.store("/path/nb1.py", "hash1", "en", "python", sample_notebook)
            cache.store("/path/nb1.py", "hash1", "de", "python", sample_notebook)
            cache.store("/path/nb2.py", "hash2", "en", "python", sample_notebook)

            deleted = cache.clear(input_file="/path/nb1.py")
            assert deleted == 2

            # nb1 should be gone
            assert cache.get("/path/nb1.py", "hash1", "en", "python") is None
            # nb2 should still exist
            assert cache.get("/path/nb2.py", "hash2", "en", "python") is not None

    def test_get_stats(self, temp_db_path, sample_notebook):
        """Test getting cache statistics."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            cache.store("/path/nb1.py", "hash1", "en", "python", sample_notebook)
            cache.store("/path/nb2.py", "hash2", "de", "python", sample_notebook)
            cache.store("/path/nb3.py", "hash3", "en", "cpp", sample_notebook)

            stats = cache.get_stats()
            assert stats["total_entries"] == 3
            assert stats["by_language"]["en"] == 2
            assert stats["by_language"]["de"] == 1
            assert stats["by_prog_lang"]["python"] == 2
            assert stats["by_prog_lang"]["cpp"] == 1

    def test_empty_stats(self, temp_db_path):
        """Test stats on empty cache."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            stats = cache.get_stats()
            assert stats["total_entries"] == 0
            assert stats["by_language"] == {}
            assert stats["by_prog_lang"] == {}

    def test_persistence_across_connections(self, temp_db_path, sample_notebook):
        """Test that cache persists across connection close/reopen."""
        # Store in first connection
        with ExecutedNotebookCache(temp_db_path) as cache:
            cache.store("/path/nb.py", "hash1", "en", "python", sample_notebook)

        # Retrieve in second connection
        with ExecutedNotebookCache(temp_db_path) as cache:
            result = cache.get("/path/nb.py", "hash1", "en", "python")
            assert result is not None
            assert len(result.cells) == 3

    def test_concurrent_access_safe(self, temp_db_path, sample_notebook):
        """Test that WAL mode enables concurrent access."""
        # This test verifies that the database is configured for concurrent access
        with ExecutedNotebookCache(temp_db_path) as cache:
            cursor = cache.conn.cursor()
            cursor.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0].lower()
            assert mode == "wal"

    def test_without_context_manager_warns(self, temp_db_path, sample_notebook, caplog):
        """Test that using cache without context manager logs warning."""
        cache = ExecutedNotebookCache(temp_db_path)
        # conn is None without __enter__

        result = cache.get("/path/nb.py", "hash1", "en", "python")
        assert result is None
        assert "not initialized" in caplog.text

        cache.store("/path/nb.py", "hash1", "en", "python", sample_notebook)
        assert "not initialized" in caplog.text

    def test_different_prog_langs(self, temp_db_path, sample_notebook):
        """Test that different programming languages are cached separately."""
        with ExecutedNotebookCache(temp_db_path) as cache:
            py_nb = sample_notebook
            cpp_nb = new_notebook()
            cpp_nb.cells = [new_code_cell("// C++ code")]

            cache.store("/path/nb.py", "hash1", "en", "python", py_nb)
            cache.store("/path/nb.cpp", "hash1", "en", "cpp", cpp_nb)

            py_result = cache.get("/path/nb.py", "hash1", "en", "python")
            cpp_result = cache.get("/path/nb.cpp", "hash1", "en", "cpp")

            assert py_result.cells[1]["source"] == "print('hello')"
            assert cpp_result.cells[0]["source"] == "// C++ code"
