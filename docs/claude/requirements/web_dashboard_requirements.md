# Web-Based Dashboard Requirements

**Version**: 1.0
**Date**: 2025-11-15
**Status**: Draft

## Executive Summary

This document specifies requirements for a web-based dashboard that provides real-time monitoring of the CLX system through a browser interface. The dashboard will display live worker status, job queue activity, performance metrics, and historical trends, with configurable refresh intervals (1-5 seconds). A backend REST API server will provide data access, supporting both real-time polling and WebSocket streaming.

## Background

### Current State

The CLX system currently provides:
- SQLite database with comprehensive monitoring data
- Statistics APIs for querying system state
- `clx status` command for snapshot views (see cli_status_command_requirements.md)
- `clx monitor` TUI for terminal-based real-time monitoring (see tui_monitoring_app_requirements.md)

However, users cannot access monitoring data through a web browser. Current options are:
- CLI commands (text-only, no visualization)
- TUI application (terminal-only, no remote access)
- Direct database queries (requires technical knowledge)

### Pain Points

1. **No Remote Access**: Users can't monitor CLX from a different machine or browser
2. **No Visualization**: No charts, graphs, or visual representations of trends
3. **No Historical View**: Can't see job processing history over time
4. **No Multi-User Access**: Can't share monitoring view with team members
5. **No Mobile Support**: Can't check status from phone/tablet

### Use Cases

1. **Remote Monitoring**: User monitors course build from laptop while server runs elsewhere
2. **Team Collaboration**: Multiple developers share view of build server status
3. **Performance Analysis**: User analyzes job processing trends over hours/days
4. **Mobile Access**: User checks build progress from mobile device
5. **CI/CD Integration**: Build dashboard is embedded in CI/CD pipeline UI
6. **Production Ops**: Operations team monitors multiple CLX instances from central dashboard

## Requirements

### 1. Backend API Server

#### 1.1 Server Launch and Configuration

**REQ-1.1.1**: The system SHALL provide a `clx serve` command to launch the API server.

**REQ-1.1.2**: The server SHALL accept command-line options:
```bash
clx serve                              # Use default settings
clx serve --host=0.0.0.0               # Bind to all interfaces
clx serve --port=8080                  # Custom port (default: 8000)
clx serve --db-path=/path/to/db        # Custom database path
clx serve --no-browser                 # Don't auto-open browser
clx serve --cors-origin="*"            # Enable CORS for specific origins
clx serve --log-level=DEBUG            # Set log level
```

**REQ-1.1.3**: The server SHALL auto-open browser to dashboard URL on startup (unless `--no-browser`).

**REQ-1.1.4**: The server SHALL log startup information:
- Listening address and port
- Database path
- Dashboard URL

**REQ-1.1.5**: The server SHALL shut down gracefully on SIGINT/SIGTERM.

#### 1.2 REST API Endpoints

**REQ-1.2.1**: The API SHALL provide the following endpoints:

##### Health and Metadata
- `GET /api/health` - Server health check
- `GET /api/version` - CLX version and API version
- `GET /api/config` - Server configuration (sanitized)

##### System Status
- `GET /api/status` - Overall system status (like `clx status`)
- `GET /api/status/summary` - Minimal status for frequent polling

##### Workers
- `GET /api/workers` - List all workers with details
- `GET /api/workers/{worker_id}` - Get specific worker details
- `GET /api/workers/stats` - Worker statistics by type

##### Jobs
- `GET /api/jobs` - List jobs with pagination and filters
- `GET /api/jobs/{job_id}` - Get specific job details
- `GET /api/jobs/stats` - Job statistics (counts by status)
- `GET /api/jobs/recent` - Recent job activity (last N jobs)

##### Queue
- `GET /api/queue/stats` - Queue statistics
- `GET /api/queue/depth` - Current queue depth by type

##### Events
- `GET /api/events` - List worker events with pagination
- `GET /api/events/recent` - Recent events (last N events)

##### Metrics (Historical)
- `GET /api/metrics/throughput` - Job throughput over time
- `GET /api/metrics/latency` - Job processing latency over time
- `GET /api/metrics/workers` - Worker utilization over time

