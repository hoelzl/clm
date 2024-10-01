import sqlite3
import hashlib
from pathlib import Path


class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.init_db()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()

    def init_db(self):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT,
                content_hash TEXT,
                metadata TEXT,
                result BLOB
            )
            """
        )
        self.conn.commit()

    def store_result(
        self, file_path: str, content_hash: str, metadata: str, result: bytes
    ):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO processed_files (file_path, content_hash, metadata, result)
            VALUES (?, ?, ?, ?)
            """,
            (str(file_path), content_hash, metadata, result),
        )
        self.conn.commit()

    def get_result(self, file_path: str, content_hash: str) -> bytes:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT result FROM processed_files
            WHERE file_path = ? AND content_hash = ?
            """,
            (str(file_path), content_hash),
        )
        result = cursor.fetchone()
        return result[0] if result else None
