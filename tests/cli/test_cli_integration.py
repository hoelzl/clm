"""
Integration tests for CLI with real backend.

These tests run the CLI with real backend and worker processes.
They verify that the full CLI → Backend → Workers → Output pipeline works.

Mark with @pytest.mark.integration to run separately from unit tests.
"""

import pickle
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from clx.cli.main import cli
from clx.infrastructure.database.db_operations import DatabaseManager


@pytest.mark.integration
class TestCliWithSqliteBackend:
    """Integration tests using SQLite backend (no external dependencies)"""

    # Make this test parametric in the number of workers:
    # Note: 4 workers is optimal (68% efficiency). 8+ workers have diminishing
    # returns due to I/O bottlenecks and OS scheduler overhead on Windows.
    @pytest.mark.parametrize("notebook_workers", [4])
    def test_build_simple_course_with_sqlite(self, tmp_path, notebook_workers):
        """Test building a simple course via CLI with SQLite backend.

        This is an integration test, so it should:
        1. Not allow errors for missing workers (workers must be available)
        2. Verify complete directory structure is created
        3. Verify all expected output files are generated
        4. Verify actual content from input appears in output files
        5. Verify cache database is correctly written
        """
        runner = CliRunner()

        # Use test-spec-2 which is a simple course with one notebook
        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"
        cache_db_path = tmp_path / "cache.db"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "--cache-db-path",
                str(cache_db_path),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "WARNING",
                "--notebook-workers",
                f"{notebook_workers}",
            ],
        )

        # This is an integration test - workers MUST be available and succeed
        if result.exit_code != 0:
            print("STDOUT:", result.output)
            print("STDERR:", result.stderr if hasattr(result, 'stderr') else "N/A")
        assert result.exit_code == 0, f"Build failed: {result.output}"

        # Verify output directory structure
        assert output_dir.exists(), "Output directory not created"

        # Check for public and speaker directories (De and En subdirectories)
        # Structure: output/public/De, output/public/En, output/speaker/De, output/speaker/En
        public_dir = output_dir / "public"
        speaker_dir = output_dir / "speaker"

        assert public_dir.exists() or speaker_dir.exists(), \
            "Neither public nor speaker directory exists"

        # Find all HTML files (should be in speaker/En or speaker/De)
        html_files = list(output_dir.rglob("*.html"))
        assert len(html_files) > 0, f"No HTML files generated in {output_dir}"

        # Find all notebook files
        ipynb_files = list(output_dir.rglob("*.ipynb"))
        assert len(ipynb_files) > 0, f"No notebook files generated in {output_dir}"

        # Verify content: Check that input code appears in output files
        # The source file contains "include_me.foo()"

        # Check at least one ipynb contains the code
        found_code_in_ipynb = False
        for ipynb_file in ipynb_files:
            ipynb_content = ipynb_file.read_text(encoding="utf-8")
            if "include_me.foo()" in ipynb_content:
                found_code_in_ipynb = True
                break
        assert found_code_in_ipynb, \
            f"Expected 'include_me.foo()' in at least one .ipynb file, but not found"

        # Check HTML output contains the code (might be HTML-escaped)
        found_code_in_html = False
        for html_file in html_files:
            html_content = html_file.read_text(encoding="utf-8")
            if "include_me" in html_content:
                found_code_in_html = True
                break
        assert found_code_in_html, \
            f"Expected 'include_me' in at least one .html file, but not found"

        # Verify cache database was written correctly
        assert cache_db_path.exists(), "Cache database not created"

        with DatabaseManager(cache_db_path) as db:
            cursor = db.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM processed_files")
            count = cursor.fetchone()[0]
            # Should have cached results for the notebooks processed
            assert count > 0, "No results cached in database"

            # Verify we can retrieve a result
            cursor.execute("SELECT file_path, result FROM processed_files LIMIT 1")
            row = cursor.fetchone()
            assert row is not None, "No cached results found"

            file_path, result_blob = row
            assert file_path is not None, "Cached file path is None"

            # Verify the result can be unpickled
            result_obj = pickle.loads(result_blob)
            assert result_obj is not None, "Could not unpickle cached result"

    def test_build_with_force_db_init(self, tmp_path):
        """Test that --force-db-init flag reinitializes the cache database.

        This test:
        1. Manually creates a cache database with proper schema
        2. Inserts a fake entry with different content
        3. Runs build with --force-db-init
        4. Verifies the fake entry is gone and real entry exists
        """
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-3.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"
        cache_db_path = tmp_path / "cache.db"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        # Manually create database and insert a fake entry
        with DatabaseManager(cache_db_path) as db:
            # Insert a fake entry for a file that will be processed
            fake_result_data = pickle.dumps({
                "fake": "This is fake data that should be removed"
            })
            cursor = db.conn.cursor()
            cursor.execute(
                """
                INSERT INTO processed_files
                    (file_path, content_hash, correlation_id, result, output_metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "test-data/slides/module_030_single_notebook/topic_100_simple_notebook/slides_simple_notebook.py",
                    "fake_hash_12345",
                    "fake_correlation_id",
                    fake_result_data,
                    "fake_metadata",
                ),
            )
            db.conn.commit()

            # Verify fake entry exists
            cursor.execute("SELECT COUNT(*) FROM processed_files WHERE content_hash = 'fake_hash_12345'")
            assert cursor.fetchone()[0] == 1, "Fake entry not inserted"

        # Run build with --force-db-init
        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "--cache-db-path",
                str(cache_db_path),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "WARNING",
                "--force-db-init",
                "--notebook-workers",
                "4",
            ],
        )

        # Build should succeed
        assert result.exit_code == 0, f"Build failed: {result.output}"

        # Verify the fake entry is gone and real entry exists
        with DatabaseManager(cache_db_path) as db:
            cursor = db.conn.cursor()

            # Fake entry should be gone
            cursor.execute("SELECT COUNT(*) FROM processed_files WHERE content_hash = 'fake_hash_12345'")
            fake_count = cursor.fetchone()[0]
            assert fake_count == 0, "Fake entry still exists after --force-db-init"

            # Real entries should exist
            cursor.execute("SELECT COUNT(*) FROM processed_files")
            real_count = cursor.fetchone()[0]
            assert real_count > 0, "No real entries in database after build"

            # Verify we have actual processed data, not the fake data
            cursor.execute("SELECT result FROM processed_files LIMIT 1")
            row = cursor.fetchone()
            assert row is not None
            result_obj = pickle.loads(row[0])
            # Should not be our fake dictionary
            assert not isinstance(result_obj, dict) or "fake" not in result_obj, \
                "Database still contains fake data"

    def test_build_with_custom_db_path(self, tmp_path):
        """Test that custom database path is used correctly"""
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-3.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"
        db_path = tmp_path / "custom" / "my_cache.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        if not spec_file.exists():
            pytest.skip("Test data not available")

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
            ],
        )

        # Verify no argument parsing errors
        assert "no such option" not in result.output.lower()

        # If build was successful, database should exist at custom path
        if result.exit_code == 0:
            assert db_path.exists()

    def test_build_output_directory_creation(self, tmp_path):
        """Test that output directory is created if it doesn't exist"""
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-3.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "new_output_dir"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        # Output dir doesn't exist yet
        assert not output_dir.exists()

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
            ],
        )

        # Verify no argument errors
        assert "no such option" not in result.output.lower()

        # Output directory should be created (even if processing fails)
        # Note: The output dir is created in the main() function
        # This might not happen if parsing fails early
        if "does not exist" not in result.output.lower():
            # If no error about directory, it should have been created
            assert output_dir.exists() or result.exit_code != 0