**REQ-1.2.2**: All endpoints SHALL return JSON responses.

**REQ-1.2.3**: All endpoints SHALL include appropriate HTTP status codes:
- 200: Success
- 400: Bad request (invalid parameters)
- 404: Resource not found
- 500: Server error
- 503: Service unavailable (database error)

**REQ-1.2.4**: Error responses SHALL include structured error information:
```json
{
  "error": "Database connection failed",
  "code": "DB_CONNECTION_ERROR",
  "details": "SQLite database locked",
  "timestamp": "2025-11-15T10:30:00Z"
}
```

**REQ-1.2.5**: The API SHALL support CORS headers for cross-origin requests (configurable).

**REQ-1.2.6**: The API SHALL support pagination for list endpoints:
```
GET /api/jobs?page=2&page_size=50
GET /api/events?offset=100&limit=50
```

**REQ-1.2.7**: The API SHALL support filtering and sorting:
```
GET /api/jobs?status=completed&worker_type=notebook&sort=-created_at
GET /api/events?event_type=worker_started&since=2025-11-15T00:00:00Z
```

#### 1.3 WebSocket API (Real-Time Streaming)

**REQ-1.3.1**: The server SHALL provide a WebSocket endpoint at `/ws`.

**REQ-1.3.2**: The WebSocket SHALL stream real-time updates for:
- Worker status changes
- Job status changes
- New events
- Queue depth changes

**REQ-1.3.3**: Clients SHALL subscribe to specific update types:
```json
// Client sends
{"subscribe": ["workers", "jobs", "events"]}

// Server sends
{"type": "worker_update", "data": {...}}
{"type": "job_completed", "data": {...}}
{"type": "event", "data": {...}}
```

**REQ-1.3.4**: The WebSocket SHALL support heartbeat/ping-pong to detect disconnections.

**REQ-1.3.5**: The server SHALL broadcast updates to all connected clients within 1 second of database change.

**REQ-1.3.6**: The server SHALL handle client disconnections gracefully (clean up subscriptions).

**REQ-1.3.7**: The WebSocket connection SHALL be optional; clients can use polling instead.

#### 1.4 Static File Serving

**REQ-1.4.1**: The server SHALL serve the dashboard frontend as static files.

**REQ-1.4.2**: The root URL (`/`) SHALL serve the dashboard HTML.

**REQ-1.4.3**: The server SHALL serve static assets (JS, CSS, images) efficiently with caching headers.

**REQ-1.4.4**: The server SHALL support gzip compression for text assets.

#### 1.5 Server Technology Stack

**REQ-1.5.1**: The server SHALL use a modern Python async web framework:
- **Option A**: FastAPI (recommended)
  - Pros: Modern, async, automatic OpenAPI docs, WebSocket support
  - Cons: Additional dependency
- **Option B**: Starlette (lightweight)
  - Pros: Minimal, async, WebSocket support
  - Cons: Less feature-rich
- **Option C**: Flask + Flask-SocketIO
  - Pros: Familiar, widely used
  - Cons: Not natively async, older design

**Recommendation**: Use FastAPI for modern async API with automatic docs.

**REQ-1.5.2**: The server SHALL use uvicorn or similar ASGI server.

### 2. Frontend Dashboard

#### 2.1 Dashboard Layout and Pages

**REQ-2.1.1**: The dashboard SHALL provide multiple pages/views:
- **Overview Page**: System status at a glance
- **Workers Page**: Detailed worker status and management
- **Jobs Page**: Job queue and history
- **Events Page**: Event log with filtering
- **Metrics Page**: Historical charts and trends
- **Settings Page**: Dashboard configuration

**REQ-2.1.2**: The dashboard SHALL use a responsive layout that works on:
- Desktop (1920x1080 and larger)
- Laptop (1366x768)
- Tablet (iPad, 768x1024)
- Mobile (phone, 375x667 and larger)

**REQ-2.1.3**: The dashboard SHALL include a navigation sidebar or top bar with:
- Page links
- System health indicator
- Last update timestamp
- Settings/theme toggle

#### 2.2 Overview Page

