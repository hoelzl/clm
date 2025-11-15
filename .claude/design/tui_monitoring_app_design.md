# TUI Real-Time Monitoring Application Design

**Version**: 1.0
**Date**: 2025-11-15
**Purpose**: Technical design for implementing `clx monitor` TUI application for real-time system monitoring

## Overview

This document provides the detailed technical design for implementing the `clx monitor` TUI (Terminal User Interface) application, which provides real-time monitoring of the CLX system through an interactive terminal interface with configurable refresh intervals (1-5 seconds).

### Design Goals

1. **Real-Time Updates**: Smooth, flicker-free updates at 1-5 second intervals
2. **Rich Information**: Multiple panels showing workers, queue, and activity
3. **Interactive**: Keyboard navigation, scrolling, pause/resume
4. **Responsive**: Adapts to terminal size, handles resize events
5. **Performant**: Efficient database queries, minimal CPU usage
6. **Accessible**: Color themes, fallback for no-color mode

## Technology Stack

### Textual Framework

We'll use **Textual** (https://github.com/Textualize/textual) for the TUI:

**Advantages:**
- Modern Python async/await support
- Built-in widgets (tables, panels, scrolling)
- Automatic terminal resize handling
- Rich integration for styling
- Active development and good documentation
- Cross-platform (Linux, macOS, Windows)

**Dependencies:**
```toml
[project.optional-dependencies]
tui = [
    "textual>=0.50.0",
    "rich>=13.7.0",
]
```

## Architecture

### Component Overview

```
┌──────────────────────────────────────────────────────┐
│              clx monitor                             │
│           (TUI Application)                          │
└─────────────┬────────────────────────────────────────┘
              │
              ├──> CLXMonitorApp (Textual App)
              │    ├── Compose layout (widgets)
              │    ├── Handle keyboard events
              │    ├── Manage refresh timer
              │    └── Update widget data
              │
              ├──> StatusWidget (Custom Widget)
              │    ├── Header with system status
              │    └── Auto-update on timer
              │
              ├──> WorkersWidget (Custom Widget)
              │    ├── Table of workers by type
              │    ├── Busy worker details
              │    └── Color-coded status
              │
              ├──> QueueWidget (Custom Widget)
              │    ├── Queue statistics
              │    ├── Progress indicators
              │    └── Warning highlights
              │
              ├──> ActivityWidget (Custom Widget)
              │    ├── Scrollable event log
              │    ├── Keyboard scroll controls
              │    └── Event buffer (500 events)
              │
              ├──> FooterWidget (Built-in)
              │    └── Keyboard shortcuts
              │
              └──> DataProvider (data access)
                   ├── JobQueue wrapper
                   ├── Query worker stats
                   ├── Query queue stats
                   └── Query recent events
```

## Module Structure

### File Layout

```
src/clx/cli/
├── main.py                         # Main CLI with monitor command
├── monitor/
│   ├── __init__.py
│   ├── app.py                      # CLXMonitorApp (main Textual app)
│   ├── data_provider.py            # DataProvider (database queries)
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── status_header.py        # StatusHeader widget
│   │   ├── workers_panel.py        # WorkersPanel widget
│   │   ├── queue_panel.py          # QueuePanel widget
│   │   └── activity_panel.py       # ActivityPanel widget
│   ├── models.py                   # Data models
│   └── formatters.py               # Formatting utilities (time, size)
```

## Data Models

### MonitorData Classes

```python
"""Data models for monitor application."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict
from enum import Enum


class EventType(Enum):
    """Activity event types."""
    JOB_STARTED = "job_started"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    WORKER_ASSIGNED = "worker_assigned"
    WORKER_IDLE = "worker_idle"


@dataclass
class WorkerInfo:
    """Information about a single worker."""
    worker_id: str
    worker_type: str  # notebook, plantuml, drawio
    status: str  # idle, busy, hung, dead
    execution_mode: str  # direct, docker
    current_job_id: Optional[str] = None
    current_document: Optional[str] = None
    elapsed_seconds: Optional[int] = None
    cpu_percent: Optional[float] = None  # Docker mode only
    memory_mb: Optional[int] = None  # Docker mode only
    jobs_processed: int = 0
    uptime_seconds: int = 0
    last_heartbeat: Optional[datetime] = None


@dataclass
class QueueInfo:
    """Job queue information."""
    pending: int
    processing: int
    completed_last_hour: int
    failed_last_hour: int
    oldest_pending_seconds: Optional[int] = None
    throughput_jobs_per_min: float = 0.0
    avg_duration_seconds: float = 0.0


@dataclass
class ActivityEvent:
    """Activity log event."""
    timestamp: datetime
    event_type: EventType
    job_id: Optional[str] = None
    worker_id: Optional[str] = None
    document_path: Optional[str] = None
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class SystemStatus:
    """Overall system status."""
    health: str  # healthy, warning, error
    timestamp: datetime
    database_path: str
    database_size_bytes: int
    workers: List[WorkerInfo]
    queue: QueueInfo
    recent_events: List[ActivityEvent]
    warnings: List[str] = field(default_factory=list)
```

