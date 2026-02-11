# Web-Based Dashboard Design

**Version**: 1.0
**Date**: 2025-11-15
**Purpose**: Technical design for implementing `clm serve` command and web dashboard for remote real-time monitoring

## Overview

This document provides the detailed technical design for implementing the CLM web dashboard, which consists of:

1. **FastAPI Backend Server**: REST API + WebSocket for real-time data
2. **React Frontend**: Single-page application with interactive charts and tables
3. **Integration**: Seamless connection between frontend and backend

### Design Goals

1. **Remote Access**: Monitor CLM from any device with a browser
2. **Real-Time Updates**: WebSocket streaming with polling fallback
3. **Rich Visualization**: Interactive charts for historical trends
4. **Mobile Responsive**: Works on desktop, tablet, and mobile
5. **Easy Deployment**: Single command to start, bundled assets
6. **Performant**: Fast initial load, efficient updates

## Technology Stack

### Backend

- **FastAPI** 0.104+: Modern async web framework
- **Uvicorn**: ASGI server
- **python-multipart**: Form/file upload support (future)
- **WebSockets**: Built into FastAPI/Starlette

### Frontend

- **React** 18+: UI library
- **TypeScript** 5+: Type safety
- **Material-UI** (MUI) 5+: Component library
- **Recharts** 2+: Charting library
- **Axios**: HTTP client
- **React Router** 6+: Navigation
- **Vite**: Build tool

## Architecture

### Overall System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Browser Client                          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │         React Frontend (SPA)                         │   │
│  │  ┌─────────┐ ┌──────────┐ ┌───────────┐            │   │
│  │  │Overview │ │ Workers  │ │ Metrics   │ ...        │   │
│  │  │  Page   │ │   Page   │ │   Page    │            │   │
│  │  └────┬────┘ └────┬─────┘ └─────┬─────┘            │   │
│  │       └───────────┼─────────────┘                   │   │
│  │                   │                                  │   │
│  │         ┌─────────▼────────────┐                    │   │
│  │         │   API Client (Axios) │                    │   │
│  │         │   WebSocket Client   │                    │   │
│  │         └─────────┬────────────┘                    │   │
│  └───────────────────┼───────────────────────────────────  │
└────────────────────┼────────────────────────────────────────┘
                     │
                HTTP │ REST / WebSocket
                     │
┌────────────────────▼────────────────────────────────────────┐
│              FastAPI Backend Server                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              API Router                              │   │
│  │  ┌──────────┐ ┌─────────┐ ┌──────────┐             │   │
│  │  │ /api/    │ │/api/    │ │ /api/    │ ...         │   │
│  │  │ status   │ │workers  │ │ metrics  │             │   │
│  │  └────┬─────┘ └────┬────┘ └────┬─────┘             │   │
│  │       └────────────┼───────────┘                    │   │
│  │                    │                                 │   │
│  │         ┌──────────▼──────────┐                     │   │
│  │         │    Service Layer    │                     │   │
│  │         │  (Business Logic)   │                     │   │
│  │         └──────────┬──────────┘                     │   │
│  │                    │                                 │   │
│  │         ┌──────────▼──────────┐                     │   │
│  │         │  Database Access    │                     │   │
│  │         │   (JobQueue)        │                     │   │
│  │         └──────────┬──────────┘                     │   │
│  └────────────────────┼─────────────────────────────────   │
│                       │                                     │
│  ┌────────────────────▼─────────────────────────────────┐  │
│  │         WebSocket Manager                            │  │
│  │  - Client connections                                │  │
│  │  - Broadcast updates                                 │  │
│  │  - Subscription management                           │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          │
                ┌─────────▼────────┐
                │   SQLite DB      │
                │   clm_jobs.db    │
                └──────────────────┘
