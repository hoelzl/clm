# CLI Status Command Design

**Version**: 1.0
**Date**: 2025-11-15
**Purpose**: Technical design for implementing `clx status` command for snapshot system status

## Overview

This document provides the detailed technical design for implementing the `clx status` CLI command, which provides a quick snapshot of the CLX system state including worker availability, job queue status, and system health indicators.

### Design Goals

1. **Fast Execution**: < 1 second response time for typical status checks
2. **Clear Output**: Human-readable by default, machine-parseable when needed
3. **Existing Infrastructure**: Leverage existing JobQueue and statistics APIs
4. **Minimal Dependencies**: Use existing CLI framework (Click) and optional Rich for formatting
5. **Actionable Information**: Provide enough detail for users to make decisions

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────┐
│                  clx status                         │
│                (CLI Command)                        │
└────────────┬────────────────────────────────────────┘
             │
             ├──> StatusCommand (main.py)
             │    ├── Parse arguments
             │    ├── Initialize StatusCollector
             │    └── Format and display output
             │
             ├──> StatusCollector (status_collector.py)
             │    ├── Collect worker stats
             │    ├── Collect queue stats
             │    ├── Collect database info
             │    └── Determine health status
             │
             ├──> StatusFormatter (status_formatter.py)
             │    ├── TableFormatter (default)
             │    ├── JsonFormatter
             │    └── CompactFormatter
             │
             └──> JobQueue (existing)
                  ├── get_worker_stats()
                  ├── get_queue_statistics()
                  └── get_jobs_by_status()