## Core Classes

### 1. DataProvider

```python
"""Data provider for monitor application."""

import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

from clx.infrastructure.database.job_queue import JobQueue
from clx.cli.monitor.models import (
    SystemStatus,
    WorkerInfo,
    QueueInfo,
    ActivityEvent,
    EventType,
)

logger = logging.getLogger(__name__)


class DataProvider:
    """Provide data from database for monitor UI."""

    def __init__(self, db_path: Path):
        """Initialize data provider.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.job_queue = JobQueue(db_path)

    def get_system_status(self) -> SystemStatus:
        """Get complete system status.

        Returns:
            SystemStatus with all current data
        """
        timestamp = datetime.now()

        # Get workers
        workers = self._get_workers()

        # Get queue stats
        queue = self._get_queue_info()

        # Get recent events
        events = self._get_recent_events(limit=100)

        # Determine health
        health, warnings = self._determine_health(workers, queue)

        # Get database info
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

        return SystemStatus(
            health=health,
            timestamp=timestamp,
            database_path=str(self.db_path),
            database_size_bytes=db_size,
            workers=workers,
            queue=queue,
            recent_events=events,
            warnings=warnings,
        )

    def _get_workers(self) -> List[WorkerInfo]:
        """Get list of all workers with details."""
        workers = []

        try:
            conn = self.job_queue._get_conn()
            cursor = conn.execute(
                """
                SELECT
                    w.id,
                    w.worker_id,
                    w.worker_type,
                    w.status,
                    w.execution_mode,
                    w.jobs_processed,
                    w.last_heartbeat,
                    w.created_at,
                    j.job_id,
                    j.document_path,
                    CAST((julianday('now') - julianday(j.started_at)) * 86400 AS INTEGER) as elapsed
                FROM workers w
                LEFT JOIN jobs j ON j.worker_id = w.id AND j.status = 'processing'
                WHERE w.status != 'dead'
                ORDER BY w.worker_type, w.id
                """
            )

            for row in cursor.fetchall():
                # Calculate uptime
                created_at = datetime.fromisoformat(row[7])
                uptime_seconds = int((datetime.now() - created_at).total_seconds())

                # Parse heartbeat
                last_heartbeat = None
                if row[6]:
                    last_heartbeat = datetime.fromisoformat(row[6])

                # Get Docker stats if applicable
                cpu_percent = None
                memory_mb = None
                if row[4] == 'docker':
                    cpu_percent, memory_mb = self._get_docker_stats(row[1])

                workers.append(
                    WorkerInfo(
                        worker_id=row[1],
                        worker_type=row[2],
                        status=row[3],
                        execution_mode=row[4] or 'unknown',
                        current_job_id=row[8],
                        current_document=row[9],
                        elapsed_seconds=row[10],
                        cpu_percent=cpu_percent,
                        memory_mb=memory_mb,
                        jobs_processed=row[5] or 0,
                        uptime_seconds=uptime_seconds,
                        last_heartbeat=last_heartbeat,
                    )
                )

            return workers

        except Exception as e:
            logger.error(f"Error getting workers: {e}", exc_info=True)
            return []

    def _get_docker_stats(self, worker_id: str) -> tuple[Optional[float], Optional[int]]:
        """Get Docker container CPU and memory stats.

        Args:
            worker_id: Worker container ID

        Returns:
            Tuple of (cpu_percent, memory_mb) or (None, None)
        """
        try:
            import docker
            client = docker.from_env()
            container = client.containers.get(worker_id)
            stats = container.stats(stream=False)

            # Calculate CPU percentage
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                        stats['precpu_stats']['cpu_usage']['total_usage']
            system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                          stats['precpu_stats']['system_cpu_usage']
            cpu_count = stats['cpu_stats']['online_cpus']

            cpu_percent = 0.0
            if system_delta > 0 and cpu_delta > 0:
                cpu_percent = (cpu_delta / system_delta) * cpu_count * 100.0

            # Get memory usage in MB
            memory_bytes = stats['memory_stats']['usage']
            memory_mb = memory_bytes / (1024 * 1024)

            return cpu_percent, int(memory_mb)

        except Exception as e:
            logger.debug(f"Could not get Docker stats for {worker_id}: {e}")
            return None, None

    def _get_queue_info(self) -> QueueInfo:
        """Get queue statistics."""
        try:
            stats = self.job_queue.get_queue_statistics()

            # Get oldest pending job
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

            # Calculate throughput (jobs/minute over last 5 minutes)
            five_min_ago = datetime.now() - timedelta(minutes=5)
            conn = self.job_queue._get_conn()
            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE status = 'completed'
                  AND completed_at > ?
                """,
                (five_min_ago.isoformat(),)
            )
            completed_last_5min = cursor.fetchone()[0]
            throughput = completed_last_5min / 5.0  # jobs per minute

            # Calculate average duration (last 10 completed jobs)
            cursor = conn.execute(
                """
                SELECT AVG(CAST((julianday(completed_at) - julianday(started_at)) * 86400 AS REAL))
                FROM (
                    SELECT started_at, completed_at
                    FROM jobs
                    WHERE status = 'completed'
                      AND started_at IS NOT NULL
                      AND completed_at IS NOT NULL
                    ORDER BY completed_at DESC
                    LIMIT 10
                )
                """
            )
            avg_duration = cursor.fetchone()[0] or 0.0

            # Get completed/failed in last hour
            one_hour_ago = datetime.now() - timedelta(hours=1)
            cursor = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'completed' AND completed_at > ?",
                (one_hour_ago.isoformat(),)
            )
            completed_last_hour = cursor.fetchone()[0]

            cursor = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'failed' AND completed_at > ?",
                (one_hour_ago.isoformat(),)
            )
            failed_last_hour = cursor.fetchone()[0]

            return QueueInfo(
                pending=stats.get('pending', 0),
                processing=stats.get('processing', 0),
                completed_last_hour=completed_last_hour,
                failed_last_hour=failed_last_hour,
                oldest_pending_seconds=oldest_pending_seconds,
                throughput_jobs_per_min=throughput,
                avg_duration_seconds=avg_duration,
            )

        except Exception as e:
            logger.error(f"Error getting queue info: {e}", exc_info=True)
            return QueueInfo(0, 0, 0, 0, None, 0.0, 0.0)

    def _get_recent_events(self, limit: int = 100) -> List[ActivityEvent]:
        """Get recent activity events.

        Args:
            limit: Maximum number of events to return

        Returns:
            List of recent activity events
        """
        events = []

        try:
            conn = self.job_queue._get_conn()

            # Query job events (started, completed, failed)
            cursor = conn.execute(
                """
                SELECT
                    j.status,
                    j.job_id,
                    j.document_path,
                    COALESCE(j.completed_at, j.started_at, j.created_at) as event_time,
                    w.worker_id,
                    CAST((julianday(j.completed_at) - julianday(j.started_at)) * 86400 AS INTEGER) as duration,
                    j.error_message
                FROM jobs j
                LEFT JOIN workers w ON w.id = j.worker_id
                WHERE j.status IN ('processing', 'completed', 'failed')
                ORDER BY event_time DESC
                LIMIT ?
                """,
                (limit,)
            )

            for row in cursor.fetchall():
                status = row[0]
                timestamp = datetime.fromisoformat(row[3])

                if status == 'processing':
                    event_type = EventType.JOB_STARTED
                elif status == 'completed':
                    event_type = EventType.JOB_COMPLETED
                else:  # failed
                    event_type = EventType.JOB_FAILED

                events.append(
                    ActivityEvent(
                        timestamp=timestamp,
                        event_type=event_type,
                        job_id=row[1],
                        document_path=row[2],
                        worker_id=row[4],
                        duration_seconds=row[5],
                        error_message=row[6],
                    )
                )

            # Sort by timestamp (most recent first)
            events.sort(key=lambda e: e.timestamp, reverse=True)

            return events[:limit]

        except Exception as e:
            logger.error(f"Error getting recent events: {e}", exc_info=True)
            return []

    def _determine_health(
        self,
        workers: List[WorkerInfo],
        queue: QueueInfo
    ) -> tuple[str, List[str]]:
        """Determine system health.

        Args:
            workers: List of workers
            queue: Queue info

        Returns:
            Tuple of (health_status, warnings)
        """
        warnings = []

        # Check for workers
        if not workers:
            return "error", ["No workers registered"]

        # Check for hung workers
        hung_count = sum(1 for w in workers if w.status == 'hung')
        if hung_count > 0:
            warnings.append(f"{hung_count} worker(s) hung")

        # Check for stale heartbeats
        now = datetime.now()
        stale_workers = [
            w for w in workers
            if w.last_heartbeat and (now - w.last_heartbeat).total_seconds() > 30
        ]
        if stale_workers:
            warnings.append(f"{len(stale_workers)} worker(s) with stale heartbeat")

        # Check queue
        if queue.pending > 10:
            idle_workers = sum(1 for w in workers if w.status == 'idle')
            if idle_workers == 0:
                warnings.append(f"{queue.pending} jobs pending, no idle workers")

        # Determine overall health
        if not workers:
            return "error", warnings
        elif warnings:
            return "warning", warnings
        else:
            return "healthy", warnings
```