```

## Backend Design

### Module Structure

```
src/clm/web/
├── __init__.py
├── server/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app initialization
│   ├── config.py                # Configuration
│   ├── dependencies.py          # FastAPI dependencies
│   └── lifespan.py              # App startup/shutdown
│
├── api/
│   ├── __init__.py
│   ├── router.py                # Main API router
│   ├── endpoints/
│   │   ├── __init__.py
│   │   ├── health.py            # Health/version endpoints
│   │   ├── status.py            # Status endpoints
│   │   ├── workers.py           # Worker endpoints
│   │   ├── jobs.py              # Job endpoints
│   │   ├── events.py            # Event endpoints
│   │   └── metrics.py           # Metrics endpoints
│   └── websocket.py             # WebSocket endpoint
│
├── services/
│   ├── __init__.py
│   ├── worker_service.py        # Worker business logic
│   ├── job_service.py           # Job business logic
│   ├── metrics_service.py       # Metrics aggregation
│   └── cache_service.py         # Caching layer
│
├── models/
│   ├── __init__.py
│   ├── api_models.py            # Pydantic API models
│   └── responses.py             # Common response models
│
└── static/                      # Bundled frontend assets
    ├── index.html
    ├── assets/
    │   ├── index.[hash].js
    │   └── index.[hash].css
    └── favicon.ico
```

### Core Backend Classes

#### 1. FastAPI Application Setup

```python
"""FastAPI application setup."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from clm.web.api.router import api_router
from clm.web.api.websocket import websocket_manager
from clm.web.server.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan context manager."""
    # Startup
    print(f"Starting CLM Dashboard Server...")
    print(f"Database: {app.state.settings.db_path}")
    print(f"Listening on: http://{app.state.settings.host}:{app.state.settings.port}")

    # Initialize WebSocket manager
    app.state.websocket_manager = websocket_manager

    yield

    # Shutdown
    print("Shutting down CLM Dashboard Server...")


def create_app(settings: Settings) -> FastAPI:
    """Create and configure FastAPI application.

    Args:
        settings: Application settings

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="CLM Dashboard API",
        description="Real-time monitoring API for CLM system",
        version="0.3.0",
        lifespan=lifespan,
    )

    # Store settings in app state
    app.state.settings = settings

    # Configure CORS
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Include API router
    app.include_router(api_router, prefix="/api")

    # Serve static frontend files
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/")
        async def serve_frontend():
            """Serve frontend index.html."""
            return FileResponse(static_dir / "index.html")

        @app.get("/favicon.ico")
        async def serve_favicon():
            """Serve favicon."""
            return FileResponse(static_dir / "favicon.ico")

    return app
```

#### 2. Configuration

```python
"""Server configuration."""

from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    # Server settings
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False

    # Database
    db_path: Path = Path("clm_jobs.db")

    # CORS
    cors_origins: List[str] = ["*"]

    # Cache
    cache_ttl_seconds: int = 1  # Cache API responses for 1 second

    # WebSocket
    ws_heartbeat_interval: int = 30  # Ping clients every 30 seconds

    class Config:
        env_prefix = "CLM_"
```

#### 3. API Models

```python
"""Pydantic models for API requests/responses."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


# Health & Metadata
class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    version: str
    database: str


class VersionResponse(BaseModel):
    """Version information response."""
    clm_version: str
    api_version: str


# Worker Models
class BusyWorkerInfo(BaseModel):
    """Information about a busy worker."""
    worker_id: str
    job_id: str
    document: str
    elapsed_seconds: int


class WorkerTypeStats(BaseModel):
    """Statistics for a worker type."""
    total: int
    idle: int
    busy: int
    hung: int
    dead: int
    execution_mode: Optional[str] = None
    busy_workers: List[BusyWorkerInfo] = Field(default_factory=list)


class WorkerDetail(BaseModel):
    """Detailed worker information."""
    worker_id: str
    worker_type: str
    status: str
    execution_mode: str
    current_job_id: Optional[str] = None
    current_document: Optional[str] = None
    started_at: datetime
    elapsed_seconds: Optional[int] = None
    jobs_processed: int
    uptime_seconds: int
    last_heartbeat: Optional[datetime] = None
    cpu_percent: Optional[float] = None
    memory_mb: Optional[int] = None