```

## Module Structure

### File Layout

```
src/clx/cli/
├── main.py                      # Main CLI with status command
├── commands/
│   └── status.py                # Status command implementation
├── status/
│   ├── __init__.py
│   ├── collector.py             # StatusCollector class
│   ├── formatter.py             # Base Formatter class
│   ├── formatters/
│   │   ├── __init__.py
│   │   ├── table_formatter.py   # Rich table output
│   │   ├── json_formatter.py    # JSON output
│   │   └── compact_formatter.py # Compact text output
│   └── models.py                # Data models for status info
```

## Data Models

### StatusInfo Dataclass

```python
"""Data models for status information."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum


class SystemHealth(Enum):
    """Overall system health status."""
    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR = "error"


class WorkerStatus(Enum):
    """Worker status."""
    IDLE = "idle"
    BUSY = "busy"
    HUNG = "hung"
    DEAD = "dead"


@dataclass
class DatabaseInfo:
    """Database connectivity and metadata."""
    path: str
    accessible: bool
    exists: bool
    size_bytes: Optional[int] = None
    last_modified: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class BusyWorkerInfo:
    """Information about a busy worker."""
    worker_id: str
    job_id: str
    document_path: str
    elapsed_seconds: int


@dataclass
class WorkerTypeStats:
    """Statistics for a specific worker type."""
    worker_type: str  # notebook, plantuml, drawio
    execution_mode: Optional[str]  # direct, docker, or mixed
    total: int
    idle: int
    busy: int
    hung: int
    dead: int
    busy_workers: List[BusyWorkerInfo] = field(default_factory=list)


@dataclass
class QueueStats:
    """Job queue statistics."""
    pending: int
    processing: int
    completed_last_hour: int
    failed_last_hour: int
    oldest_pending_seconds: Optional[int] = None


@dataclass
class StatusInfo:
    """Complete system status information."""
    timestamp: datetime
    health: SystemHealth
    database: DatabaseInfo
    workers: Dict[str, WorkerTypeStats]  # key: worker_type
    queue: QueueStats
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
```

## Core Classes

### 1. StatusCollector

```python
"""Collect system status information from database."""

import os
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict

from clx.infrastructure.database.job_queue import JobQueue
from clx.cli.status.models import (
    StatusInfo,
    DatabaseInfo,
    WorkerTypeStats,
    BusyWorkerInfo,
    QueueStats,
    SystemHealth,
    WorkerStatus,
)

logger = logging.getLogger(__name__)


class StatusCollector:
    """Collect system status from database."""

    HUNG_WORKER_THRESHOLD_SECONDS = 300  # 5 minutes
    STALE_DATA_THRESHOLD_SECONDS = 60    # 1 minute
    LONG_QUEUE_THRESHOLD = 10
    OLD_PENDING_JOB_THRESHOLD_SECONDS = 300  # 5 minutes

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize status collector.

        Args:
            db_path: Path to database. If None, use default location.
        """
        self.db_path = db_path or self._get_default_db_path()
        self.job_queue: Optional[JobQueue] = None

    def _get_default_db_path(self) -> Path:
        """Get default database path from environment or config."""
        # Check environment variable
        db_path = os.getenv("CLX_DB_PATH")
        if db_path:
            return Path(db_path)

        # Check current directory
        default_paths = [
            Path.cwd() / "clx_jobs.db",
            Path.cwd() / "jobs.db",
            Path.home() / ".clx" / "clx_jobs.db",
        ]

        for path in default_paths:
            if path.exists():
                return path

        # Return default (may not exist yet)
        return Path.cwd() / "clx_jobs.db"

    def collect(self) -> StatusInfo:
        """Collect complete system status.

        Returns:
            StatusInfo with all collected data
        """
        timestamp = datetime.now()

        # Check database
        db_info = self._collect_database_info()

        if not db_info.accessible:
            # Database not accessible - return error state
            return StatusInfo(
                timestamp=timestamp,
                health=SystemHealth.ERROR,
                database=db_info,
                workers={},
                queue=QueueStats(0, 0, 0, 0, None),
                errors=[db_info.error_message or "Database not accessible"],
            )

        # Initialize job queue
        try:
            self.job_queue = JobQueue(self.db_path)
        except Exception as e:
            logger.error(f"Failed to initialize job queue: {e}", exc_info=True)
            return StatusInfo(
                timestamp=timestamp,
                health=SystemHealth.ERROR,
                database=db_info,
                workers={},
                queue=QueueStats(0, 0, 0, 0, None),
                errors=[f"Failed to connect to database: {e}"],
            )

        # Collect worker and queue stats
        workers = self._collect_worker_stats()
        queue = self._collect_queue_stats()

        # Determine health and collect warnings/errors
        health, warnings, errors = self._determine_health(workers, queue, db_info)

        return StatusInfo(
            timestamp=timestamp,
            health=health,
            database=db_info,
            workers=workers,
            queue=queue,
            warnings=warnings,
            errors=errors,
        )

    def _collect_database_info(self) -> DatabaseInfo:
        """Collect database metadata."""
        path_str = str(self.db_path)

        if not self.db_path.exists():
            return DatabaseInfo(
                path=path_str,
                accessible=False,
                exists=False,
                error_message=f"Database not found: {path_str}",
            )

        try:
            stat = self.db_path.stat()
            return DatabaseInfo(
                path=path_str,
                accessible=True,
                exists=True,
                size_bytes=stat.st_size,
                last_modified=datetime.fromtimestamp(stat.st_mtime),
            )
        except Exception as e:
            logger.error(f"Error accessing database: {e}", exc_info=True)
            return DatabaseInfo(
                path=path_str,
                accessible=False,
                exists=True,
                error_message=f"Cannot access database: {e}",
            )

    def _collect_worker_stats(self) -> Dict[str, WorkerTypeStats]:
        """Collect worker statistics by type."""
        if not self.job_queue:
            return {}

        try:
            # Get worker stats from JobQueue
            worker_stats = self.job_queue.get_worker_stats()

            # Transform to our model
            result = {}
            for worker_type in ['notebook', 'plantuml', 'drawio']:
                stats = worker_stats.get(worker_type, {})

                # Get busy workers details
                busy_workers = self._get_busy_workers(worker_type)

                # Determine execution mode
                execution_mode = self._get_worker_execution_mode(worker_type)

                result[worker_type] = WorkerTypeStats(
                    worker_type=worker_type,
                    execution_mode=execution_mode,
                    total=stats.get('total', 0),
                    idle=stats.get('idle', 0),
                    busy=stats.get('busy', 0),
                    hung=stats.get('hung', 0),
                    dead=stats.get('dead', 0),
                    busy_workers=busy_workers,
                )

            return result

        except Exception as e:
            logger.error(f"Error collecting worker stats: {e}", exc_info=True)
            return {}

    def _get_busy_workers(self, worker_type: str) -> List[BusyWorkerInfo]:
        """Get details of busy workers for a type."""
        if not self.job_queue:
            return []

        try:
            conn = self.job_queue._get_conn()
            cursor = conn.execute(
                """
                SELECT
                    w.worker_id,
                    j.job_id,
                    j.document_path,
                    CAST((julianday('now') - julianday(j.started_at)) * 86400 AS INTEGER) as elapsed
                FROM workers w
                JOIN jobs j ON j.worker_id = w.id
                WHERE w.worker_type = ?
                  AND w.status = 'busy'
                  AND j.status = 'processing'
                ORDER BY j.started_at ASC
                """,
                (worker_type,)
            )

            busy_workers = []
            for row in cursor.fetchall():
                busy_workers.append(
                    BusyWorkerInfo(
                        worker_id=row[0],
                        job_id=row[1],
                        document_path=row[2],
                        elapsed_seconds=row[3] or 0,
                    )
                )

            return busy_workers

        except Exception as e:
            logger.error(f"Error getting busy workers: {e}", exc_info=True)
            return []

    def _get_worker_execution_mode(self, worker_type: str) -> Optional[str]:
        """Get execution mode for worker type."""
        if not self.job_queue:
            return None

        try:
            conn = self.job_queue._get_conn()
            cursor = conn.execute(
                """
                SELECT DISTINCT execution_mode
                FROM workers
                WHERE worker_type = ?
                  AND status != 'dead'
                """,
                (worker_type,)
            )

            modes = [row[0] for row in cursor.fetchall() if row[0]]

            if not modes:
                return None
            elif len(modes) == 1:
                return modes[0]
            else:
                return "mixed"

        except Exception:
            return None

    def _collect_queue_stats(self) -> QueueStats:
        """Collect job queue statistics."""
        if not self.job_queue:
            return QueueStats(0, 0, 0, 0, None)

        try:
            stats = self.job_queue.get_queue_statistics()

            # Calculate oldest pending job
            oldest_pending_seconds = None
            if stats.get('pending', 0) > 0:
                conn = self.job_queue._get_conn()
                cursor = conn.execute(
                    """
                    SELECT CAST((julianday('now') - julianday(created_at)) * 86400 AS INTEGER)
                    FROM jobs
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                )
                row = cursor.fetchone()
                if row:
                    oldest_pending_seconds = row[0]

            # Get completed/failed in last hour
            one_hour_ago = datetime.now() - timedelta(hours=1)
            conn = self.job_queue._get_conn()

            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE status = 'completed'
                  AND completed_at > ?
                """,
                (one_hour_ago.isoformat(),)
            )
            completed_last_hour = cursor.fetchone()[0]

            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE status = 'failed'
                  AND completed_at > ?
                """,
                (one_hour_ago.isoformat(),)
            )
            failed_last_hour = cursor.fetchone()[0]

            return QueueStats(
                pending=stats.get('pending', 0),
                processing=stats.get('processing', 0),
                completed_last_hour=completed_last_hour,
                failed_last_hour=failed_last_hour,
                oldest_pending_seconds=oldest_pending_seconds,
            )

        except Exception as e:
            logger.error(f"Error collecting queue stats: {e}", exc_info=True)
            return QueueStats(0, 0, 0, 0, None)

    def _determine_health(
        self,
        workers: Dict[str, WorkerTypeStats],
        queue: QueueStats,
        db_info: DatabaseInfo,
    ) -> tuple[SystemHealth, list[str], list[str]]:
        """Determine system health and collect warnings/errors.

        Returns:
            Tuple of (health, warnings, errors)
        """
        warnings = []
        errors = []

        # Check database
        if not db_info.accessible:
            errors.append("Database not accessible")
            return SystemHealth.ERROR, warnings, errors

        # Check for workers
        total_workers = sum(stats.total for stats in workers.values())
        if total_workers == 0:
            errors.append("No workers registered")
            return SystemHealth.ERROR, warnings, errors

        # Check for hung workers
        hung_workers = sum(stats.hung for stats in workers.values())
        if hung_workers > 0:
            warnings.append(f"{hung_workers} worker(s) hung (processing > 5 minutes)")

        # Check for dead workers
        dead_workers = sum(stats.dead for stats in workers.values())
        if dead_workers > 0:
            warnings.append(f"{dead_workers} worker(s) dead (no heartbeat)")

        # Check queue
        if queue.pending > self.LONG_QUEUE_THRESHOLD:
            idle_workers = sum(stats.idle for stats in workers.values())
            if idle_workers == 0:
                warnings.append(
                    f"{queue.pending} jobs pending with no idle workers available"
                )
            else:
                warnings.append(f"{queue.pending} jobs pending")

        # Check oldest pending job
        if queue.oldest_pending_seconds and queue.oldest_pending_seconds > self.OLD_PENDING_JOB_THRESHOLD_SECONDS:
            minutes = queue.oldest_pending_seconds // 60
            warnings.append(f"Oldest pending job waiting {minutes} minutes")

        # Check failure rate
        total_recent_jobs = queue.completed_last_hour + queue.failed_last_hour
        if total_recent_jobs > 0:
            failure_rate = queue.failed_last_hour / total_recent_jobs
            if failure_rate > 0.2:  # > 20% failure
                warnings.append(
                    f"High failure rate: {failure_rate:.1%} ({queue.failed_last_hour}/{total_recent_jobs} jobs)"
                )

        # Determine overall health
        if errors:
            return SystemHealth.ERROR, warnings, errors
        elif warnings:
            return SystemHealth.WARNING, warnings, errors
        else:
            return SystemHealth.HEALTHY, warnings, errors
```

### 2. Formatter Base Class

```python
"""Base formatter for status output."""

from abc import ABC, abstractmethod
from clx.cli.status.models import StatusInfo


class StatusFormatter(ABC):
    """Base class for status formatters."""

    @abstractmethod
    def format(self, status: StatusInfo, workers_only: bool = False, jobs_only: bool = False) -> str:
        """Format status information for output.

        Args:
            status: Status information to format
            workers_only: Only show worker information
            jobs_only: Only show job queue information

        Returns:
            Formatted string ready for output
        """
        pass

    @abstractmethod
    def get_exit_code(self, status: StatusInfo) -> int:
        """Get appropriate exit code for status.

        Args:
            status: Status information

        Returns:
            Exit code (0=healthy, 1=warning, 2=error)
        """
        pass
```

### 3. TableFormatter (Rich-based)

```python
"""Table formatter using Rich library."""

from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from clx.cli.status.formatter import StatusFormatter
from clx.cli.status.models import StatusInfo, SystemHealth, WorkerTypeStats


class TableFormatter(StatusFormatter):
    """Format status as rich tables."""

    def __init__(self, use_color: bool = True):
        """Initialize formatter.

        Args:
            use_color: Whether to use colored output
        """
        self.console = Console(force_terminal=use_color, no_color=not use_color)

    def format(self, status: StatusInfo, workers_only: bool = False, jobs_only: bool = False) -> str:
        """Format status information as tables."""
        # Create string buffer for output
        with self.console.capture() as capture:
            if not workers_only and not jobs_only:
                # Show header
                self._print_header(status)
                self.console.print()

            if not jobs_only:
                # Show workers
                self._print_workers(status)
                self.console.print()

            if not workers_only:
                # Show queue
                self._print_queue(status)
                self.console.print()

            if not workers_only and not jobs_only:
                # Show warnings/errors
                self._print_issues(status)

        return capture.get()

    def _print_header(self, status: StatusInfo):
        """Print header with system status."""
        health_icon = {
            SystemHealth.HEALTHY: "✓",
            SystemHealth.WARNING: "⚠",
            SystemHealth.ERROR: "✗",
        }[status.health]

        health_color = {
            SystemHealth.HEALTHY: "green",
            SystemHealth.WARNING: "yellow",
            SystemHealth.ERROR: "red",
        }[status.health]

        # Calculate time since last update
        seconds_ago = (datetime.now() - status.timestamp).total_seconds()
        if seconds_ago < 2:
            update_str = "just now"
        elif seconds_ago < 60:
            update_str = f"{int(seconds_ago)}s ago"
        else:
            minutes = int(seconds_ago / 60)
            update_str = f"{minutes}m ago"

        # Get database size
        if status.database.size_bytes:
            size_kb = status.database.size_bytes / 1024
            db_size = f"({size_kb:.0f} KB)"
        else:
            db_size = ""

        header_text = Text()
        header_text.append("CLX System Status\n", style="bold")
        header_text.append(f"Overall Status: ", style="dim")
        header_text.append(f"{health_icon} {status.health.value.title()}", style=f"bold {health_color}")
        header_text.append(f"\nDatabase: ", style="dim")
        header_text.append(f"{status.database.path} {db_size}", style="")
        header_text.append(f"\nLast Updated: ", style="dim")
        header_text.append(update_str, style="")

        self.console.print(Panel(header_text, border_style="blue"))

    def _print_workers(self, status: StatusInfo):
        """Print workers table."""
        self.console.print("[bold]Workers by Type[/bold]")

        for worker_type in ['notebook', 'plantuml', 'drawio']:
            stats = status.workers.get(worker_type)
            if not stats:
                continue

            # Worker type header
            mode_str = f" ({stats.execution_mode} mode)" if stats.execution_mode else ""
            self.console.print(f"[cyan]{worker_type.title()} Workers[/cyan]: {stats.total} total{mode_str}")

            if stats.total == 0:
                self.console.print("  [yellow]⚠ No workers registered[/yellow]")
                continue

            # Status breakdown
            if stats.idle > 0:
                self.console.print(f"  [green]✓ {stats.idle} idle[/green]")

            if stats.busy > 0:
                self.console.print(f"  [blue]⚙ {stats.busy} busy[/blue]")

                # Show busy worker details
                for bw in stats.busy_workers:
                    elapsed_str = self._format_elapsed(bw.elapsed_seconds)
                    # Truncate document path if too long
                    doc_path = bw.document_path
                    if len(doc_path) > 50:
                        doc_path = "..." + doc_path[-47:]

                    self.console.print(f"     Worker {bw.worker_id}: {doc_path} ({elapsed_str})")

            if stats.hung > 0:
                self.console.print(f"  [yellow]⚠ {stats.hung} hung[/yellow]")

            if stats.dead > 0:
                self.console.print(f"  [red]✗ {stats.dead} dead[/red]")

            self.console.print()

    def _print_queue(self, status: StatusInfo):
        """Print queue statistics."""
        self.console.print("[bold]Job Queue Status[/bold]")

        queue = status.queue

        # Pending jobs
        pending_str = f"Pending:    {queue.pending} jobs"
        if queue.oldest_pending_seconds:
            oldest_str = self._format_elapsed(queue.oldest_pending_seconds)
            pending_str += f"  (oldest: {oldest_str})"
            if queue.oldest_pending_seconds > 300:  # 5 minutes
                pending_str = f"[yellow]{pending_str} ⚠[/yellow]"

        self.console.print(f"  {pending_str}")

        # Processing jobs
        self.console.print(f"  Processing: {queue.processing} jobs")

        # Completed jobs
        self.console.print(f"  Completed:  {queue.completed_last_hour} jobs (last hour)")

        # Failed jobs
        total_recent = queue.completed_last_hour + queue.failed_last_hour
        failure_str = f"  Failed:     {queue.failed_last_hour} jobs"
        if total_recent > 0:
            failure_rate = queue.failed_last_hour / total_recent
            failure_str += f"  ({failure_rate:.1%} failure rate)"
            if failure_rate > 0.2:
                failure_str = f"[red]{failure_str}[/red]"

        self.console.print(failure_str)

    def _print_issues(self, status: StatusInfo):
        """Print warnings and errors."""
        if status.errors:
            for error in status.errors:
                self.console.print(f"[red]✗ Error: {error}[/red]")

        if status.warnings:
            for warning in status.warnings:
                self.console.print(f"[yellow]⚠ Warning: {warning}[/yellow]")

    def _format_elapsed(self, seconds: int) -> str:
        """Format elapsed time."""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}:{secs:02d}"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}:{minutes:02d}:{seconds % 60:02d}"

    def get_exit_code(self, status: StatusInfo) -> int:
        """Get exit code based on health."""
        return {
            SystemHealth.HEALTHY: 0,
            SystemHealth.WARNING: 1,
            SystemHealth.ERROR: 2,
        }[status.health]
```

### 4. JsonFormatter

```python
"""JSON formatter for machine-readable output."""

import json
from datetime import datetime

from clx.cli.status.formatter import StatusFormatter
from clx.cli.status.models import StatusInfo, SystemHealth


class JsonFormatter(StatusFormatter):
    """Format status as JSON."""

    def __init__(self, pretty: bool = True):
        """Initialize formatter.

        Args:
            pretty: Whether to pretty-print JSON
        """
        self.pretty = pretty

    def format(self, status: StatusInfo, workers_only: bool = False, jobs_only: bool = False) -> str:
        """Format status information as JSON."""
        data = {
            "status": status.health.value,
            "timestamp": status.timestamp.isoformat(),
        }

        if not jobs_only:
            # Database info
            data["database"] = {
                "path": status.database.path,
                "accessible": status.database.accessible,
                "exists": status.database.exists,
            }
            if status.database.size_bytes is not None:
                data["database"]["size_bytes"] = status.database.size_bytes
            if status.database.last_modified:
                data["database"]["last_modified"] = status.database.last_modified.isoformat()
            if status.database.error_message:
                data["database"]["error_message"] = status.database.error_message

        if not jobs_only:
            # Workers
            data["workers"] = {}
            for worker_type, stats in status.workers.items():
                worker_data = {
                    "total": stats.total,
                    "idle": stats.idle,
                    "busy": stats.busy,
                    "hung": stats.hung,
                    "dead": stats.dead,
                }

                if stats.execution_mode:
                    worker_data["execution_mode"] = stats.execution_mode

                if stats.busy_workers:
                    worker_data["busy_workers"] = [
                        {
                            "worker_id": bw.worker_id,
                            "job_id": bw.job_id,
                            "document": bw.document_path,
                            "elapsed_seconds": bw.elapsed_seconds,
                        }
                        for bw in stats.busy_workers
                    ]

                data["workers"][worker_type] = worker_data

        if not workers_only:
            # Queue
            data["queue"] = {
                "pending": status.queue.pending,
                "processing": status.queue.processing,
                "completed_last_hour": status.queue.completed_last_hour,
                "failed_last_hour": status.queue.failed_last_hour,
            }
            if status.queue.oldest_pending_seconds is not None:
                data["queue"]["oldest_pending_seconds"] = status.queue.oldest_pending_seconds

        # Issues
        if status.warnings:
            data["warnings"] = status.warnings
        if status.errors:
            data["errors"] = status.errors

        if self.pretty:
            return json.dumps(data, indent=2)
        else:
            return json.dumps(data)

    def get_exit_code(self, status: StatusInfo) -> int:
        """Get exit code based on health."""
        return {
            SystemHealth.HEALTHY: 0,
            SystemHealth.WARNING: 1,
            SystemHealth.ERROR: 2,
        }[status.health]
```

### 5. CompactFormatter

```python
"""Compact single-line formatter."""

from clx.cli.status.formatter import StatusFormatter
from clx.cli.status.models import StatusInfo, SystemHealth


class CompactFormatter(StatusFormatter):
    """Format status as compact single line."""

    def format(self, status: StatusInfo, workers_only: bool = False, jobs_only: bool = False) -> str:
        """Format status information as compact line."""
        parts = []

        # Health
        parts.append(status.health.value)

        if not jobs_only:
            # Workers
            worker_parts = []
            for worker_type in ['notebook', 'plantuml', 'drawio']:
                stats = status.workers.get(worker_type)
                if stats and stats.total > 0:
                    worker_parts.append(
                        f"{stats.total} {worker_type} ({stats.idle} idle, {stats.busy} busy)"
                    )
            if worker_parts:
                parts.append(": " + ", ".join(worker_parts))

        if not workers_only:
            # Queue
            queue_parts = []
            if status.queue.pending > 0:
                queue_parts.append(f"{status.queue.pending} pending")
            if status.queue.processing > 0:
                queue_parts.append(f"{status.queue.processing} processing")
            if queue_parts:
                parts.append(" | queue: " + ", ".join(queue_parts))

        return "".join(parts)

    def get_exit_code(self, status: StatusInfo) -> int:
        """Get exit code based on health."""
        return {
            SystemHealth.HEALTHY: 0,
            SystemHealth.WARNING: 1,
            SystemHealth.ERROR: 2,
        }[status.health]
```

## CLI Integration

### Add Status Command to main.py

```python
"""Add status command to CLI."""

import click
from pathlib import Path

from clx.cli.status.collector import StatusCollector
from clx.cli.status.formatters.table_formatter import TableFormatter
from clx.cli.status.formatters.json_formatter import JsonFormatter
from clx.cli.status.formatters.compact_formatter import CompactFormatter


@cli.command()
@click.option(
    '--db-path',
    type=click.Path(exists=False, path_type=Path),
    help='Path to SQLite database (auto-detected if not specified)',
)
@click.option(
    '--workers',
    is_flag=True,
    help='Show only worker information',
)
@click.option(
    '--jobs',
    is_flag=True,
    help='Show only job queue information',
)
@click.option(
    '--format',
    type=click.Choice(['table', 'json', 'compact'], case_sensitive=False),
    default='table',
    help='Output format',
)
@click.option(
    '--no-color',
    is_flag=True,
    help='Disable colored output',
)
def status(db_path, workers, jobs, format, no_color):
    """Show CLX system status.

    Displays worker availability, job queue status, and system health.

    Examples:

        clx status                      # Show full status
        clx status --workers            # Show only workers
        clx status --format=json        # JSON output
        clx status --db-path=/data/clx_jobs.db  # Custom database
    """
    # Create collector
    collector = StatusCollector(db_path=db_path)

    # Collect status
    try:
        status_info = collector.collect()
    except Exception as e:
        click.echo(f"Error collecting status: {e}", err=True)
        return 2

    # Create formatter
    if format == 'json':
        formatter = JsonFormatter(pretty=True)
    elif format == 'compact':
        formatter = CompactFormatter()
    else:  # table
        formatter = TableFormatter(use_color=not no_color)

    # Format and display
    output = formatter.format(status_info, workers_only=workers, jobs_only=jobs)
    click.echo(output)

    # Exit with appropriate code
    exit_code = formatter.get_exit_code(status_info)
    raise SystemExit(exit_code)
```

## Testing Strategy

### Unit Tests

```python
"""Unit tests for status command."""

import pytest
from pathlib import Path
from datetime import datetime

from clx.cli.status.models import (
    StatusInfo,
    SystemHealth,
    DatabaseInfo,
    WorkerTypeStats,
    QueueStats,
)
from clx.cli.status.formatters.json_formatter import JsonFormatter
from clx.cli.status.formatters.compact_formatter import CompactFormatter


def test_json_formatter_basic():
    """Test JSON formatter with basic status."""
    status = StatusInfo(
        timestamp=datetime(2025, 11, 15, 10, 30, 0),
        health=SystemHealth.HEALTHY,
        database=DatabaseInfo(
            path="/path/to/db",
            accessible=True,
            exists=True,
            size_bytes=102400,
        ),
        workers={
            'notebook': WorkerTypeStats(
                worker_type='notebook',
                execution_mode='direct',
                total=2,
                idle=1,
                busy=1,
                hung=0,
                dead=0,
            )
        },
        queue=QueueStats(
            pending=5,
            processing=2,
            completed_last_hour=100,
            failed_last_hour=2,
        ),
    )

    formatter = JsonFormatter(pretty=False)
    output = formatter.format(status)

    import json
    data = json.loads(output)

    assert data['status'] == 'healthy'
    assert data['workers']['notebook']['total'] == 2
    assert data['queue']['pending'] == 5


def test_compact_formatter():
    """Test compact formatter output."""
    status = StatusInfo(
        timestamp=datetime.now(),
        health=SystemHealth.WARNING,
        database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
        workers={
            'notebook': WorkerTypeStats(
                worker_type='notebook',
                execution_mode='direct',
                total=2,
                idle=0,
                busy=2,
                hung=0,
                dead=0,
            )
        },
        queue=QueueStats(
            pending=10,
            processing=2,
            completed_last_hour=50,
            failed_last_hour=5,
        ),
        warnings=["High queue depth"],
    )

    formatter = CompactFormatter()
    output = formatter.format(status)

    assert "warning" in output
    assert "2 notebook" in output
    assert "10 pending" in output


def test_exit_codes():
    """Test exit codes for different health states."""
    from clx.cli.status.formatters.table_formatter import TableFormatter

    formatter = TableFormatter()

    healthy_status = StatusInfo(
        timestamp=datetime.now(),
        health=SystemHealth.HEALTHY,
        database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
        workers={},
        queue=QueueStats(0, 0, 0, 0),
    )
    assert formatter.get_exit_code(healthy_status) == 0

    warning_status = StatusInfo(
        timestamp=datetime.now(),
        health=SystemHealth.WARNING,
        database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
        workers={},
        queue=QueueStats(0, 0, 0, 0),
        warnings=["Something"],
    )
    assert formatter.get_exit_code(warning_status) == 1

    error_status = StatusInfo(
        timestamp=datetime.now(),
        health=SystemHealth.ERROR,
        database=DatabaseInfo(path="/path/to/db", accessible=True, exists=True),
        workers={},
        queue=QueueStats(0, 0, 0, 0),
        errors=["Database error"],
    )
    assert formatter.get_exit_code(error_status) == 2
```

### Integration Tests

```python
"""Integration tests for status command."""

import pytest
from pathlib import Path
from click.testing import CliRunner

from clx.cli.main import cli


@pytest.mark.integration
def test_status_command_with_database(tmp_path):
    """Test status command with real database."""
    from clx.infrastructure.database.job_queue import JobQueue

    # Create database with some data
    db_path = tmp_path / "test_jobs.db"
    job_queue = JobQueue(db_path)

    # Register a worker
    worker_id = job_queue.register_worker(
        worker_type="notebook",
        execution_mode="direct"
    )

    # Run status command
    runner = CliRunner()
    result = runner.invoke(cli, ['status', '--db-path', str(db_path)])

    assert result.exit_code == 0
    assert "notebook" in result.output.lower()
    assert "1 total" in result.output.lower()


@pytest.mark.integration
def test_status_command_no_database(tmp_path):
    """Test status command when database doesn't exist."""
    db_path = tmp_path / "nonexistent.db"

    runner = CliRunner()
    result = runner.invoke(cli, ['status', '--db-path', str(db_path)])

    assert result.exit_code == 2  # Error
    assert "not found" in result.output.lower() or "not accessible" in result.output.lower()