**REQ-2.2.1**: The overview page SHALL display:
- System health status card (healthy/warning/error)
- Worker summary cards (count by type and status)
- Queue statistics card (pending, processing, completed, failed)
- Recent activity feed (last 20 events)
- Quick metrics (throughput, avg latency)

**REQ-2.2.2**: Status cards SHALL use color coding:
- Green: Healthy, idle workers
- Blue: Busy workers, processing jobs
- Yellow: Warnings (long queue, stale data)
- Red: Errors (hung workers, failed jobs)

**REQ-2.2.3**: The overview page SHALL auto-refresh at configurable intervals (default: 2 seconds).

**REQ-2.2.4**: Workers SHALL be displayed with:
- Worker type icon/badge
- Status indicator (idle/busy/hung/dead)
- Current document (for busy workers)
- Elapsed time (for busy workers)

Example layout:
```
┌─────────────────────────────────────────────────────────┐
│ System Status: ✓ Healthy    Last Updated: 2s ago       │
├─────────────────────────────────────────────────────────┤
│ ┌─ Workers ─┐  ┌─ Queue ──┐  ┌─ Throughput ─┐         │
│ │ Notebook 2 │  │ Pending 5│  │ 28 jobs/min  │         │
│ │ PlantUML 1 │  │ Process 3│  │ Avg: 4.2s    │         │
│ │ DrawIO 0 ⚠│  │ Done 142 │  │ Failed: 1.4% │         │
│ └───────────┘  └──────────┘  └──────────────┘         │
├─────────────────────────────────────────────────────────┤
│ Recent Activity                                         │
│ • 10:30:15 ✓ Completed lecture-01.ipynb (45s)          │
│ • 10:30:10 ⚙ Started lecture-02.ipynb                   │
│ • 10:30:05 ✓ Completed architecture.puml (12s)         │
│ ...                                                     │
└─────────────────────────────────────────────────────────┘
```

#### 2.3 Workers Page

**REQ-2.3.1**: The workers page SHALL display a detailed table of all workers:
- Worker ID
- Worker type
- Status (idle/busy/hung/dead)
- Execution mode (direct/docker)
- Current job/document
- Elapsed time
- Jobs processed count
- Uptime
- CPU/Memory (Docker mode)
- Last heartbeat

**REQ-2.3.2**: The table SHALL support sorting by any column.

**REQ-2.3.3**: The table SHALL support filtering by:
- Worker type
- Status
- Execution mode

**REQ-2.3.4**: For each busy worker, the page SHALL show a progress bar (if progress available).

**REQ-2.3.5**: The page SHALL highlight workers with issues (hung, dead, high CPU).

**REQ-2.3.6**: The page SHALL include summary statistics at the top:
- Total workers by type
- Workers by status
- Average jobs per worker
- Average uptime

#### 2.4 Jobs Page

**REQ-2.4.1**: The jobs page SHALL display a paginated table of jobs:
- Job ID
- Job type (notebook, plantuml, drawio)
- Status (pending, processing, completed, failed)
- Document path/name
- Assigned worker
- Created timestamp
- Started timestamp (if started)
- Completed timestamp (if completed)
- Duration
- Error message (if failed)

**REQ-2.4.2**: The table SHALL support sorting by any column.

**REQ-2.4.3**: The table SHALL support filtering by:
- Status
- Job type
- Worker ID
- Date range

**REQ-2.4.4**: The page SHALL include a search box to search by document name.

**REQ-2.4.5**: Clicking a job row SHALL show detailed job information in a modal/panel.

**REQ-2.4.6**: The page SHALL include summary statistics:
- Total jobs by status
- Success rate
- Average duration by job type
- Failure reasons (top 5)

#### 2.5 Events Page

**REQ-2.5.1**: The events page SHALL display a chronological log of worker events:
- Timestamp
- Event type
- Worker ID
- Job ID (if applicable)
- Message/details
- Metadata

**REQ-2.5.2**: The page SHALL support filtering by:
- Event type
- Worker type
- Worker ID
- Date range

**REQ-2.5.3**: The page SHALL support real-time streaming of new events (WebSocket or polling).

**REQ-2.5.4**: Events SHALL be color-coded by type:
- Worker started: Blue
- Job completed: Green
- Job failed: Red
- Worker hung: Yellow
- Worker crashed: Red