class WorkersResponse(BaseModel):
    """Response with list of workers."""
    workers: List[WorkerDetail]
    total: int
    page: int = 1
    page_size: int = 50


# Job Models
class JobSummary(BaseModel):
    """Job summary information."""
    job_id: str
    job_type: str
    status: str
    document_path: str
    worker_id: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None


class JobsResponse(BaseModel):
    """Response with list of jobs."""
    jobs: List[JobSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class QueueStats(BaseModel):
    """Queue statistics."""
    pending: int
    processing: int
    completed_last_hour: int
    failed_last_hour: int
    oldest_pending_seconds: Optional[int] = None


# Status Models
class DatabaseInfo(BaseModel):
    """Database information."""
    path: str
    accessible: bool
    size_bytes: int
    last_modified: datetime


class StatusResponse(BaseModel):
    """Overall system status."""
    status: str  # healthy, warning, error
    timestamp: datetime
    database: DatabaseInfo
    workers: dict[str, WorkerTypeStats]
    queue: QueueStats
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# Metrics Models
class ThroughputDataPoint(BaseModel):
    """Throughput data point."""
    timestamp: datetime
    jobs_completed: int


class ThroughputResponse(BaseModel):
    """Throughput over time."""
    data: List[ThroughputDataPoint]
    interval: str
    total_jobs: int


class LatencyDataPoint(BaseModel):
    """Latency data point."""
    timestamp: datetime
    avg_duration_seconds: float
    p50_seconds: float
    p95_seconds: float


class LatencyResponse(BaseModel):
    """Latency metrics over time."""
    data: List[LatencyDataPoint]
    interval: str


# Event Models
class EventSummary(BaseModel):
    """Event summary."""
    id: int
    event_type: str
    worker_id: Optional[int] = None
    worker_type: str
    message: str
    created_at: datetime


class EventsResponse(BaseModel):
    """Response with list of events."""
    events: List[EventSummary]
    total: int
    page: int
    page_size: int


# Error Response
class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    code: str
    details: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
```

#### 4. API Endpoints

##### Status Endpoint

```python
"""Status API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from pathlib import Path

from clm.web.models.api_models import StatusResponse, DatabaseInfo, WorkerTypeStats, QueueStats
from clm.web.services.worker_service import WorkerService
from clm.web.services.job_service import JobService
from clm.web.server.dependencies import get_db_path, get_worker_service, get_job_service

router = APIRouter()


@router.get("/status", response_model=StatusResponse)
async def get_status(
    worker_service: WorkerService = Depends(get_worker_service),
    job_service: JobService = Depends(get_job_service),
    db_path: Path = Depends(get_db_path),
):
    """Get overall system status.

    Returns complete system status including worker stats, queue stats,
    and health indicators.
    """
    # Get database info
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="Database not found")

    stat = db_path.stat()
    db_info = DatabaseInfo(
        path=str(db_path),
        accessible=True,
        size_bytes=stat.st_size,
        last_modified=datetime.fromtimestamp(stat.st_mtime),
    )

    # Get worker stats
    worker_stats = await worker_service.get_worker_stats()

    # Get queue stats
    queue_stats = await job_service.get_queue_stats()

    # Determine health
    health, warnings, errors = _determine_health(worker_stats, queue_stats)

    return StatusResponse(
        status=health,
        timestamp=datetime.now(),
        database=db_info,
        workers=worker_stats,
        queue=queue_stats,
        warnings=warnings,
        errors=errors,
    )


def _determine_health(
    workers: dict[str, WorkerTypeStats],
    queue: QueueStats
) -> tuple[str, list[str], list[str]]:
    """Determine system health."""
    warnings = []
    errors = []

    # Check for workers
    total_workers = sum(stats.total for stats in workers.values())
    if total_workers == 0:
        errors.append("No workers registered")
        return "error", warnings, errors

    # Check for hung workers
    hung_workers = sum(stats.hung for stats in workers.values())
    if hung_workers > 0:
        warnings.append(f"{hung_workers} worker(s) hung")

    # Check queue
    if queue.pending > 10:
        idle_workers = sum(stats.idle for stats in workers.values())
        if idle_workers == 0:
            warnings.append(f"{queue.pending} jobs pending with no idle workers")

    if errors:
        return "error", warnings, errors
    elif warnings:
        return "warning", warnings, errors
    else:
        return "healthy", warnings, errors
```

##### Workers Endpoint

```python
"""Workers API endpoints."""

from fastapi import APIRouter, Depends, Query
from typing import Optional

from clm.web.models.api_models import WorkersResponse, WorkerDetail
from clm.web.services.worker_service import WorkerService
from clm.web.server.dependencies import get_worker_service

router = APIRouter()


@router.get("/workers", response_model=WorkersResponse)
async def list_workers(
    worker_type: Optional[str] = Query(None, description="Filter by worker type"),
    status: Optional[str] = Query(None, description="Filter by status"),
    execution_mode: Optional[str] = Query(None, description="Filter by execution mode"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    worker_service: WorkerService = Depends(get_worker_service),
):
    """List workers with optional filtering and pagination.

    Returns detailed information about all registered workers.
    """
    workers = await worker_service.get_workers(
        worker_type=worker_type,
        status=status,
        execution_mode=execution_mode,
        page=page,
        page_size=page_size,
    )

    return workers


@router.get("/workers/{worker_id}", response_model=WorkerDetail)
async def get_worker(
    worker_id: str,
    worker_service: WorkerService = Depends(get_worker_service),
):
    """Get detailed information about a specific worker."""
    worker = await worker_service.get_worker(worker_id)

    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    return worker


@router.get("/workers/stats", response_model=dict[str, WorkerTypeStats])
async def get_worker_stats(
    worker_service: WorkerService = Depends(get_worker_service),
):
    """Get worker statistics grouped by type."""
    return await worker_service.get_worker_stats()
```

#### 5. WebSocket Implementation

```python
"""WebSocket endpoint for real-time updates."""

import asyncio
import json
import logging
from typing import Set
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manage WebSocket connections and broadcast updates."""

    def __init__(self):
        """Initialize WebSocket manager."""
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: dict[WebSocket, Set[str]] = {}

    async def connect(self, websocket: WebSocket):
        """Accept new WebSocket connection.

        Args:
            websocket: WebSocket connection
        """
        await websocket.accept()
        self.active_connections.add(websocket)
        self.subscriptions[websocket] = set()
        logger.info(f"WebSocket client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection.

        Args:
            websocket: WebSocket connection
        """
        self.active_connections.discard(websocket)
        self.subscriptions.pop(websocket, None)
        logger.info(f"WebSocket client disconnected. Total: {len(self.active_connections)}")

    async def subscribe(self, websocket: WebSocket, channels: list[str]):
        """Subscribe connection to channels.

        Args:
            websocket: WebSocket connection
            channels: List of channel names (workers, jobs, events)
        """
        if websocket in self.subscriptions:
            self.subscriptions[websocket].update(channels)
            logger.debug(f"Client subscribed to: {channels}")

    async def broadcast(self, message: dict, channel: str = None):
        """Broadcast message to subscribed clients.

        Args:
            message: Message to broadcast
            channel: Optional channel filter
        """
        disconnected = set()

        for connection in self.active_connections:
            # Check if client is subscribed to this channel
            if channel and channel not in self.subscriptions.get(connection, set()):
                continue

            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to client: {e}")
                disconnected.add(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def send_heartbeat(self):
        """Send periodic heartbeat to all clients."""
        while True:
            await asyncio.sleep(30)  # Every 30 seconds

            if self.active_connections:
                await self.broadcast({"type": "ping"})


# Global WebSocket manager instance
websocket_manager = WebSocketManager()


# WebSocket endpoint
from fastapi import APIRouter

ws_router = APIRouter()


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates.

    Clients can subscribe to channels: workers, jobs, events
    """
    await websocket_manager.connect(websocket)

    try:
        while True:
            # Receive messages from client
            data = await websocket.receive_json()

            # Handle subscription
            if data.get("action") == "subscribe":
                channels = data.get("channels", [])
                await websocket_manager.subscribe(websocket, channels)
                await websocket.send_json({"type": "subscribed", "channels": channels})

            # Handle ping
            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        websocket_manager.disconnect(websocket)
```

#### 6. Service Layer

```python
"""Worker service - business logic for worker operations."""

from pathlib import Path
from datetime import datetime
from typing import Optional, List

from clm.infrastructure.database.job_queue import JobQueue
from clm.web.models.api_models import WorkerDetail, WorkersResponse, WorkerTypeStats, BusyWorkerInfo


class WorkerService:
    """Service for worker operations."""

    def __init__(self, db_path: Path):
        """Initialize worker service.

        Args:
            db_path: Path to database
        """
        self.db_path = db_path
        self.job_queue = JobQueue(db_path)

    async def get_workers(
        self,
        worker_type: Optional[str] = None,
        status: Optional[str] = None,
        execution_mode: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> WorkersResponse:
        """Get list of workers with filtering.

        Args:
            worker_type: Filter by type
            status: Filter by status
            execution_mode: Filter by execution mode
            page: Page number
            page_size: Items per page

        Returns:
            WorkersResponse with paginated workers
        """
        # Build query
        conditions = []
        params = []

        if worker_type:
            conditions.append("worker_type = ?")
            params.append(worker_type)

        if status:
            conditions.append("status = ?")
            params.append(status)

        if execution_mode:
            conditions.append("execution_mode = ?")
            params.append(execution_mode)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Get total count
        conn = self.job_queue._get_conn()
        cursor = conn.execute(
            f"SELECT COUNT(*) FROM workers WHERE {where_clause}",
            params
        )
        total = cursor.fetchone()[0]

        # Get paginated results
        offset = (page - 1) * page_size
        cursor = conn.execute(
            f"""
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
            WHERE {where_clause}
            ORDER BY w.id
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset]
        )

        workers = []
        for row in cursor.fetchall():
            created_at = datetime.fromisoformat(row[7])
            uptime = int((datetime.now() - created_at).total_seconds())

            last_heartbeat = None
            if row[6]:
                last_heartbeat = datetime.fromisoformat(row[6])

            workers.append(
                WorkerDetail(
                    worker_id=row[1],
                    worker_type=row[2],
                    status=row[3],
                    execution_mode=row[4] or "unknown",
                    current_job_id=row[8],
                    current_document=row[9],
                    started_at=created_at,
                    elapsed_seconds=row[10],
                    jobs_processed=row[5] or 0,
                    uptime_seconds=uptime,
                    last_heartbeat=last_heartbeat,
                )
            )

        return WorkersResponse(
            workers=workers,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_worker(self, worker_id: str) -> Optional[WorkerDetail]:
        """Get specific worker by ID.

        Args:
            worker_id: Worker ID

        Returns:
            WorkerDetail or None if not found
        """
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
            WHERE w.worker_id = ?
            """,
            (worker_id,)
        )

        row = cursor.fetchone()
        if not row:
            return None

        created_at = datetime.fromisoformat(row[7])
        uptime = int((datetime.now() - created_at).total_seconds())

        last_heartbeat = None
        if row[6]:
            last_heartbeat = datetime.fromisoformat(row[6])

        return WorkerDetail(
            worker_id=row[1],
            worker_type=row[2],
            status=row[3],
            execution_mode=row[4] or "unknown",
            current_job_id=row[8],
            current_document=row[9],
            started_at=created_at,
            elapsed_seconds=row[10],
            jobs_processed=row[5] or 0,
            uptime_seconds=uptime,
            last_heartbeat=last_heartbeat,
        )

    async def get_worker_stats(self) -> dict[str, WorkerTypeStats]:
        """Get worker statistics by type.

        Returns:
            Dictionary mapping worker type to stats
        """
        conn = self.job_queue._get_conn()

        result = {}

        for worker_type in ['notebook', 'plantuml', 'drawio']:
            # Get counts by status
            cursor = conn.execute(
                """
                SELECT status, COUNT(*)
                FROM workers
                WHERE worker_type = ?
                GROUP BY status
                """,
                (worker_type,)
            )

            status_counts = {row[0]: row[1] for row in cursor.fetchall()}
            total = sum(status_counts.values())

            # Get busy workers
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
                """,
                (worker_type,)
            )

            busy_workers = [
                BusyWorkerInfo(
                    worker_id=row[0],
                    job_id=row[1],
                    document=row[2],
                    elapsed_seconds=row[3] or 0,
                )
                for row in cursor.fetchall()
            ]

            # Get execution mode
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
            execution_mode = modes[0] if len(modes) == 1 else ("mixed" if modes else None)

            result[worker_type] = WorkerTypeStats(
                total=total,
                idle=status_counts.get('idle', 0),
                busy=status_counts.get('busy', 0),
                hung=status_counts.get('hung', 0),
                dead=status_counts.get('dead', 0),
                execution_mode=execution_mode,
                busy_workers=busy_workers,
            )

        return result
```

### CLI Integration

```python
"""Add serve command to CLI."""

import click
from pathlib import Path
import webbrowser
import uvicorn

from clm.web.server.main import create_app
from clm.web.server.config import Settings


@cli.command()
@click.option(
    '--host',
    default='127.0.0.1',
    help='Host to bind to (default: 127.0.0.1, use 0.0.0.0 for all interfaces)',
)
@click.option(
    '--port',
    type=int,
    default=8000,
    help='Port to bind to (default: 8000)',
)
@click.option(
    '--db-path',
    type=click.Path(exists=False, path_type=Path),
    help='Path to SQLite database (auto-detected if not specified)',
)
@click.option(
    '--no-browser',
    is_flag=True,
    help='Do not auto-open browser',
)
@click.option(
    '--reload',
    is_flag=True,
    help='Enable auto-reload for development',
)
@click.option(
    '--cors-origin',
    multiple=True,
    help='CORS allowed origins (can specify multiple times)',
)
def serve(host, port, db_path, no_browser, reload, cors_origin):
    """Start web dashboard server.

    Launches FastAPI server with React dashboard for remote monitoring.

    Examples:

        clm serve                           # Start on localhost:8000
        clm serve --host=0.0.0.0 --port=8080  # Bind to all interfaces
        clm serve --db-path=/data/clm_jobs.db  # Custom database
    """
    # Auto-detect database if not specified
    if not db_path:
        db_path = _auto_detect_db_path()

    if not db_path.exists():
        click.echo(f"Error: Database not found: {db_path}", err=True)
        click.echo("Run 'clm build course.yaml' to initialize the system.", err=True)
        raise SystemExit(2)

    # Create settings
    settings = Settings(
        host=host,
        port=port,
        db_path=db_path,
        reload=reload,
        cors_origins=list(cors_origin) if cors_origin else ["*"],
    )

    # Create app
    app = create_app(settings)

    # Open browser
    if not no_browser:
        url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}"
        webbrowser.open(url)

    # Run server
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
```

## Frontend Design

### Project Structure

```
frontend/
├── package.json
├── tsconfig.json
├── vite.config.ts
├── index.html
├── src/
│   ├── main.tsx                 # Entry point
│   ├── App.tsx                  # Main app component
│   ├── api/
│   │   ├── client.ts            # Axios client
│   │   ├── websocket.ts         # WebSocket client
│   │   └── types.ts             # API type definitions
│   ├── components/
│   │   ├── Layout/
│   │   │   ├── AppLayout.tsx    # Main layout
│   │   │   ├── Sidebar.tsx      # Navigation
│   │   │   └── Header.tsx       # Top bar
│   │   ├── Cards/
│   │   │   ├── StatusCard.tsx
│   │   │   ├── WorkerCard.tsx
│   │   │   └── QueueCard.tsx
│   │   ├── Tables/
│   │   │   ├── WorkersTable.tsx
│   │   │   ├── JobsTable.tsx
│   │   │   └── EventsTable.tsx
│   │   └── Charts/
│   │       ├── ThroughputChart.tsx
│   │       ├── LatencyChart.tsx
│   │       └── QueueDepthChart.tsx
│   ├── pages/
│   │   ├── Overview.tsx
│   │   ├── Workers.tsx
│   │   ├── Jobs.tsx
│   │   ├── Events.tsx
│   │   ├── Metrics.tsx
│   │   └── Settings.tsx
│   ├── hooks/
│   │   ├── useApi.ts            # API fetching hook
│   │   ├── useWebSocket.ts      # WebSocket hook
│   │   └── useSettings.ts       # Settings hook
│   ├── contexts/
│   │   └── SettingsContext.tsx  # Settings provider
│   ├── utils/
│   │   ├── formatters.ts        # Date/time formatters
│   │   └── constants.ts         # Constants
│   └── theme.ts                 # MUI theme
```

### Key Frontend Components

#### API Client

```typescript
// src/api/client.ts
import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// API functions
export const api = {
  // Status
  getStatus: () => apiClient.get('/status'),

  // Workers
  getWorkers: (params?: WorkerFilters) =>
    apiClient.get('/workers', { params }),

  getWorker: (workerId: string) =>
    apiClient.get(`/workers/${workerId}`),

  // Jobs
  getJobs: (params?: JobFilters) =>
    apiClient.get('/jobs', { params }),

  // Metrics
  getThroughput: (params: MetricsParams) =>
    apiClient.get('/metrics/throughput', { params }),

  getLatency: (params: MetricsParams) =>
    apiClient.get('/metrics/latency', { params }),
};
```

#### WebSocket Hook

```typescript
// src/hooks/useWebSocket.ts
import { useEffect, useRef, useState } from 'react';

interface UseWebSocketOptions {
  url: string;
  channels: string[];
  onMessage: (data: any) => void;
  enabled?: boolean;
}

export function useWebSocket({
  url,
  channels,
  onMessage,
  enabled = true,
}: UseWebSocketOptions) {
  const ws = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!enabled) return;

    // Connect WebSocket
    ws.current = new WebSocket(url);

    ws.current.onopen = () => {
      console.log('WebSocket connected');
      setConnected(true);
      setError(null);

      // Subscribe to channels
      ws.current?.send(JSON.stringify({
        action: 'subscribe',
        channels,
      }));
    };

    ws.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      onMessage(data);
    };

    ws.current.onerror = (event) => {
      console.error('WebSocket error:', event);
      setError(new Error('WebSocket connection error'));
    };

    ws.current.onclose = () => {
      console.log('WebSocket disconnected');
      setConnected(false);
    };

    // Cleanup
    return () => {
      ws.current?.close();
    };
  }, [url, channels, enabled]);

  return { connected, error };
}
```

#### Overview Page

```typescript
// src/pages/Overview.tsx
import React, { useEffect, useState } from 'react';
import { Grid, Card, CardContent, Typography } from '@mui/material';
import { api } from '../api/client';
import { StatusCard } from '../components/Cards/StatusCard';
import { WorkerCard } from '../components/Cards/WorkerCard';
import { QueueCard } from '../components/Cards/QueueCard';

export function Overview() {
  const [status, setStatus] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Fetch initial data
    fetchStatus();

    // Poll every 2 seconds
    const interval = setInterval(fetchStatus, 2000);

    return () => clearInterval(interval);
  }, []);

  const fetchStatus = async () => {
    try {
      const response = await api.getStatus();
      setStatus(response.data);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching status:', error);
      setLoading(false);
    }
  };

  if (loading) return <div>Loading...</div>;

  return (
    <Grid container spacing={3}>
      <Grid item xs={12}>
        <StatusCard status={status} />
      </Grid>

      <Grid item xs={12} md={4}>
        <WorkerCard
          type="notebook"
          stats={status?.workers?.notebook}
        />
      </Grid>

      <Grid item xs={12} md={4}>
        <WorkerCard
          type="plantuml"
          stats={status?.workers?.plantuml}
        />
      </Grid>

      <Grid item xs={12} md={4}>
        <WorkerCard
          type="drawio"
          stats={status?.workers?.drawio}
        />
      </Grid>

      <Grid item xs={12}>
        <QueueCard queue={status?.queue} />
      </Grid>
    </Grid>
  );
}
```

## Build and Deployment

### Frontend Build Process

```bash
# Install dependencies
cd frontend
npm install

# Development
npm run dev  # Vite dev server on port 5173

# Production build
npm run build  # Output to frontend/dist
```

### Package Integration

After building frontend, copy assets to Python package:

```bash
# Copy built assets to Python package
cp -r frontend/dist/* src/clm/web/static/
```

### Docker Deployment

```dockerfile
# Dockerfile for dashboard
FROM python:3.11-slim

WORKDIR /app

# Copy Python package
COPY . /app

# Install CLM with web extras
RUN pip install -e ".[web]"

# Expose port
EXPOSE 8000

# Run server
CMD ["clm", "serve", "--host=0.0.0.0", "--port=8000"]
```

## Implementation Checklist

### Backend
- [ ] Create web module structure
- [ ] Implement FastAPI application
- [ ] Implement configuration
- [ ] Implement API models (Pydantic)
- [ ] Implement status endpoint
- [ ] Implement workers endpoints
- [ ] Implement jobs endpoints
- [ ] Implement events endpoints
- [ ] Implement metrics endpoints
- [ ] Implement WebSocket endpoint
- [ ] Implement WebSocket manager
- [ ] Implement worker service
- [ ] Implement job service
- [ ] Implement metrics service
- [ ] Add serve command to CLI
- [ ] Add tests for API endpoints
- [ ] Add WebSocket tests

### Frontend
- [ ] Set up React + TypeScript + Vite project
- [ ] Configure Material-UI theme
- [ ] Implement API client
- [ ] Implement WebSocket client
- [ ] Implement Layout components
- [ ] Implement Overview page
- [ ] Implement Workers page
- [ ] Implement Jobs page
- [ ] Implement Events page
- [ ] Implement Metrics page with charts
- [ ] Implement Settings page
- [ ] Add routing with React Router
- [ ] Add responsive design
- [ ] Add error boundaries
- [ ] Build production bundle
- [ ] Integrate with Python package

### Integration
- [ ] Test API endpoints
- [ ] Test WebSocket streaming
- [ ] Test polling fallback
- [ ] Test CORS configuration
- [ ] Test static file serving
- [ ] Test on mobile devices
- [ ] Performance testing
- [ ] Documentation

## Future Enhancements

1. **Authentication**: JWT-based authentication
2. **Authorization**: Role-based access control
3. **Worker Control**: Start/stop workers from UI
4. **Job Control**: Cancel jobs, retry failed jobs
5. **Alerts**: Email/Slack notifications for failures
6. **Multi-Instance**: Monitor multiple CLM deployments
7. **Data Export**: Export charts/tables to CSV/PDF
8. **Dark/Light Theme**: Automatic theme switching
9. **Internationalization**: Multi-language support
10. **Prometheus Integration**: Export metrics for Grafana
