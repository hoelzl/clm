# CLI Status Command Requirements

**Version**: 1.0
**Date**: 2025-11-15
**Status**: Draft

## Executive Summary

This document specifies requirements for a new `clx status` command that provides a snapshot view of the CLX system state. The command will show worker availability, status, and current activity, enabling users to quickly understand the system's operational state without running a full build or watching logs.

## Background

### Current State

The CLX system currently provides monitoring infrastructure with:
- SQLite database tracking jobs, workers, and events
- Worker health monitoring with heartbeat tracking
- Statistics APIs (`get_worker_stats()`, `get_queue_statistics()`)
- Diagnostic scripts (`check_jobs.py`, `diagnose_workers.py`)

However, there is no integrated CLI command for users to check system status. Users must either:
- Run Python diagnostic scripts directly
- Query the SQLite database manually
- Check docker-compose logs
- Write custom queries

### Pain Points

1. **No Quick Status Check**: Users can't easily see if workers are ready before running builds
2. **Scattered Information**: Worker status, job queue, and activity are in different places
3. **No Integration**: Diagnostic scripts are separate from the main `clx` CLI
4. **Poor Discoverability**: Users don't know how to check system health

## Requirements

### 1. Command Interface

**REQ-1.1**: The system SHALL provide a `clx status` command accessible from the main CLI.

**REQ-1.2**: The command SHALL accept optional arguments for filtering and formatting:
```bash
clx status                          # Show all status information
clx status --workers                # Show only worker information
clx status --jobs                   # Show only job queue information
clx status --format=json            # Output in JSON format
clx status --format=table           # Output in table format (default)
clx status --db-path=/path/to/db    # Use custom database path
```

**REQ-1.3**: The command SHALL return appropriate exit codes:
- `0`: System healthy (workers available, no critical issues)
- `1`: Warning state (some workers unavailable, long queue)
- `2`: Error state (no workers, database inaccessible)

**REQ-1.4**: The command SHALL execute quickly (< 1 second) for typical status checks.

### 2. Worker Information Display

**REQ-2.1**: The status output SHALL show for each worker type (notebook, plantuml, drawio):
- Number of workers registered
- Number of workers in each state (idle, busy, hung, dead)
- Execution mode (direct or docker)
- For busy workers: Current document being processed

**REQ-2.2**: Worker information SHALL be grouped by worker type.

**REQ-2.3**: The output SHALL indicate if no workers are registered for a type.

**REQ-2.4**: For each busy worker, the output SHALL show:
- Worker ID
- Current job ID
- Document path/name being processed
- Time since job started (elapsed time)

**REQ-2.5**: The output SHALL highlight workers with issues:
- Hung workers (processing > 5 minutes)
- Dead workers (no heartbeat)
- Stale workers (old registration)

Example output:
```
Workers Status:
  Notebook Workers: 2 total
    ✓ 1 idle (direct mode)
    ⚙ 1 busy (direct mode)
       - Worker nb-1234: Processing lecture-01.ipynb (45s)

  PlantUML Workers: 1 total
    ✓ 1 idle (docker mode)

  DrawIO Workers: 0 total
    ⚠ No workers registered
```

### 3. Job Queue Information Display

**REQ-3.1**: The status output SHALL show job queue statistics:
- Total jobs in queue by status (pending, processing, completed, failed)
- Oldest pending job age
- Average job processing time (recent)

**REQ-3.2**: The output SHALL warn if:
- Queue has many pending jobs (> 10)
- Oldest pending job is old (> 5 minutes)
- Recent jobs are failing (> 20% failure rate)

Example output:
```
Job Queue Status:
  Pending: 3 jobs (oldest: 12s)
  Processing: 2 jobs
  Completed: 145 jobs (last hour)
  Failed: 2 jobs (last hour)

  ⚠ Warning: 3 jobs pending, no idle workers available
```

### 4. System Health Indicators

**REQ-4.1**: The status output SHALL include an overall health indicator:
- `✓ System Healthy`: Workers available, queue processing normally
- `⚠ System Warning`: Some issues (workers unavailable, long queue)
- `✗ System Error`: Critical issues (no workers, database error)

**REQ-4.2**: The output SHALL show database connectivity status.

**REQ-4.3**: The output SHALL show database file size and last update time.

**REQ-4.4**: If the database is not found, the output SHALL provide helpful guidance:
```
✗ Database not found: /path/to/clx_jobs.db

Run 'clx build course.yaml' to initialize the system, or
Run 'clx start-services' to start persistent workers.
```