**REQ-2.5.5**: The page SHALL support infinite scroll or pagination for large event logs.

#### 2.6 Metrics Page

**REQ-2.6.1**: The metrics page SHALL display historical charts:
- **Throughput Chart**: Jobs completed over time (line chart)
- **Latency Chart**: Average job duration over time (line chart)
- **Queue Depth Chart**: Queue size over time (area chart)
- **Worker Utilization Chart**: Worker busy/idle ratio over time (stacked bar chart)
- **Success Rate Chart**: Job success/failure rate over time (line chart)

**REQ-2.6.2**: Charts SHALL support time range selection:
- Last hour
- Last 6 hours
- Last 24 hours
- Last 7 days
- Custom range

**REQ-2.6.3**: Charts SHALL be interactive:
- Zoom in/out
- Pan
- Hover tooltips with exact values
- Legend toggle (show/hide series)

**REQ-2.6.4**: Charts SHALL support exporting to PNG/SVG.

**REQ-2.6.5**: The page SHALL include statistics summary cards:
- Peak throughput
- Average latency
- Total jobs processed
- Total runtime

**REQ-2.6.6**: Charts SHALL update automatically as new data arrives (configurable refresh).

#### 2.7 Settings Page

**REQ-2.7.1**: The settings page SHALL allow users to configure:
- Refresh interval (1-10 seconds)
- Theme (light, dark, auto)
- Time format (relative, absolute, both)
- Timezone display (local, UTC)
- Update mode (polling, WebSocket)
- Notification preferences (future)

**REQ-2.7.2**: Settings SHALL be persisted in browser localStorage.

**REQ-2.7.3**: The page SHALL include a "Reset to Defaults" button.

**REQ-2.7.4**: The page SHALL display current server configuration (read-only):
- Server version
- Database path
- Uptime

#### 2.8 Real-Time Updates

**REQ-2.8.1**: The dashboard SHALL support two update modes:
- **Polling Mode**: HTTP GET requests at configurable intervals
- **WebSocket Mode**: Real-time streaming via WebSocket

**REQ-2.8.2**: The dashboard SHALL auto-select update mode:
- Try WebSocket first
- Fall back to polling if WebSocket fails
- Allow manual override in settings

**REQ-2.8.3**: The dashboard SHALL display connection status:
- Connected (green indicator)
- Disconnected (red indicator)
- Reconnecting (yellow indicator)

**REQ-2.8.4**: The dashboard SHALL auto-reconnect on connection loss:
- Retry WebSocket connection with exponential backoff
- Fall back to polling after 3 failed attempts

**REQ-2.8.5**: The dashboard SHALL pause updates when tab is not visible (to save resources).

**REQ-2.8.6**: The dashboard SHALL resume updates when tab becomes visible.

#### 2.9 User Interface and Theming

**REQ-2.9.1**: The dashboard SHALL support light and dark themes.

**REQ-2.9.2**: The dashboard SHALL auto-detect system theme preference.

**REQ-2.9.3**: The dashboard SHALL use a modern UI component library:
- **Option A**: Material-UI (React) - Comprehensive, well-documented
- **Option B**: Ant Design (React) - Rich components, good charts
- **Option C**: Tailwind CSS + Headless UI - Lightweight, customizable
- **Option D**: Bootstrap 5 - Classic, widely known

**Recommendation**: Use Material-UI or Ant Design for rich components and charts.

**REQ-2.9.4**: The dashboard SHALL use consistent color scheme:
- Primary color: Blue (CLX brand)
- Success: Green
- Warning: Yellow/Orange
- Error: Red
- Neutral: Gray

**REQ-2.9.5**: The dashboard SHALL be accessible (WCAG AA compliant):
- Keyboard navigation support
- Screen reader friendly
- Sufficient color contrast
- Focus indicators

#### 2.10 Frontend Technology Stack

**REQ-2.10.1**: The frontend SHALL use a modern JavaScript framework:
- **Option A**: React (recommended)
  - Pros: Popular, rich ecosystem, component-based
  - Cons: Boilerplate, learning curve
- **Option B**: Vue.js
  - Pros: Simpler, progressive, good docs
  - Cons: Smaller ecosystem
