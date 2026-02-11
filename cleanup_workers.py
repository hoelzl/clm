#!/usr/bin/env python3
"""Cleanup script to remove stale worker records from the database.

This script removes all worker records that are no longer running.
Run this before starting the pool manager if you have stale workers.
"""

import sqlite3
from pathlib import Path


def cleanup_workers(db_path: Path):
    """Remove all stale worker records from the database.

    Args:
        db_path: Path to the SQLite database
    """
    if not db_path.exists():
        print(f"Database not found at: {db_path}")
        print("No cleanup needed.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get count of existing workers
    cursor.execute("SELECT COUNT(*) FROM workers")
    total_workers = cursor.fetchone()[0]

    if total_workers == 0:
        print("No workers found in database.")
        print("✓ Database is clean!")
        return

    # Show existing workers
    print(f"Found {total_workers} worker record(s) in database:")
    cursor.execute("""
        SELECT id, worker_type, status, last_heartbeat
        FROM workers
        ORDER BY id
    """)

    for row in cursor.fetchall():
        worker_id, worker_type, status, last_heartbeat = row
        print(f"  - Worker {worker_id}: {worker_type} ({status}, last heartbeat: {last_heartbeat})")

    # Delete all workers
    print("\nRemoving all worker records...")
    cursor.execute("DELETE FROM workers")
    deleted_count = cursor.rowcount
    conn.commit()

    print(f"✓ Removed {deleted_count} worker record(s)")
    print("✓ Database cleanup complete!")

    conn.close()


if __name__ == "__main__":
    import os

    # Get database path from environment or use default
    db_path_str = os.getenv("CLM_DB_PATH", "clm_jobs.db")
    db_path = Path(db_path_str)

    print(f"Cleaning up workers in: {db_path}")
    print("-" * 60)

    cleanup_workers(db_path)