### 2. Custom Widgets

#### StatusHeader Widget

```python
"""Status header widget."""

from textual.app import ComposeResult
from textual.widgets import Static
from textual.containers import Container
from rich.text import Text

from clx.cli.monitor.models import SystemStatus


class StatusHeader(Static):
    """Header showing system status summary."""

    def __init__(self, **kwargs):
        """Initialize header widget."""
        super().__init__(**kwargs)
        self.status: SystemStatus | None = None

    def update_status(self, status: SystemStatus) -> None:
        """Update with new status data.

        Args:
            status: System status data
        """
        self.status = status
        self.update(self._render_content())

    def _render_content(self) -> Text:
        """Render header content."""
        if not self.status:
            return Text("CLX Monitor - Loading...", style="bold")

        # Health indicator
        health_icon = {
            "healthy": "✓",
            "warning": "⚠",
            "error": "✗",
        }.get(self.status.health, "?")

        health_color = {
            "healthy": "green",
            "warning": "yellow",
            "error": "red",
        }.get(self.status.health, "white")

        # Format timestamp
        time_str = self.status.timestamp.strftime("%H:%M:%S")

        # Format database size
        size_kb = self.status.database_size_bytes / 1024
        db_size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"

        # Build header text
        text = Text()
        text.append("CLX Monitor v0.3.0", style="bold cyan")
        text.append(" | ")
        text.append(f"{health_icon} {self.status.health.title()}", style=f"bold {health_color}")
        text.append(" | ")
        text.append(time_str, style="dim")
        text.append(" | DB: ")
        text.append(db_size_str, style="dim")

        return text
```

