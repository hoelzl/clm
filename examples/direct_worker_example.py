#!/usr/bin/env python3
"""Example script demonstrating direct worker execution.

This script shows how to run workers directly as subprocesses without Docker.
This is useful for:
- Development and testing
- Environments without Docker
- Simpler use cases where container isolation isn't needed
"""

import sys
import time
import logging
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "clx-common" / "src"))

from clx_common.database.schema import init_database
from clx_common.database.job_queue import JobQueue
from clx_common.workers.pool_manager import WorkerPoolManager
from clx_common.workers.worker_executor import WorkerConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def example_direct_workers():
    """Example: Running workers in direct (subprocess) mode."""
    logger.info("=" * 60)
    logger.info("Example 1: Direct Worker Execution")
    logger.info("=" * 60)

    # Setup paths
    db_path = Path("./example_direct.db")
    workspace_path = Path("./workspace")
    workspace_path.mkdir(exist_ok=True)

    # Initialize database
    if not db_path.exists():
        logger.info(f"Initializing database at {db_path}")
        init_database(db_path)

    # Configure workers to run directly (no Docker)
    worker_configs = [
        WorkerConfig(
            worker_type='notebook',
            count=2,
            execution_mode='direct'  # Run as subprocess
        ),
        WorkerConfig(
            worker_type='drawio',
            count=1,
            execution_mode='direct'  # Run as subprocess
        ),
        WorkerConfig(
            worker_type='plantuml',
            count=1,
            execution_mode='direct'  # Run as subprocess
        )
    ]

    logger.info("\nStarting workers in direct mode...")
    logger.info("Note: Workers will run as Python subprocesses")
    logger.info(f"  - 2 notebook workers")
    logger.info(f"  - 1 drawio worker")
    logger.info(f"  - 1 plantuml worker")

    # Create pool manager
    manager = WorkerPoolManager(
        db_path=db_path,
        workspace_path=workspace_path,
        worker_configs=worker_configs,
        log_level='INFO'
    )

    try:
        # Start worker pools
        manager.start_pools()
        logger.info("\nWorkers started successfully!")

        # Give workers time to register
        time.sleep(2)

        # Check worker status
        stats = manager.get_worker_stats()
        logger.info("\nWorker Status:")
        for worker_type, status_counts in stats.items():
            logger.info(f"  {worker_type}:")
            for status, count in status_counts.items():
                logger.info(f"    {status}: {count}")

        # Start health monitoring
        logger.info("\nStarting health monitoring...")
        manager.start_monitoring(check_interval=5)

        # Keep running for a while
        logger.info("\nWorkers are running. Press Ctrl+C to stop.")
        logger.info("You can now add jobs to the queue and they will be processed.")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("\n\nShutting down gracefully...")
    finally:
        manager.stop_pools()
        logger.info("All workers stopped.")


def example_mixed_mode():
    """Example: Running some workers in Docker and some directly."""
    logger.info("\n")
    logger.info("=" * 60)
    logger.info("Example 2: Mixed Mode (Docker + Direct)")
    logger.info("=" * 60)

    # Setup paths
    db_path = Path("./example_mixed.db")
    workspace_path = Path("./workspace")
    workspace_path.mkdir(exist_ok=True)

    # Initialize database
    if not db_path.exists():
        logger.info(f"Initializing database at {db_path}")
        init_database(db_path)

    # Configure mixed mode: some Docker, some direct
    worker_configs = [
        WorkerConfig(
            worker_type='notebook',
            count=1,
            execution_mode='docker',  # Run in Docker
            image='notebook-processor:0.2.2',
            memory_limit='1g'
        ),
        WorkerConfig(
            worker_type='drawio',
            count=1,
            execution_mode='direct'  # Run as subprocess
        )
    ]

    logger.info("\nStarting workers in mixed mode...")
    logger.info("  - 1 notebook worker (Docker)")
    logger.info("  - 1 drawio worker (Direct)")

    manager = WorkerPoolManager(
        db_path=db_path,
        workspace_path=workspace_path,
        worker_configs=worker_configs
    )

    try:
        manager.start_pools()
        logger.info("\nWorkers started successfully!")

        # Give workers time to register
        time.sleep(2)

        # Check worker status
        stats = manager.get_worker_stats()
        logger.info("\nWorker Status:")
        for worker_type, status_counts in stats.items():
            logger.info(f"  {worker_type}:")
            for status, count in status_counts.items():
                logger.info(f"    {status}: {count}")

        logger.info("\nPress Ctrl+C to stop.")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("\n\nShutting down...")
    finally:
        manager.stop_pools()
        logger.info("All workers stopped.")


