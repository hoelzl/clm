"""Worker state management for persistent workers."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class WorkerInfo(BaseModel):
    """Information about a worker."""

    worker_type: str
    execution_mode: str
    executor_id: str  # Container ID or direct-worker-id
    db_worker_id: int
    started_at: str
    config: dict[str, Any]


class WorkerState(BaseModel):
    """Persistent worker state."""

    version: str = "1.0"
    db_path: str
    workers: list[WorkerInfo]
    metadata: dict[str, Any]


class WorkerStateManager:
    """Manage persistent worker state.

    This class handles saving and loading worker state to/from disk, enabling
    persistent workers that can be started with 'clx start-services' and stopped
    with 'clx stop-services'.
    """

    def __init__(self, state_file: Path | None = None):
        """Initialize state manager.

        Args:
            state_file: Path to state file. Defaults to .clx/worker-state.json
        """
        if state_file is None:
            state_file = Path(".clx") / "worker-state.json"

        self.state_file = state_file

    def save_worker_state(self, workers: list[WorkerInfo], db_path: Path, **metadata) -> None:
        """Save worker state to disk.

        The state file is written atomically to prevent corruption.

        Args:
            workers: List of worker information
            db_path: Path to database
            **metadata: Additional metadata to store
        """
        # Ensure directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Build state
        state = WorkerState(
            db_path=str(db_path.absolute()),
            workers=workers,
            metadata={
                "created_at": datetime.now().isoformat(),
                "created_by": "clx start-services",
                **metadata,
            },
        )

        # Write atomically
        temp_file = self.state_file.with_suffix(".tmp")
        try:
            with temp_file.open("w") as f:
                f.write(state.model_dump_json(indent=2))

            # Set restrictive permissions (user rw only)
            temp_file.chmod(0o600)

            # Atomic rename
            temp_file.replace(self.state_file)

            logger.info(f"Saved worker state to {self.state_file}")

        except Exception as e:
            logger.error(f"Failed to save worker state: {e}")
            if temp_file.exists():
                temp_file.unlink()
            raise

    def load_worker_state(self) -> WorkerState | None:
        """Load worker state from disk.

        Returns:
            WorkerState if file exists and is valid, None otherwise
        """
        if not self.state_file.exists():
            logger.debug(f"State file does not exist: {self.state_file}")
            return None

        try:
            with self.state_file.open("r") as f:
                data = json.load(f)

            state = WorkerState(**data)
            logger.debug(f"Loaded worker state from {self.state_file}")
            return state

        except Exception as e:
            logger.error(f"Failed to load worker state: {e}")
            return None

    def clear_worker_state(self) -> None:
        """Clear worker state file."""
        if self.state_file.exists():
            try:
                self.state_file.unlink()
                logger.info(f"Cleared worker state: {self.state_file}")
            except Exception as e:
                logger.error(f"Failed to clear worker state: {e}")

    def validate_state(self, db_path: Path) -> bool:
        """Validate that state file matches expected database.

        Args:
            db_path: Expected database path

        Returns:
            True if state is valid and matches db_path
        """
        state = self.load_worker_state()
        if not state:
            return False

        # Check database path matches
        if state.db_path != str(db_path.absolute()):
            logger.warning(f"Database path mismatch: state={state.db_path}, expected={db_path}")
            return False

        return True