@pytest.mark.integration
def test_status_command_json_format(tmp_path):
    """Test status command with JSON format."""
    from clx.infrastructure.database.job_queue import JobQueue

    db_path = tmp_path / "test_jobs.db"
    job_queue = JobQueue(db_path)

    runner = CliRunner()
    result = runner.invoke(cli, ['status', '--db-path', str(db_path), '--format=json'])

    assert result.exit_code == 0

    import json
    data = json.loads(result.output)
    assert 'status' in data
    assert 'workers' in data
    assert 'queue' in data
```

## Performance Considerations

1. **Database Query Optimization**
   - Use indexes on worker_type, status, and timestamps
   - Limit queries to necessary data only
   - Use connection pooling for repeated queries

2. **Caching**
   - Cache results for 1 second to support rapid repeated calls
   - Invalidate cache on database modification

3. **Timeout Handling**
   - Set 5-second timeout for database operations
   - Use retry logic with exponential backoff for locked database

## Documentation

### Help Text

```bash
$ clx status --help
Usage: clx status [OPTIONS]

  Show CLX system status.

  Displays worker availability, job queue status, and system health.

Examples:

    clx status                      # Show full status
    clx status --workers            # Show only workers
    clx status --format=json        # JSON output
    clx status --db-path=/data/clx_jobs.db  # Custom database