- **Option C**: Svelte
  - Pros: Minimal, fast, reactive
  - Cons: Smaller ecosystem, newer

**Recommendation**: Use React with TypeScript for type safety and tooling.

**REQ-2.10.2**: The frontend SHALL use a charting library:
- **Option A**: Recharts (React-specific, declarative)
- **Option B**: Chart.js (popular, framework-agnostic)
- **Option C**: ECharts (powerful, feature-rich)

**Recommendation**: Use Recharts for React integration.

**REQ-2.10.3**: The frontend SHALL use a build tool:
- Vite (recommended for fast development)
- Create React App (simpler, more opinionated)
- Webpack (powerful, complex)

**REQ-2.10.4**: The frontend SHALL be optimized for production:
- Minified JavaScript and CSS
- Code splitting for faster loads
- Lazy loading for routes/components
- Service worker for offline support (future)

### 3. Integration with Existing Infrastructure

**REQ-3.1**: The server SHALL use existing `JobQueue` class for database queries.

**REQ-3.2**: The server SHALL use existing statistics APIs where possible.

**REQ-3.3**: The server SHALL respect `--db-path` option consistent with other commands.

**REQ-3.4**: The server SHALL use the same logging configuration as other CLX commands.

**REQ-3.5**: The dashboard SHALL NOT require changes to the existing database schema.

**REQ-3.6**: The server SHALL run independently of `clx build` (no coupling).

### 4. Performance Requirements

**REQ-4.1**: The API SHALL respond to requests within:
- Health check: < 50ms
- Status endpoints: < 200ms
- List endpoints (paginated): < 500ms
- Metrics endpoints: < 1 second

**REQ-4.2**: The server SHALL support at least 10 concurrent WebSocket connections.

**REQ-4.3**: The server SHALL handle database locking gracefully:
- Retry queries with exponential backoff
- Return cached data if database is locked
- Return 503 status if database is unavailable

**REQ-4.4**: The frontend SHALL load initial page within:
- First paint: < 1 second
- Interactive: < 2 seconds
- Full load: < 3 seconds (on fast connection)

**REQ-4.5**: The frontend SHALL minimize re-renders:
- Only update changed components
- Use memoization for expensive computations
- Virtualize long lists (jobs, events)

**REQ-4.6**: The server SHALL cache frequently accessed data:
- Worker stats: 1 second cache
- Queue stats: 1 second cache
- Metrics: 10 second cache

### 5. Security Requirements

**REQ-5.1**: The server SHALL bind to localhost by default (127.0.0.1).

**REQ-5.2**: The server SHALL require explicit `--host=0.0.0.0` to bind to all interfaces.

**REQ-5.3**: The server SHALL support authentication (future enhancement):
- API key authentication
- Session-based authentication
- JWT tokens

**REQ-5.4**: The server SHALL validate all input parameters:
- Reject invalid pagination parameters
- Reject SQL injection attempts
- Sanitize file paths

**REQ-5.5**: The server SHALL use HTTPS in production (future):
- Support TLS certificates
- Auto-redirect HTTP to HTTPS
- HSTS headers

**REQ-5.6**: The server SHALL implement rate limiting (future):
- Limit API requests per IP
- Limit WebSocket connections per IP

**REQ-5.7**: The dashboard SHALL NOT expose sensitive information:
- No database credentials
- No file system paths (sanitize)
- No internal IPs

### 6. Deployment and Installation

**REQ-6.1**: The server SHALL be installable with optional dependencies:
```bash
pip install -e ".[web]"
```

**REQ-6.2**: The frontend SHALL be bundled with the Python package (no separate build required).

**REQ-6.3**: The server SHALL support Docker deployment:
- Provide Dockerfile
- Expose port 8000
- Mount database directory as volume

**REQ-6.4**: The server SHALL support production deployment with:
- Gunicorn or uvicorn with workers
- Reverse proxy (nginx/Apache) support
- Systemd service file example

### 7. Documentation

**REQ-7.1**: The project SHALL include API documentation:
- OpenAPI/Swagger UI at `/docs`
- ReDoc at `/redoc`
- Endpoint descriptions and examples