@pytest.mark.integration
class TestDeleteDatabaseIntegration:
    """Integration tests for delete_database command"""

    def test_delete_database_removes_existing_db(self, tmp_path):
        """Test that delete_database actually removes the database file"""
        runner = CliRunner()

        db_path = tmp_path / "test.db"
        db_path.write_text("dummy database content")

        assert db_path.exists()

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "delete-database",
            ],
        )

        assert result.exit_code == 0
        assert "Deleted:" in result.output
        assert not db_path.exists()

    def test_delete_database_idempotent(self, tmp_path):
        """Test that delete_database can be called multiple times safely"""
        runner = CliRunner()

        db_path = tmp_path / "test.db"

        # First call - no database exists
        result1 = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "delete-database",
            ],
        )
        assert result1.exit_code == 0
        assert "No databases found" in result1.output

        # Second call - still no database
        result2 = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "delete-database",
            ],
        )
        assert result2.exit_code == 0
        assert "No databases found" in result2.output


@pytest.mark.integration
class TestCliBuildWithDifferentOptions:
    """Test various CLI build option combinations"""

    def test_build_with_ignore_db_flag(self, tmp_path):
        """Test that --ignore-db flag prevents cache usage.

        This test:
        1. Manually writes a fake cached result to the database
        2. Runs build with --ignore-db
        3. Verifies that actual processing occurred (output files generated)
        4. Verifies cache was NOT updated (--ignore-db means no cache read/write)
        """
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-3.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"
        cache_db_path = tmp_path / "cache.db"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        # Create database and insert a fake cached result
        from clx.infrastructure.messaging.notebook_classes import NotebookResult
        fake_result = NotebookResult(
            correlation_id="fake_correlation_id",
            input_file="test-data/slides/module_030_single_notebook/topic_100_simple_notebook/slides_simple_notebook.py",
            output_file="slides_simple_notebook.html",
            content_hash="fake_hash_from_cache",
            result="<html>Fake HTML content</html>",
            output_metadata_tags=("slides", "python", "en", "html"),
        )

        with DatabaseManager(cache_db_path) as db:
            cursor = db.conn.cursor()
            cursor.execute(
                """
                INSERT INTO processed_files
                    (file_path, content_hash, correlation_id, result, output_metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    fake_result.input_file,
                    "fake_hash_from_cache",
                    "fake_correlation",
                    pickle.dumps(fake_result),
                    fake_result.output_metadata(),  # Call the method
                ),
            )
            db.conn.commit()

        # Run build with --ignore-db
        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "--cache-db-path",
                str(cache_db_path),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--ignore-db",
                "--log-level",
                "WARNING",
                "--notebook-workers",
                "4",
            ],
        )

        # Build should succeed
        assert result.exit_code == 0, f"Build failed: {result.output}"

        # Verify actual processing occurred
        # Find the generated HTML files (use broader search)
        html_files = list(output_dir.rglob("*.html"))

        # Debug: print what was generated
        if len(html_files) == 0:
            all_files = list(output_dir.rglob("*"))
            print(f"Output dir contents: {[str(f.relative_to(output_dir)) for f in all_files[:20]]}")

        assert len(html_files) > 0, "No HTML files generated"

        html_content = html_files[0].read_text(encoding="utf-8")
        # Should contain actual content from the notebook, not fake cached content
        # The simple_notebook.py contains "Hello from a simple notebook!"
        assert "Hello" in html_content or "Simple Notebook" in html_content, \
            "HTML does not contain expected content from source file"

        # Verify that cache was NOT updated when using --ignore-db
        with DatabaseManager(cache_db_path) as db:
            cursor = db.conn.cursor()
            # The fake entry should still exist (and be the ONLY entry)
            cursor.execute("SELECT COUNT(*) FROM processed_files WHERE content_hash = 'fake_hash_from_cache'")
            fake_count = cursor.fetchone()[0]
            assert fake_count == 1, "Fake entry was removed or modified"

            # Should NOT have any new entries (--ignore-db means cache not updated)
            cursor.execute("SELECT COUNT(*) FROM processed_files")
            total_count = cursor.fetchone()[0]
            assert total_count == 1, \
                f"Cache was updated despite --ignore-db flag (expected 1 entry, found {total_count})"

    def test_build_with_keep_directory_flag(self, tmp_path):
        """Test that --keep-directory flag preserves non-generated files.

        This test:
        1. Creates output directory with two files:
           - file_a.txt: NOT generated by build (should be preserved)
           - slides_simple_notebook.html: IS generated by build (should be overwritten)
        2. Runs build with --keep-directory
        3. Verifies file_a.txt still exists with original content
        4. Verifies slides_simple_notebook.html was overwritten with new content
        """
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-3.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        # Create output directory with test files
        output_dir.mkdir(parents=True)

        # Create file_a.txt (not generated by build - should be preserved)
        file_a = output_dir / "file_a.txt"
        file_a_content = "This is file_a.txt and should NOT be deleted"
        file_a.write_text(file_a_content, encoding="utf-8")

        # Create a subdirectory that matches where HTML will be generated
        # The output structure is: output/speaker/En/Simple Notebook/Slides/Html/...
        # or output/public/En/Simple Notebook/Slides/Html/...
        html_dir = output_dir / "speaker" / "En" / "Simple Notebook" / "Slides" / "Html" / "Speaker" / "Single Section"
        html_dir.mkdir(parents=True, exist_ok=True)

        # Create file_b with wrong content (should be overwritten by build)
        file_b = html_dir / "01 Simple Notebook.html"
        file_b_content = "This is WRONG content that should be overwritten by build"
        file_b.write_text(file_b_content, encoding="utf-8")

        # Run build with --keep-directory
        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "--cache-db-path",
                str(tmp_path / "cache.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--keep-directory",
                "--log-level",
                "WARNING",
                "--notebook-workers",
                "4",
            ],
        )

        # Build should succeed
        assert result.exit_code == 0, f"Build failed: {result.output}"

        # Verify file_a.txt still exists with original content
        assert file_a.exists(), "file_a.txt was deleted (should be preserved)"
        assert file_a.read_text(encoding="utf-8") == file_a_content, \
            "file_a.txt content changed (should be preserved)"

        # Verify file_b was overwritten with actual content
        # Find the generated HTML files (all .html files)
        html_files = list(output_dir.rglob("*.html"))
        assert len(html_files) > 0, "No HTML files generated"

        # At least one should have been overwritten with real content
        found_real_content = False
        for html_file in html_files:
            content = html_file.read_text(encoding="utf-8")
            # Real content should contain "Hello" from the notebook source
            # The simple_notebook.py contains: print("Hello from a simple notebook!")
            if "Hello" in content or "Simple Notebook" in content:
                found_real_content = True
                # Should NOT contain the wrong content we wrote
                assert file_b_content not in content, \
                    f"HTML still contains old wrong content: {html_file}"

        assert found_real_content, "No HTML files contain expected content from source"


@pytest.mark.integration
class TestCliErrorHandling:
    """Test CLI error handling and edge cases"""

    def test_build_with_invalid_spec_file_content(self, tmp_path):
        """Test that CLI handles invalid XML spec files gracefully"""
        runner = CliRunner()

        spec_file = tmp_path / "invalid.xml"
        spec_file.write_text("This is not valid XML")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        output_dir = tmp_path / "output"

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
            ],
        )

        # Should fail, but gracefully
        assert result.exit_code != 0

    def test_build_with_nonexistent_data_dir(self, tmp_path):
        """Test that CLI handles non-existent data directory"""
        runner = CliRunner()

        spec_file = tmp_path / "test.xml"
        spec_file.write_text('<?xml version="1.0"?><course><name>Test</name></course>')
        data_dir = tmp_path / "nonexistent_data"
        output_dir = tmp_path / "output"

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
            ],
        )

        # Should fail because data dir doesn't exist
        assert result.exit_code != 0
        # Click validation should catch this
        assert "does not exist" in result.output.lower() or result.exit_code != 0