### 5. Output Formats

**REQ-5.1**: The default output format SHALL be human-readable table/text format.

**REQ-5.2**: The system SHALL support JSON output format for scripting:
```json
{
  "status": "healthy",
  "timestamp": "2025-11-15T10:30:00Z",
  "database": {
    "path": "/path/to/clx_jobs.db",
    "accessible": true,
    "size_bytes": 102400,
    "last_modified": "2025-11-15T10:29:45Z"
  },
  "workers": {
    "notebook": {
      "total": 2,
      "idle": 1,
      "busy": 1,
      "hung": 0,
      "dead": 0,
      "busy_workers": [
        {
          "worker_id": "nb-1234",
          "job_id": "job-5678",
          "document": "lecture-01.ipynb",
          "elapsed_seconds": 45
        }
      ]
    },
    "plantuml": {"total": 1, "idle": 1, "busy": 0},
    "drawio": {"total": 0, "idle": 0, "busy": 0}
  },
  "queue": {
    "pending": 3,
    "processing": 2,
    "completed_last_hour": 145,
    "failed_last_hour": 2,
    "oldest_pending_seconds": 12
  }
}
```

**REQ-5.3**: The system SHALL support compact output format for scripting:
```bash
$ clx status --compact
healthy: 2 notebook (1 idle, 1 busy), 1 plantuml (1 idle), 0 drawio | queue: 3 pending, 2 processing
```

### 6. Performance Requirements

**REQ-6.1**: The command SHALL query the database efficiently using existing statistics APIs.

**REQ-6.2**: The command SHALL NOT scan all jobs/events; only query summary statistics.

**REQ-6.3**: The command SHALL cache results for 1 second to support rapid repeated calls.

**REQ-6.4**: The command SHALL timeout after 5 seconds if database is locked or slow.

### 7. Error Handling

**REQ-7.1**: If the database is locked, the command SHALL:
- Retry up to 3 times with 100ms delay
- Display clear error message if all retries fail
- Return exit code 2

**REQ-7.2**: If the database schema is outdated, the command SHALL:
- Detect schema version mismatch
- Suggest running migrations or rebuilding
- Return exit code 2

**REQ-7.3**: If worker data is stale (> 5 minutes), the command SHALL:
- Display warning about stale data
- Suggest running `clx workers cleanup`
- Return exit code 1

### 8. Integration with Existing Infrastructure

**REQ-8.1**: The command SHALL use existing `JobQueue` class for database queries.

**REQ-8.2**: The command SHALL use existing statistics methods:
- `JobQueue.get_worker_stats()`
- `JobQueue.get_queue_statistics()`
- `JobQueue.get_jobs_by_status()`

**REQ-8.3**: The command SHALL respect the `--db-path` option used by other commands.

**REQ-8.4**: The command SHALL use the same logging configuration as other CLI commands.

### 9. Documentation

**REQ-9.1**: The command SHALL include help text accessible via `clx status --help`.

**REQ-9.2**: The help text SHALL include examples of common usage patterns.

**REQ-9.3**: The user guide SHALL be updated with `clx status` documentation.

**REQ-9.4**: The command SHALL be mentioned in troubleshooting documentation.

## Non-Requirements

**NR-1**: This command does NOT aim to provide real-time updates (use TUI app for that).

**NR-2**: This command does NOT aim to show historical trends (use web dashboard for that).

**NR-3**: This command does NOT aim to modify system state (no start/stop workers).

**NR-4**: This command does NOT aim to show detailed job history (use database queries).

## Success Criteria

1. **Quick Health Check**: Users can run `clx status` to verify system is ready in < 1 second.

2. **Clear Worker Status**: Users can see at a glance which workers are available and what they're doing.

3. **Queue Visibility**: Users can see if jobs are pending and why (no workers, busy workers).

4. **Scriptable**: Automation scripts can check status programmatically via JSON output.

5. **Integrated**: Command feels like a natural part of the `clx` CLI.

## Open Questions

1. **Timestamp Display**: Should we show timestamps in local time or UTC?
   - **Recommendation**: Local time for human readability, UTC in JSON format

2. **Worker Details**: How much detail should we show for each worker?
   - Option A: Just counts (idle/busy/hung)
   - Option B: Show document names for busy workers
   - Option C: Show full job details
   - **Recommendation**: Option B (document names)

3. **Color Output**: Should we use colored output for better visibility?
   - **Recommendation**: Yes, with `--no-color` option to disable