**REQ-7.2**: The project SHALL include user documentation:
- Dashboard user guide
- Server deployment guide
- Troubleshooting guide

**REQ-7.3**: The dashboard SHALL include inline help:
- Tooltips for UI elements
- Help panel with keyboard shortcuts
- FAQ section in settings

### 8. Error Handling and Resilience

**REQ-8.1**: If the database is not found, the server SHALL:
- Return 503 status for API requests
- Display "Database Not Found" page in dashboard
- Suggest commands to initialize system

**REQ-8.2**: If the database connection is lost, the server SHALL:
- Attempt to reconnect with exponential backoff
- Serve cached data if available
- Return 503 status if no cached data

**REQ-8.3**: If WebSocket connection fails, the dashboard SHALL:
- Display connection error message
- Fall back to polling mode
- Offer manual reconnect button

**REQ-8.4**: If API request fails, the dashboard SHALL:
- Display error message with details
- Offer retry button
- Log error to browser console

**REQ-8.5**: The server SHALL log all errors:
- To stdout/stderr in development
- To log file in production
- Include timestamps, request IDs, stack traces

## Non-Requirements

**NR-1**: This system does NOT aim to modify system state (no start/stop workers, cancel jobs) in initial version.

**NR-2**: This system does NOT aim to support multi-tenant deployments (single CLX instance per dashboard).

**NR-3**: This system does NOT aim to support historical data beyond what's in the database (no separate time-series DB).

**NR-4**: This system does NOT aim to support alerting/notifications in initial version.

**NR-5**: This system does NOT aim to support user management/permissions in initial version.

## Success Criteria

1. **Remote Access**: Users can monitor CLX from any device with a browser.

2. **Real-Time Updates**: Dashboard updates within 2 seconds of system changes.

3. **Visual Clarity**: Users can understand system state at a glance with charts and colors.

4. **Historical Analysis**: Users can analyze job processing trends over hours/days.

5. **Performance**: Dashboard loads quickly (< 3s) and responds smoothly.

6. **Mobile Friendly**: Dashboard works on tablets and phones (responsive).

7. **Easy Deployment**: Server starts with single command, no complex setup.

## Open Questions

1. **Authentication**: Should we add authentication in initial version?
   - **Recommendation**: No, add in v2 (simpler initial scope)

2. **Job Control**: Should users be able to cancel jobs from dashboard?
   - **Recommendation**: Future enhancement (read-only initially)

3. **Multi-Instance**: Should dashboard support monitoring multiple CLX instances?
   - **Recommendation**: No, one instance per dashboard (simpler)

4. **Data Retention**: Should we add automatic database cleanup?
   - **Recommendation**: Yes, add settings for auto-cleanup (e.g., delete jobs > 7 days)

5. **Alerts**: Should dashboard support browser notifications?
   - **Recommendation**: Future enhancement, not initial version

6. **Export**: Should users be able to export data (CSV, JSON)?
   - **Recommendation**: Yes, add export buttons for tables and charts

## Dependencies

**Backend:**
- FastAPI ~= 0.104.0
- uvicorn[standard] ~= 0.24.0
- websockets ~= 12.0
- python-multipart (for file uploads, future)

**Frontend:**
- React ~= 18.2.0
- TypeScript ~= 5.0.0
- Material-UI ~= 5.14.0 (or Ant Design)
- Recharts ~= 2.10.0
- Axios ~= 1.6.0 (for HTTP requests)
- React Router ~= 6.20.0 (for navigation)

**Build Tools:**
- Vite ~= 5.0.0
- TypeScript compiler

## Implementation Approach

### Phase 1: Backend API (5-6 days)
- Set up FastAPI project structure
- Implement REST endpoints (status, workers, jobs, queue)
- Implement WebSocket endpoint
- Add database query functions
- Add error handling and logging
- Write API tests

### Phase 2: Frontend Foundation (4-5 days)
- Set up React + TypeScript + Vite project
- Implement routing and navigation
- Create layout components (sidebar, header)
- Implement theme switching
- Set up API client with Axios
- Implement WebSocket client