#### WorkersPanel Widget

```python
"""Workers panel widget."""

from textual.app import ComposeResult
from textual.widgets import Static, DataTable
from textual.containers import VerticalScroll

from clx.cli.monitor.models import SystemStatus, WorkerInfo
from clx.cli.monitor.formatters import format_elapsed


class WorkersPanel(Static):
    """Panel showing worker status."""

    def __init__(self, **kwargs):
        """Initialize workers panel."""
        super().__init__(**kwargs)
        self.workers: list[WorkerInfo] = []

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static("Workers", classes="panel-title")
        yield VerticalScroll(
            id="workers-content"
        )

    def update_status(self, status: SystemStatus) -> None:
        """Update with new status data.

        Args:
            status: System status data
        """
        self.workers = status.workers
        self._render_workers()

    def _render_workers(self) -> None:
        """Render workers content."""
        content = self.query_one("#workers-content", VerticalScroll)
        content.remove_children()

        if not self.workers:
            content.mount(Static("[yellow]⚠ No workers registered[/yellow]"))
            return

        # Group by worker type
        by_type = {}
        for worker in self.workers:
            if worker.worker_type not in by_type:
                by_type[worker.worker_type] = []
            by_type[worker.worker_type].append(worker)

        # Render each type
        for worker_type in ['notebook', 'plantuml', 'drawio']:
            if worker_type not in by_type:
                continue

            workers = by_type[worker_type]

            # Count by status
            total = len(workers)
            idle = sum(1 for w in workers if w.status == 'idle')
            busy = sum(1 for w in workers if w.status == 'busy')
            hung = sum(1 for w in workers if w.status == 'hung')

            # Worker type header
            mode = workers[0].execution_mode if workers else 'unknown'
            header = f"[cyan]{worker_type.title()}[/cyan] ({total} total, {mode} mode)"
            content.mount(Static(header))

            # Status summary
            if idle > 0:
                content.mount(Static(f"  [green]✓ {idle} idle[/green]"))
            if busy > 0:
                content.mount(Static(f"  [blue]⚙ {busy} busy[/blue]"))

                # Show busy worker details
                for w in workers:
                    if w.status == 'busy' and w.current_document:
                        elapsed = format_elapsed(w.elapsed_seconds or 0)
                        doc = w.current_document
                        if len(doc) > 40:
                            doc = "..." + doc[-37:]

                        cpu_str = ""
                        if w.cpu_percent is not None:
                            cpu_str = f"  [{w.cpu_percent:.0f}% CPU]"

                        content.mount(
                            Static(f"     {w.worker_id}: {doc} ({elapsed}){cpu_str}")
                        )

            if hung > 0:
                content.mount(Static(f"  [yellow]⚠ {hung} hung[/yellow]"))

            content.mount(Static(""))  # Blank line
```