4. **Auto-refresh**: Should `clx status` support `--watch` mode for continuous updates?
   - **Recommendation**: No, use TUI app for that (simpler scope)

5. **Database Path**: Should we auto-detect database path from config or require explicit path?
   - **Recommendation**: Auto-detect with `--db-path` override

## Dependencies

- Existing `JobQueue` class and statistics methods
- Existing `WorkerEventLogger` for worker status
- Click CLI framework
- Rich library for formatted table output (optional, nice-to-have)

## Implementation Approach

### Phase 1: Core Status Query (1 day)
- Implement `clx status` command skeleton
- Query worker statistics from database
- Query job queue statistics
- Display basic text output

### Phase 2: Enhanced Display (1 day)
- Add table formatting with Rich library
- Add busy worker details (document names)
- Add health indicators and warnings
- Add color coding for status

### Phase 3: Output Formats (1 day)
- Implement JSON output format
- Implement compact output format
- Add command-line options (--workers, --jobs, --format)

### Phase 4: Error Handling & Polish (1 day)
- Add database error handling
- Add stale data detection
- Add exit code logic
- Add comprehensive help text

### Phase 5: Testing & Documentation (1 day)
- Unit tests for status command
- Integration tests with real database
- Update user documentation
- Update troubleshooting guide

**Total Estimate**: 5 days

## Risks

1. **Database Locking**: Status command might compete with workers for database access
   - Mitigation: Use read-only queries, short timeouts, retry logic

2. **Performance**: Querying statistics might be slow with large databases
   - Mitigation: Use indexed queries, cache results, optimize schema

3. **Stale Data**: Worker status might be outdated if heartbeats are slow
   - Mitigation: Display last update time, warn about stale data

4. **Display Complexity**: Too much information might overwhelm users
   - Mitigation: Provide filtering options, sensible defaults, progressive disclosure

## Appendix A: Example Output Formats

### Default Table Format

```
CLX System Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Overall Status: ✓ System Healthy
Database: /home/user/clx/clx_jobs.db (100 KB, updated 2s ago)

Workers by Type
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Notebook Workers: 2 total (direct mode)
  ✓ 1 idle
  ⚙ 1 busy
     Worker nb-abc123: lecture-01.ipynb (45s elapsed)

PlantUML Workers: 1 total (docker mode)
  ✓ 1 idle

DrawIO Workers: 0 total
  ⚠ No workers registered

Job Queue Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Pending:     3 jobs (oldest: 12s)
  Processing:  2 jobs
  Completed:   145 jobs (last hour)
  Failed:      2 jobs (last hour)

⚠ Warning: 3 jobs pending with no idle workers available
```

### JSON Format

See REQ-5.2 above.

### Compact Format

```
healthy: 2 notebook (1 idle, 1 busy), 1 plantuml (1 idle), 0 drawio | queue: 3 pending, 2 processing
```

### Workers-Only Output

```bash
$ clx status --workers

Notebook Workers: 2 total (direct mode)
  ✓ 1 idle
  ⚙ 1 busy - Worker nb-abc123: lecture-01.ipynb (45s)

PlantUML Workers: 1 total (docker mode)
  ✓ 1 idle

DrawIO Workers: 0 total
  ⚠ No workers registered
```

### Jobs-Only Output

```bash
$ clx status --jobs

Job Queue Status:
  Pending:     3 jobs (oldest: 12s)
  Processing:  2 jobs
  Completed:   145 jobs (last hour)
  Failed:      2 jobs (last hour)

⚠ Warning: 3 jobs pending with no idle workers available
```

## Appendix B: Exit Codes

| Exit Code | Status | Condition |
|-----------|--------|-----------|
| 0 | Healthy | Workers available, queue processing normally, no critical issues |
| 1 | Warning | Some workers unavailable, long queue, stale data, or minor issues |
| 2 | Error | No workers registered, database inaccessible, schema mismatch |

## Appendix C: Command-Line Options Reference

```bash
clx status [OPTIONS]

Options:
  --workers              Show only worker information
  --jobs                 Show only job queue information
  --format TEXT          Output format: table (default), json, compact
  --db-path PATH         Path to SQLite database [default: auto-detect]
  --no-color             Disable colored output
  --verbose, -v          Show detailed information
  --help                 Show this message and exit

Examples:
  clx status                      # Show full system status
  clx status --workers            # Show only workers
  clx status --format=json        # JSON output for scripts
  clx status --db-path=/data/clx_jobs.db  # Custom database
```