### Phase 3: Dashboard Pages (6-7 days)
- Implement Overview page with cards and activity feed
- Implement Workers page with table and filters
- Implement Jobs page with table and search
- Implement Events page with log viewer
- Add real-time updates to all pages

### Phase 4: Metrics and Charts (3-4 days)
- Implement Metrics page with Recharts
- Create throughput chart
- Create latency chart
- Create queue depth chart
- Create worker utilization chart
- Add time range selection

### Phase 5: Settings and Polish (2-3 days)
- Implement Settings page
- Add localStorage persistence
- Add error boundaries
- Improve loading states
- Add keyboard shortcuts
- Responsive design testing

### Phase 6: Integration and Testing (3-4 days)
- Integrate frontend with backend
- End-to-end testing
- Performance optimization
- Documentation
- Deployment guide

### Phase 7: Packaging and Deployment (2-3 days)
- Bundle frontend with Python package
- Create Dockerfile
- Create systemd service file
- Write deployment documentation
- Final testing

**Total Estimate**: 25-32 days

## Risks

1. **Complexity**: Full-stack implementation requires frontend and backend expertise
   - Mitigation: Use well-documented frameworks, allocate learning time

2. **WebSocket Scaling**: Multiple clients might overload server
   - Mitigation: Implement connection limits, fallback to polling

3. **Database Locking**: Frequent queries might lock SQLite database
   - Mitigation: Use read-only connections, caching, retry logic

4. **Bundle Size**: React + Material-UI might create large bundles
   - Mitigation: Code splitting, lazy loading, tree shaking

5. **Browser Compatibility**: Dashboard might not work on older browsers
   - Mitigation: Use Babel for transpilation, test on major browsers

6. **Real-Time Performance**: Updates might lag with many jobs/workers
   - Mitigation: Optimize queries, use pagination, limit event buffer

## Appendix A: API Endpoint Reference

### GET /api/status
Returns overall system status.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-11-15T10:30:00Z",
  "database": {
    "path": "/home/user/clx/clx_jobs.db",
    "accessible": true,
    "size_bytes": 102400,
    "last_modified": "2025-11-15T10:29:45Z"
  },
  "workers": {
    "notebook": {"total": 2, "idle": 1, "busy": 1, "hung": 0, "dead": 0},
    "plantuml": {"total": 1, "idle": 1, "busy": 0, "hung": 0, "dead": 0},
    "drawio": {"total": 0, "idle": 0, "busy": 0, "hung": 0, "dead": 0}
  },
  "queue": {
    "pending": 5,
    "processing": 3,
    "completed_last_hour": 142,
    "failed_last_hour": 2,
    "oldest_pending_seconds": 135
  }
}
```

### GET /api/workers
Returns list of all workers with details.

**Query Parameters:**
- `worker_type`: Filter by type (notebook, plantuml, drawio)
- `status`: Filter by status (idle, busy, hung, dead)
- `execution_mode`: Filter by mode (direct, docker)

**Response:**
```json
{
  "workers": [
    {
      "worker_id": "nb-abc123",
      "worker_type": "notebook",
      "status": "busy",
      "execution_mode": "direct",
      "current_job_id": "job-xyz789",
      "current_document": "lecture-01.ipynb",
      "started_at": "2025-11-15T10:28:00Z",
      "elapsed_seconds": 135,
      "jobs_processed": 42,
      "uptime_seconds": 3600,
      "last_heartbeat": "2025-11-15T10:29:55Z",
      "cpu_percent": 85.2,
      "memory_mb": 256
    },
    ...
  ],
  "total": 3,
  "page": 1,
  "page_size": 50
}
```

### GET /api/jobs
Returns paginated list of jobs.

**Query Parameters:**
- `status`: Filter by status (pending, processing, completed, failed)
- `job_type`: Filter by type (notebook, plantuml, drawio)
- `worker_id`: Filter by worker ID
- `page`: Page number (default: 1)
- `page_size`: Items per page (default: 50, max: 200)
- `sort`: Sort field (e.g., `-created_at` for descending)

**Response:**
```json
{
  "jobs": [
    {
      "job_id": "job-xyz789",
      "job_type": "notebook",
      "status": "processing",
      "document_path": "/course/notebooks/lecture-01.ipynb",
      "worker_id": "nb-abc123",
      "created_at": "2025-11-15T10:25:00Z",
      "started_at": "2025-11-15T10:28:00Z",
      "completed_at": null,
      "duration_seconds": null,
      "error_message": null
    },
    ...
  ],
  "total": 150,
  "page": 1,
  "page_size": 50,
  "total_pages": 3
}
```

### GET /api/metrics/throughput
Returns job throughput over time.

**Query Parameters:**
- `from`: Start timestamp (ISO 8601)
- `to`: End timestamp (ISO 8601)
- `interval`: Aggregation interval (1m, 5m, 15m, 1h, 1d)

**Response:**
```json
{
  "data": [
    {"timestamp": "2025-11-15T10:00:00Z", "jobs_completed": 28},
    {"timestamp": "2025-11-15T10:05:00Z", "jobs_completed": 32},
    {"timestamp": "2025-11-15T10:10:00Z", "jobs_completed": 25},
    ...
  ],
  "interval": "5m",
  "total_jobs": 142
}
```

### WebSocket /ws

**Client Subscribe:**
```json
{"action": "subscribe", "channels": ["workers", "jobs", "events"]}
```

**Server Messages:**
```json
// Worker update
{"type": "worker_update", "data": {"worker_id": "nb-abc123", "status": "busy", ...}}