#### QueuePanel Widget

```python
"""Queue panel widget."""

from textual.widgets import Static
from textual.containers import VerticalScroll
from textual.app import ComposeResult

from clx.cli.monitor.models import SystemStatus
from clx.cli.monitor.formatters import format_elapsed


class QueuePanel(Static):
    """Panel showing queue statistics."""

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static("Job Queue", classes="panel-title")
        yield VerticalScroll(id="queue-content")

    def update_status(self, status: SystemStatus) -> None:
        """Update with new status data.

        Args:
            status: System status data
        """
        content = self.query_one("#queue-content", VerticalScroll)
        content.remove_children()

        queue = status.queue

        # Pending jobs
        pending_text = f"Pending:    {queue.pending} jobs"
        if queue.oldest_pending_seconds:
            oldest = format_elapsed(queue.oldest_pending_seconds)
            pending_text += f"  (oldest: {oldest})"
            if queue.oldest_pending_seconds > 300:
                pending_text = f"[yellow]{pending_text} ⚠[/yellow]"

        content.mount(Static(pending_text))

        # Processing jobs
        content.mount(Static(f"Processing: {queue.processing} jobs"))

        # Completed jobs
        content.mount(Static(f"Completed:  {queue.completed_last_hour} jobs (last hour)"))

        # Failed jobs
        total = queue.completed_last_hour + queue.failed_last_hour
        failure_text = f"Failed:     {queue.failed_last_hour} jobs"
        if total > 0:
            rate = queue.failed_last_hour / total
            failure_text += f"  ({rate:.1%})"
            if rate > 0.2:
                failure_text = f"[red]{failure_text}[/red]"

        content.mount(Static(failure_text))

        # Blank line
        content.mount(Static(""))

        # Metrics
        content.mount(Static(f"Throughput: {queue.throughput_jobs_per_min:.1f} jobs/min"))
        content.mount(Static(f"Avg Time:   {queue.avg_duration_seconds:.1f}s per job"))
```

#### ActivityPanel Widget

```python
"""Activity panel widget with scrolling."""

from textual.widgets import Static, RichLog
from textual.app import ComposeResult
from textual.containers import Container

from clx.cli.monitor.models import SystemStatus, EventType
from clx.cli.monitor.formatters import format_timestamp


class ActivityPanel(Static):
    """Panel showing recent activity log."""

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static("Recent Activity", classes="panel-title")
        yield RichLog(id="activity-log", wrap=False, highlight=True)

    def update_status(self, status: SystemStatus) -> None:
        """Update with new status data.

        Args:
            status: System status data
        """
        log = self.query_one("#activity-log", RichLog)

        # Clear and repopulate (Textual RichLog handles scrolling)
        log.clear()

        for event in status.recent_events:
            timestamp = format_timestamp(event.timestamp)

            # Format based on event type
            if event.event_type == EventType.JOB_STARTED:
                log.write(f"{timestamp} [blue]⚙ Started[/blue]    {event.document_path}")

            elif event.event_type == EventType.JOB_COMPLETED:
                duration = format_elapsed(event.duration_seconds) if event.duration_seconds else "?"
                log.write(f"{timestamp} [green]✓ Completed[/green]  {event.document_path}  ({duration})")

            elif event.event_type == EventType.JOB_FAILED:
                duration = format_elapsed(event.duration_seconds) if event.duration_seconds else "?"
                error = event.error_message[:30] if event.error_message else "unknown error"
                log.write(f"{timestamp} [red]✗ Failed[/red]     {event.document_path}  ({duration}) - {error}")
```

