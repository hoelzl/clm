import logging
import pickle
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from clx.infrastructure.messaging.base_classes import Result

if TYPE_CHECKING:
    from clx.cli.build_data_classes import BuildError, BuildWarning

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_path, force_init=False):
        self.db_path = Path(db_path)
        self.conn = None
        self.force_init = force_init

    def __enter__(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.init_db(force=self.force_init)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()

    def init_db(self, force=False):
        cursor = self.conn.cursor()

        if force:
            cursor.execute("DROP TABLE IF EXISTS processed_files")
            cursor.execute("DROP TABLE IF EXISTS processing_issues")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT,
                content_hash TEXT,
                correlation_id TEXT,
                result BLOB,
                output_metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

        # Table for storing errors and warnings associated with processed files
        # This allows us to report errors even when using cached results
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processing_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                output_metadata TEXT NOT NULL,
                issue_type TEXT NOT NULL,
                issue_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

        # Create index for faster lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_processing_issues_lookup
            ON processing_issues (file_path, content_hash, output_metadata)
            """)

        self.conn.commit()

    def store_result(self, file_path: str, content_hash: str, correlation_id: str, result: Result):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO processed_files
                (file_path, content_hash, correlation_id, result, output_metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(file_path),
                content_hash,
                correlation_id,
                pickle.dumps(result),
                result.output_metadata(),
            ),
        )
        self.conn.commit()

    def store_latest_result(
        self,
        file_path: str,
        content_hash: str,
        correlation_id: str,
        result: Result,
        retain_count: int | None = 0,
    ):
        cursor = self.conn.cursor()

        # Insert the new result
        cursor.execute(
            """
            INSERT INTO processed_files
                (file_path, content_hash, correlation_id, result, output_metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(file_path),
                content_hash,
                correlation_id,
                pickle.dumps(result),
                result.output_metadata(),
            ),
        )

        # Delete old entries, keeping the specified number of recent entries for each output_metadata
        if retain_count is not None:
            cursor.execute(
                """
                DELETE FROM processed_files
                WHERE file_path = ? AND output_metadata = ? AND id NOT IN (
                    SELECT id FROM processed_files
                    WHERE file_path = ? AND output_metadata = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                )
                """,
                (
                    str(file_path),
                    result.output_metadata(),
                    str(file_path),
                    result.output_metadata(),
                    retain_count + 1,
                ),
            )

        self.conn.commit()

    def get_result(self, file_path: str, content_hash: str, output_metadata: str) -> Result:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT result FROM processed_files
            WHERE file_path = ? AND content_hash = ? AND output_metadata = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(file_path), content_hash, output_metadata),
        )
        db_result = cursor.fetchone()
        return pickle.loads(db_result[0]) if db_result else None

    def remove_old_entries(self, file_path: str):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            DELETE FROM processed_files
            WHERE file_path = ? AND id NOT IN (
                SELECT id FROM processed_files
                WHERE file_path = ?
                GROUP BY output_metadata
                HAVING id = MAX(id)
            )
            """,
            (str(file_path), str(file_path)),
        )
        self.conn.commit()

    def get_newest_entry(self, file_path: str, output_metadata: str) -> Result:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT result FROM processed_files
            WHERE file_path = ? AND output_metadata = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(file_path), output_metadata),
        )
        db_result = cursor.fetchone()
        return pickle.loads(db_result[0]) if db_result else None

    def store_error(
        self,
        file_path: str,
        content_hash: str,
        output_metadata: str,
        error: "BuildError",
    ) -> None:
        """Store an error for a processed file.

        Args:
            file_path: Path to the source file
            content_hash: Hash of the file content
            output_metadata: Output metadata string for the processing
            error: BuildError object to store
        """
        cursor = self.conn.cursor()

        # Remove existing errors for this file/hash/metadata combo
        cursor.execute(
            """
            DELETE FROM processing_issues
            WHERE file_path = ? AND content_hash = ? AND output_metadata = ?
            AND issue_type = 'error'
            """,
            (str(file_path), content_hash, output_metadata),
        )

        # Store the new error
        cursor.execute(
            """
            INSERT INTO processing_issues
                (file_path, content_hash, output_metadata, issue_type, issue_json)
            VALUES (?, ?, ?, 'error', ?)
            """,
            (str(file_path), content_hash, output_metadata, error.to_json()),
        )
        self.conn.commit()
        logger.debug(f"Stored error for {file_path} with output_metadata={output_metadata}")

    def store_warning(
        self,
        file_path: str,
        content_hash: str,
        output_metadata: str,
        warning: "BuildWarning",
    ) -> None:
        """Store a warning for a processed file.

        Args:
            file_path: Path to the source file
            content_hash: Hash of the file content
            output_metadata: Output metadata string for the processing
            warning: BuildWarning object to store
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO processing_issues
                (file_path, content_hash, output_metadata, issue_type, issue_json)
            VALUES (?, ?, ?, 'warning', ?)
            """,
            (str(file_path), content_hash, output_metadata, warning.to_json()),
        )
        self.conn.commit()
        logger.debug(f"Stored warning for {file_path} with output_metadata={output_metadata}")

    def get_issues(
        self,
        file_path: str,
        content_hash: str,
        output_metadata: str,
    ) -> tuple[list["BuildError"], list["BuildWarning"]]:
        """Retrieve stored errors and warnings for a processed file.

        Args:
            file_path: Path to the source file
            content_hash: Hash of the file content
            output_metadata: Output metadata string for the processing

        Returns:
            Tuple of (errors list, warnings list)
        """
        from clx.cli.build_data_classes import BuildError, BuildWarning

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT issue_type, issue_json FROM processing_issues
            WHERE file_path = ? AND content_hash = ? AND output_metadata = ?
            ORDER BY created_at DESC
            """,
            (str(file_path), content_hash, output_metadata),
        )

        errors = []
        warnings = []

        for row in cursor.fetchall():
            issue_type, issue_json = row
            try:
                if issue_type == "error":
                    errors.append(BuildError.from_json(issue_json))
                elif issue_type == "warning":
                    warnings.append(BuildWarning.from_json(issue_json))
            except Exception as e:
                logger.warning(f"Failed to deserialize issue: {e}")

        return errors, warnings

    def clear_issues(
        self,
        file_path: str,
        content_hash: str,
        output_metadata: str,
    ) -> None:
        """Clear all stored issues for a processed file.

        Args:
            file_path: Path to the source file
            content_hash: Hash of the file content
            output_metadata: Output metadata string for the processing
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            DELETE FROM processing_issues
            WHERE file_path = ? AND content_hash = ? AND output_metadata = ?
            """,
            (str(file_path), content_hash, output_metadata),
        )
        self.conn.commit()