// Job completed
{"type": "job_completed", "data": {"job_id": "job-xyz", "duration": 45, ...}}

// New event
{"type": "event", "data": {"event_type": "worker_started", "worker_id": "nb-abc123", ...}}

// Heartbeat
{"type": "ping"}
```

## Appendix B: Frontend Component Structure

```
src/
├── App.tsx                 # Main app component
├── main.tsx                # Entry point
├── api/
│   ├── client.ts           # Axios API client
│   └── websocket.ts        # WebSocket client
├── components/
│   ├── Layout/
│   │   ├── AppLayout.tsx   # Main layout with sidebar
│   │   ├── Sidebar.tsx     # Navigation sidebar
│   │   └── Header.tsx      # Top header with status
│   ├── Cards/
│   │   ├── StatusCard.tsx  # Status summary card
│   │   ├── WorkerCard.tsx  # Worker status card
│   │   └── QueueCard.tsx   # Queue stats card
│   ├── Tables/
│   │   ├── WorkersTable.tsx
│   │   ├── JobsTable.tsx
│   │   └── EventsTable.tsx
│   └── Charts/
│       ├── ThroughputChart.tsx
│       ├── LatencyChart.tsx
│       └── QueueDepthChart.tsx
├── pages/
│   ├── Overview.tsx
│   ├── Workers.tsx
│   ├── Jobs.tsx
│   ├── Events.tsx
│   ├── Metrics.tsx
│   └── Settings.tsx
├── hooks/
│   ├── useApi.ts           # API data fetching hook
│   ├── useWebSocket.ts     # WebSocket hook
│   └── useTheme.ts         # Theme management hook
├── types/
│   └── api.ts              # TypeScript API types
└── utils/
    ├── formatters.ts       # Date/time formatters
    └── constants.ts        # Constants
```

## Appendix C: Deployment Examples

### Development
```bash
# Terminal 1: Start CLX workers
clx start-services

# Terminal 2: Start dashboard server
clx serve --reload
```

### Production with Systemd
```bash
# /etc/systemd/system/clx-dashboard.service
[Unit]
Description=CLX Dashboard Server
After=network.target

[Service]
Type=simple
User=clx
WorkingDirectory=/opt/clx
ExecStart=/opt/clx/venv/bin/clx serve --host=0.0.0.0 --port=8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### Production with Docker
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install -e ".[web]"

EXPOSE 8000

CMD ["clx", "serve", "--host=0.0.0.0", "--port=8000"]
```

```bash
docker run -d \
  -p 8000:8000 \
  -v /path/to/clx_jobs.db:/data/clx_jobs.db \
  -e DB_PATH=/data/clx_jobs.db \
  clx-dashboard:latest
```

### Production with Nginx Reverse Proxy
```nginx
server {
    listen 80;
    server_name clx.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws {
        proxy_pass http://127.0.0.1:8000/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```