Options:
  --db-path PATH                  Path to SQLite database (auto-detected if
                                  not specified)
  --workers                       Show only worker information
  --jobs                          Show only job queue information
  --format [table|json|compact]   Output format  [default: table]
  --no-color                      Disable colored output
  --help                          Show this message and exit.
```

### User Documentation

Add to `docs/user-guide/`:

**Quick Status Check** (`docs/user-guide/status-command.md`):
```markdown
# Status Command

The `clx status` command provides a quick snapshot of your CLX system state.

## Basic Usage

```bash
clx status
```

This shows:
- System health (healthy, warning, or error)
- Worker availability by type
- Current jobs being processed
- Job queue statistics

## Output Formats

### Table Format (Default)

Human-readable table with color coding...

### JSON Format

Machine-parseable JSON for scripting...

### Compact Format

Single-line summary for monitoring...

## Exit Codes

The command returns different exit codes based on system health:
- `0`: System healthy
- `1`: Warning (some issues, but functional)
- `2`: Error (critical issues, not functional)

Use in shell scripts:
```bash
if clx status --format=compact; then
    echo "System healthy, starting build..."
    clx build course.yaml
else
    echo "System not ready!"
    exit 1
fi
```
```

## Implementation Checklist

- [ ] Create status module structure
- [ ] Implement StatusInfo data models
- [ ] Implement StatusCollector class
- [ ] Implement TableFormatter (with Rich)
- [ ] Implement JsonFormatter
- [ ] Implement CompactFormatter
- [ ] Add status command to CLI main.py
- [ ] Add unit tests for formatters
- [ ] Add unit tests for collector
- [ ] Add integration tests with database
- [ ] Add documentation to user guide
- [ ] Test on various terminal types
- [ ] Test with different database states
- [ ] Add optional dependency on Rich
- [ ] Update pyproject.toml
- [ ] Test exit codes in shell scripts

## Future Enhancements

1. **Watch Mode**: `clx status --watch` for continuous updates (later, use TUI instead)
2. **Remote Database**: Support PostgreSQL for multi-user deployments
3. **Prometheus Metrics**: Export metrics in Prometheus format
4. **Historical Status**: Show status trends over time
5. **Worker Control**: Add `--stop-worker` flag to stop specific workers (requires authentication)