def example_with_jobs():
    """Example: Running direct workers and processing jobs."""
    logger.info("\n")
    logger.info("=" * 60)
    logger.info("Example 3: Direct Workers Processing Jobs")
    logger.info("=" * 60)

    # Setup paths
    db_path = Path("./example_jobs.db")
    workspace_path = Path("./workspace")
    workspace_path.mkdir(exist_ok=True)

    # Initialize database
    if not db_path.exists():
        logger.info(f"Initializing database at {db_path}")
        init_database(db_path)

    # Create a simple test notebook
    import json
    test_notebook = workspace_path / "test.ipynb"
    notebook_content = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["print('Hello from direct worker!')"]
            }
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3"
            }
        },
        "nbformat": 4,
        "nbformat_minor": 4
    }

    with open(test_notebook, 'w') as f:
        json.dump(notebook_content, f)

    logger.info(f"Created test notebook: {test_notebook}")

    # Add job to queue
    job_queue = JobQueue(db_path)
    output_file = workspace_path / "output.ipynb"

    job_id = job_queue.add_job(
        job_type='notebook',
        input_file=str(test_notebook),
        output_file=str(output_file),
        payload={'kernel': 'python3', 'timeout': 60}
    )

    logger.info(f"Added job to queue (ID: {job_id})")

    # Start worker
    worker_configs = [
        WorkerConfig(
            worker_type='notebook',
            count=1,
            execution_mode='direct'
        )
    ]

    manager = WorkerPoolManager(
        db_path=db_path,
        workspace_path=workspace_path,
        worker_configs=worker_configs
    )

    try:
        logger.info("\nStarting direct worker...")
        manager.start_pools()

        # Wait for job to be processed
        logger.info("Waiting for job to be processed...")
        max_wait = 30
        start_time = time.time()

        while time.time() - start_time < max_wait:
            conn = job_queue._get_conn()
            cursor = conn.execute(
                "SELECT status FROM jobs WHERE id = ?",
                (job_id,)
            )
            row = cursor.fetchone()
            if row:
                status = row[0]
                if status == 'completed':
                    logger.info(f"\n✓ Job completed successfully!")
                    logger.info(f"Output file: {output_file}")
                    if output_file.exists():
                        logger.info(f"Output file size: {output_file.stat().st_size} bytes")
                    break
                elif status == 'failed':
                    logger.error(f"\n✗ Job failed!")
                    cursor = conn.execute(
                        "SELECT error FROM jobs WHERE id = ?",
                        (job_id,)
                    )
                    error = cursor.fetchone()[0]
                    logger.error(f"Error: {error}")
                    break

            time.sleep(0.5)
        else:
            logger.warning(f"\nJob did not complete within {max_wait}s")

    finally:
        manager.stop_pools()
        logger.info("Worker stopped.")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Direct Worker Execution Examples")
    print("=" * 60)
    print("\nThese examples demonstrate how to run workers without Docker:")
    print("  1. Direct workers only")
    print("  2. Mixed mode (Docker + Direct)")
    print("  3. Processing jobs with direct workers")
    print("\nChoose an example (1-3) or press Ctrl+C to exit:")

    try:
        choice = input("> ").strip()

        if choice == "1":
            example_direct_workers()
        elif choice == "2":
            example_mixed_mode()
        elif choice == "3":
            example_with_jobs()
        else:
            print("Invalid choice. Please select 1, 2, or 3.")

    except KeyboardInterrupt:
        print("\n\nExiting.")
        sys.exit(0)