### 3. Main Application

```python
"""Main TUI application."""

import logging
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Header, Footer
from textual.binding import Binding

from clx.cli.monitor.data_provider import DataProvider
from clx.cli.monitor.widgets.status_header import StatusHeader
from clx.cli.monitor.widgets.workers_panel import WorkersPanel
from clx.cli.monitor.widgets.queue_panel import QueuePanel
from clx.cli.monitor.widgets.activity_panel import ActivityPanel

logger = logging.getLogger(__name__)


class CLXMonitorApp(App):
    """CLX Real-Time Monitoring TUI Application."""

    CSS_PATH = "monitor.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("r", "refresh", "Refresh"),
        Binding("p", "pause", "Pause/Resume"),
        Binding("escape", "quit", "Quit", show=False),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        db_path: Path,
        refresh_interval: int = 2,
        theme: str = "dark",
    ):
        """Initialize monitor application.

        Args:
            db_path: Path to SQLite database
            refresh_interval: Refresh interval in seconds
            theme: Color theme (dark, light, auto)
        """
        super().__init__()
        self.db_path = db_path
        self.refresh_interval = refresh_interval
        self.theme_name = theme
        self.data_provider = DataProvider(db_path)
        self.paused = False

        # Widget references
        self.status_header = None
        self.workers_panel = None
        self.queue_panel = None
        self.activity_panel = None

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        # Header
        self.status_header = StatusHeader(id="status-header")
        yield self.status_header

        # Main content area
        yield Container(
            Horizontal(
                WorkersPanel(id="workers-panel", classes="panel"),
                QueuePanel(id="queue-panel", classes="panel"),
                id="top-panels",
            ),
            ActivityPanel(id="activity-panel", classes="panel"),
            id="main-content",
        )

        # Footer with keyboard shortcuts
        yield Footer()

    def on_mount(self) -> None:
        """Set up refresh timer when app mounts."""
        self.workers_panel = self.query_one("#workers-panel", WorkersPanel)
        self.queue_panel = self.query_one("#queue-panel", QueuePanel)
        self.activity_panel = self.query_one("#activity-panel", ActivityPanel)

        # Initial data load
        self.refresh_data()

        # Set up periodic refresh
        self.set_interval(self.refresh_interval, self.refresh_data)

    def refresh_data(self) -> None:
        """Refresh data from database and update widgets."""
        if self.paused:
            return

        try:
            # Get fresh data
            status = self.data_provider.get_system_status()

            # Update all widgets
            self.status_header.update_status(status)
            self.workers_panel.update_status(status)
            self.queue_panel.update_status(status)
            self.activity_panel.update_status(status)

        except Exception as e:
            logger.error(f"Error refreshing data: {e}", exc_info=True)
            self.notify(f"Error refreshing data: {e}", severity="error")

    def action_refresh(self) -> None:
        """Handle manual refresh (r key)."""
        self.refresh_data()
        self.notify("Refreshed", timeout=1)

    def action_pause(self) -> None:
        """Handle pause/resume (p key)."""
        self.paused = not self.paused
        if self.paused:
            self.notify("Paused - Press 'p' to resume", severity="warning")
            self.sub_title = "PAUSED"
        else:
            self.notify("Resumed", timeout=1)
            self.sub_title = ""
            self.refresh_data()

    def action_quit(self) -> None:
        """Handle quit action."""
        self.exit()
```

### 4. CSS Styling

```css
/* monitor.tcss - Textual CSS for monitor app */

StatusHeader {
    height: 3;
    background: $panel;
    border: solid $primary;
    padding: 1;
    content-align: center middle;
}

#main-content {
    height: 1fr;
}

#top-panels {
    height: 40%;
}

.panel {
    border: solid $primary;
    background: $panel;
    padding: 1;
}

.panel-title {
    color: $accent;
    text-style: bold;
    background: $panel;
}

#workers-panel {
    width: 60%;
}

#queue-panel {
    width: 40%;
}

#activity-panel {
    height: 60%;
}

RichLog {
    background: $surface;
    border: none;
}
```

