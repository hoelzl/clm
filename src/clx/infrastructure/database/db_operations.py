import pickle
import sqlite3
from pathlib import Path

from clx.infrastructure.messaging.base_classes import Result


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
        self.conn.commit()

    def store_result(
        self, file_path: str, content_hash: str, correlation_id: str, result: Result
    ):
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

    def get_result(
        self, file_path: str, content_hash: str, output_metadata: str
    ) -> Result:
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
