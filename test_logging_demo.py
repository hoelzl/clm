#!/usr/bin/env python
"""Demonstration script for the new e2e logging system.

This script demonstrates the logging features:
1. Job submission logging with input file names
2. Progress tracking with periodic updates
3. Job completion/failure logging
4. Long-running job warnings
"""

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-8s %(name)s - %(message)s',
    datefmt='%H:%M:%S'
)

# Configure e2e logging settings
os.environ['CLX_E2E_PROGRESS_INTERVAL'] = '2'  # Report every 2 seconds
os.environ['CLX_E2E_LONG_JOB_THRESHOLD'] = '5'  # Warn after 5 seconds
os.environ['CLX_E2E_SHOW_WORKER_DETAILS'] = 'true'

from clx_common.database.schema import init_database
from clx_common.database.job_queue import JobQueue
from clx_faststream_backend.sqlite_backend import SqliteBackend
from clx_common.messaging.base_classes import Payload
from pydantic import BaseModel


class DemoPayload(Payload):
    """Demo payload for testing."""
    data: str = ""
    input_file_name: str = "demo.txt"
    correlation_id: str = "demo-001"

    def content_hash(self) -> str:
        return "demo-hash"

    def output_metadata(self) -> dict:
        return {}


class DemoOperation:
    """Demo operation for testing."""

    def __init__(self, name: str):
        self.name = name
        self.service_name = "notebook-processor"


async def main():
    """Run the logging demonstration."""
    print("=" * 80)
    print("E2E Test Logging Demonstration")
    print("=" * 80)
    print()
    print("This demo shows the following logging features:")
    print("  1. Job submission with input file names")
    print("  2. Progress tracking with periodic updates")
    print("  3. ProgressTracker warnings for long-running jobs")
    print("  4. Final summary statistics")
    print()
    print("=" * 80)
    print()

    # Create temporary database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "demo.db"
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()

        # Initialize database
        init_database(db_path)

        # Create backend with progress tracking enabled
        backend = SqliteBackend(
            db_path=db_path,
            workspace_path=workspace,
            enable_progress_tracking=True
        )

        # Create some demo jobs
        print("Submitting demo jobs...")
        print()

        demo_files = [
            "lecture_001_introduction.ipynb",
            "lecture_002_basics.ipynb",
            "lecture_003_advanced.ipynb",
            "diagram_001.drawio",
            "diagram_002.drawio",
        ]

        for i, filename in enumerate(demo_files):
            # Create demo input/output files
            input_file = workspace / filename
            output_file = workspace / f"output_{filename}"
            input_file.write_text(f"Demo content for {filename}")

            # Create payload
            payload = DemoPayload(
                input_file=str(input_file),
                input_file_name=filename,
                output_file=str(output_file),
                correlation_id=f"demo-{i:03d}"
            )

            # Create operation
            operation = DemoOperation(filename)

            # Submit job
            await backend.execute_operation(operation, payload)

        print()
        print("All jobs submitted!")
        print()
        print("=" * 80)
        print("Waiting for job completion (simulated)...")
        print("=" * 80)
        print()
        print("NOTE: In a real scenario, workers would process these jobs.")
        print("      This demo just shows the submission and progress tracking logs.")
        print()

        # Start progress tracker to see periodic updates
        if backend.progress_tracker:
            backend.progress_tracker.start_progress_logging()

            # Simulate some time passing to see progress updates
            print("Simulating job processing (watching for 8 seconds)...")
            print()
            await asyncio.sleep(8)

            # Stop progress tracker
            backend.progress_tracker.stop_progress_logging()
            print()
            print("=" * 80)
            print("Final Summary:")
            print("=" * 80)
            print()
            backend.progress_tracker.log_summary()

        print()
        print("=" * 80)
        print("Demo Complete!")
        print("=" * 80)
        print()
        print("In actual e2e tests, you would see:")
        print("  - Worker logs when picking up jobs")
        print("  - Job processing progress")
        print("  - Completion notifications with duration")
        print("  - Warnings for jobs taking longer than expected")
        print()


if __name__ == "__main__":
    asyncio.run(main())