## CLI Integration

### Add Monitor Command

```python
"""Add monitor command to CLI."""

import click
from pathlib import Path

from clx.cli.monitor.app import CLXMonitorApp


@cli.command()
@click.option(
    '--db-path',
    type=click.Path(exists=False, path_type=Path),
    help='Path to SQLite database (auto-detected if not specified)',
)
@click.option(
    '--refresh',
    type=click.IntRange(1, 10),
    default=2,
    help='Refresh interval in seconds (1-10, default: 2)',
)
@click.option(
    '--theme',
    type=click.Choice(['dark', 'light', 'auto'], case_sensitive=False),
    default='dark',
    help='Color theme',
)
@click.option(
    '--log-file',
    type=click.Path(path_type=Path),
    help='Log errors to file',
)
def monitor(db_path, refresh, theme, log_file):
    """Launch real-time monitoring TUI.

    Displays live worker status, job queue, and activity in an
    interactive terminal interface.

    Examples:

        clx monitor                         # Use default settings
        clx monitor --refresh=5             # Update every 5 seconds
        clx monitor --db-path=/data/clx_jobs.db  # Custom database
    """
    # Set up logging if requested
    if log_file:
        import logging
        logging.basicConfig(
            filename=str(log_file),
            level=logging.ERROR,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    # Auto-detect database path if not specified
    if not db_path:
        db_path = _auto_detect_db_path()

    if not db_path.exists():
        click.echo(f"Error: Database not found: {db_path}", err=True)
        click.echo("Run 'clx build course.yaml' to initialize the system.", err=True)
        raise SystemExit(2)

    # Launch TUI app
    app = CLXMonitorApp(
        db_path=db_path,
        refresh_interval=refresh,
        theme=theme,
    )

    try:
        app.run()
    except Exception as e:
        click.echo(f"Error running monitor: {e}", err=True)
        if log_file:
            click.echo(f"See {log_file} for details", err=True)
        raise SystemExit(1)


def _auto_detect_db_path() -> Path:
    """Auto-detect database path."""
    import os
    db_path = os.getenv("CLX_DB_PATH")
    if db_path:
        return Path(db_path)

    default_paths = [
        Path.cwd() / "clx_jobs.db",
        Path.cwd() / "jobs.db",
        Path.home() / ".clx" / "clx_jobs.db",
    ]

    for path in default_paths:
        if path.exists():
            return path

    return Path.cwd() / "clx_jobs.db"
```

## Performance Optimizations

1. **Efficient Database Queries**
   - Use indexes on frequently queried columns
   - Limit result sets (recent events to 100)
   - Use single connection with read-only mode

2. **Textual Rendering**
   - Only update changed widgets (Textual handles this)
   - Use Textual's diff-based rendering (automatic)
   - Avoid full re-renders on every update

3. **Docker Stats Caching**
   - Cache Docker stats for 2 seconds
   - Skip Docker stats if unavailable (fail fast)

4. **Memory Management**
   - Limit activity log buffer to 500 events
   - Clear old events when buffer full

## Testing Strategy

### Manual Testing

```bash
# Test with different refresh rates
clx monitor --refresh=1   # Fast updates
clx monitor --refresh=5   # Slow updates

# Test with different terminal sizes
# Resize terminal while running

# Test keyboard shortcuts
# Press q, r, p, ESC, Ctrl+C

# Test with no database
clx monitor --db-path=/nonexistent/db

# Test with empty database
# Create fresh database, no workers

# Test with active workers
# Start workers, submit jobs
```

### Integration Tests

```python
"""Integration tests for monitor app."""

import pytest
from pathlib import Path
from textual.pilot import Pilot

from clx.cli.monitor.app import CLXMonitorApp
from clx.infrastructure.database.job_queue import JobQueue


@pytest.mark.integration
async def test_monitor_app_launches(tmp_path):
    """Test monitor app launches successfully."""
    db_path = tmp_path / "test_jobs.db"
    job_queue = JobQueue(db_path)

    # Register a worker
    job_queue.register_worker(worker_type="notebook", execution_mode="direct")

    # Launch app
    app = CLXMonitorApp(db_path=db_path, refresh_interval=10)

    async with app.run_test() as pilot:
        # Check that widgets are present
        assert app.status_header is not None
        assert app.workers_panel is not None
        assert app.queue_panel is not None
        assert app.activity_panel is not None


@pytest.mark.integration
async def test_monitor_app_refresh(tmp_path):
    """Test monitor app refreshes data."""
    db_path = tmp_path / "test_jobs.db"
    job_queue = JobQueue(db_path)

    app = CLXMonitorApp(db_path=db_path, refresh_interval=1)

    async with app.run_test() as pilot:
        # Wait for initial render
        await pilot.pause()

        # Trigger manual refresh
        await pilot.press("r")
        await pilot.pause()

        # Should show notification
        # (Check would depend on Textual notification API)


@pytest.mark.integration
async def test_monitor_app_pause(tmp_path):
    """Test monitor app pause/resume."""
    db_path = tmp_path / "test_jobs.db"
    job_queue = JobQueue(db_path)

    app = CLXMonitorApp(db_path=db_path, refresh_interval=1)

    async with app.run_test() as pilot:
        # Pause
        await pilot.press("p")
        assert app.paused is True

        # Resume
        await pilot.press("p")
        assert app.paused is False


@pytest.mark.integration
async def test_monitor_app_quit(tmp_path):
    """Test monitor app quit actions."""
    db_path = tmp_path / "test_jobs.db"
    job_queue = JobQueue(db_path)

    app = CLXMonitorApp(db_path=db_path, refresh_interval=10)

    async with app.run_test() as pilot:
        # Quit with q
        await pilot.press("q")
        # App should exit (test framework handles this)
```

## Documentation

### User Guide

Add to `docs/user-guide/monitor-command.md`:

```markdown
# Real-Time Monitoring with clx monitor

The `clx monitor` command launches an interactive terminal UI for real-time system monitoring.

## Quick Start

```bash
clx monitor
```

This displays:
- System health status
- Worker status by type (idle, busy, hung)
- Job queue statistics
- Recent activity log

## Keyboard Controls

- `q` or `ESC`: Quit
- `r`: Force refresh now
- `p`: Pause/resume auto-refresh
- `↑`/`↓`: Scroll activity log (if needed)
- `Ctrl+C`: Quit

## Options

### Custom Database Path

```bash
clx monitor --db-path=/data/clx_jobs.db
```

### Refresh Interval

```bash
clx monitor --refresh=5  # Update every 5 seconds
```

Supported intervals: 1-10 seconds (default: 2)

### Theme

```bash
clx monitor --theme=light  # Light theme
clx monitor --theme=dark   # Dark theme (default)
clx monitor --theme=auto   # Auto-detect terminal
```

### Error Logging

```bash
clx monitor --log-file=/tmp/monitor.log
```

Logs errors to file instead of displaying them (prevents TUI corruption).

## Tips

- Use smaller terminals for compact view (minimum 80x24)
- Use pause (`p`) to freeze display for reading
- Press `r` to force immediate refresh
- Check log file if display becomes corrupted
```

## Implementation Checklist

- [ ] Create monitor module structure
- [ ] Implement DataProvider class
- [ ] Implement data models (WorkerInfo, QueueInfo, etc.)
- [ ] Implement StatusHeader widget
- [ ] Implement WorkersPanel widget
- [ ] Implement QueuePanel widget
- [ ] Implement ActivityPanel widget
- [ ] Implement main CLXMonitorApp
- [ ] Create CSS styling (monitor.tcss)
- [ ] Add monitor command to CLI
- [ ] Add formatters module
- [ ] Test with different terminal sizes
- [ ] Test keyboard controls
- [ ] Test pause/resume
- [ ] Test with no database
- [ ] Test with active workers
- [ ] Add integration tests
- [ ] Add user documentation
- [ ] Update pyproject.toml with TUI dependencies
- [ ] Test on Windows/macOS/Linux
- [ ] Test with Docker workers

## Future Enhancements

1. **Interactive Controls**: Click on workers to see details, stop workers
2. **Multiple Databases**: Monitor multiple CLX instances in tabs
3. **Alerts**: Sound/visual alerts for failures
4. **Export**: Export current view to file
5. **Filtering**: Filter activity log by job type, worker
6. **Charts**: ASCII charts for throughput/latency trends
7. **Help Panel**: Press `h` for detailed help overlay
