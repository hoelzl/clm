# TUI Real-Time Monitoring Application Requirements

**Version**: 1.0
**Date**: 2025-11-15
**Status**: Draft

## Executive Summary

This document specifies requirements for a Terminal User Interface (TUI) application that provides real-time monitoring of the CLM system. The application will display live worker status, job queue activity, and performance metrics in a text-based interactive interface, updating at configurable intervals (1-5 seconds).

## Background

### Current State

The CLM system currently provides:
- SQLite database with comprehensive monitoring data
- Statistics APIs for querying system state
- `clm status` command for snapshot views (see cli_status_command_requirements.md)
- Diagnostic scripts for troubleshooting

However, users cannot observe the system in real-time as it processes jobs. Current options are:
- Watch log files (`tail -f`)
- Poll `clm status` repeatedly
- Query database manually in a loop
- Monitor docker-compose logs

None of these provide a unified, interactive, real-time view.

### Pain Points

1. **No Real-Time Visibility**: Users can't watch jobs being processed in real-time
2. **Log Overload**: Log files contain too much detail for quick monitoring
3. **Poll Inefficiency**: Repeatedly running `clm status` is inefficient
4. **No Interactivity**: Can't filter, sort, or focus on specific information
5. **Poor User Experience**: No visual feedback during long-running builds

### Use Cases

1. **Development Monitoring**: Developer watches system while testing new notebooks
2. **Build Progress**: User monitors course conversion progress in real-time
3. **Performance Tuning**: User observes worker load to optimize worker count
4. **Troubleshooting**: User identifies slow/hung workers during investigation
5. **Production Monitoring**: Server admin watches system health on remote server

## Requirements

### 1. Application Launch and Lifecycle

**REQ-1.1**: The system SHALL provide a `clm monitor` command to launch the TUI application.

**REQ-1.2**: The application SHALL accept command-line options:
```bash
clm monitor                          # Use default settings
clm monitor --db-path=/path/to/db    # Custom database path
clm monitor --refresh=2              # Update every 2 seconds (default: 2)
clm monitor --theme=dark             # Color theme: dark, light, or auto
clm monitor --log-file=/path/to/log  # Log TUI errors to file
```

**REQ-1.3**: The application SHALL run in full-screen terminal mode using available terminal size.

**REQ-1.4**: The application SHALL gracefully handle terminal resize events.

**REQ-1.5**: The application SHALL exit cleanly on:
- `q` or `Q` key press
- `Ctrl+C` signal
- `ESC` key press

**REQ-1.6**: The application SHALL restore terminal state on exit (no leftover formatting).

### 2. Screen Layout and Panels

**REQ-2.1**: The application SHALL divide the screen into multiple panels:
- **Header Panel**: System status summary (1-2 lines)
- **Workers Panel**: Worker status by type (expandable)
- **Queue Panel**: Job queue statistics
- **Activity Panel**: Recent job activity log (scrollable)
- **Footer Panel**: Help text and keyboard shortcuts (1 line)

**REQ-2.2**: The layout SHALL adapt to terminal size:
- Minimum supported: 80x24 characters
- Optimal: 120x40 characters
- Panels SHALL stack vertically on narrow terminals
- Panels SHALL arrange side-by-side on wide terminals

**REQ-2.3**: Panel sizes SHALL be adjustable (future enhancement) or proportional:
- Header: Fixed 2 lines
- Workers: 30-40% of remaining space
- Queue: 15-20% of remaining space
- Activity: 40-50% of remaining space
- Footer: Fixed 1 line

### 3. Header Panel - System Overview

**REQ-3.1**: The header panel SHALL display:
- Application title and version
- Overall system health indicator (✓ Healthy, ⚠ Warning, ✗ Error)
- Current timestamp
- Database path (truncated if needed)
- Refresh interval

**REQ-3.2**: The header SHALL use color coding:
- Green: System healthy
- Yellow: System warning
- Red: System error
- Gray: Disconnected/loading

Example:
```
CLM Monitor v0.3.0 | ✓ System Healthy | 2025-11-15 10:30:15 | DB: ~/clm/jobs.db | Refresh: 2s
```

### 4. Workers Panel - Real-Time Worker Status

**REQ-4.1**: The workers panel SHALL display for each worker type:
- Worker type name (Notebook, PlantUML, DrawIO)
- Total count of workers
- Count by status (idle, busy, hung, dead)
- Execution mode (direct/docker)

**REQ-4.2**: For each busy worker, the panel SHALL show:
- Worker ID (truncated if needed)
- Current document being processed
- Elapsed time for current job (MM:SS or HH:MM:SS)
- Progress indicator (if available)
- CPU usage (Docker mode only, optional)
- Memory usage (Docker mode only, optional)

**REQ-4.3**: The panel SHALL highlight workers with issues:
- **Hung workers** (> 5 minutes): Red background or ⚠ symbol
- **High CPU** (> 90%): Yellow highlight
- **Dead workers**: Strikethrough or ✗ symbol

**REQ-4.4**: Workers SHALL be sorted by:
- Primary: Worker type (notebook, plantuml, drawio)
- Secondary: Status (busy before idle)
- Tertiary: Elapsed time (longest first)

Example:
```
┌─ Workers ─────────────────────────────────────────────┐
│ Notebook (2 workers, direct mode)                     │
│   ⚙ nb-abc123  lecture-01.ipynb           02:15  90% │
│   ✓ nb-def456  [idle]                                 │
│                                                        │
│ PlantUML (1 worker, docker mode)                      │
│   ⚙ pu-xyz789  diagrams/architecture.puml 00:45  45% │
│                                                        │
│ DrawIO (0 workers)                                    │
│   ⚠ No workers registered                             │
└────────────────────────────────────────────────────────┘
```

### 5. Queue Panel - Job Queue Statistics

**REQ-5.1**: The queue panel SHALL display:
- Total jobs by status (pending, processing, completed, failed)
- Oldest pending job age
- Average job processing time (recent window, e.g., last 10 jobs)
- Job throughput (jobs/minute, last 5 minutes)
- Queue depth trend (increasing, stable, decreasing)

**REQ-5.2**: The panel SHALL use visual indicators:
- Progress bars for job distribution (pending vs completed)
- Trend arrows (↑ increasing, → stable, ↓ decreasing)
- Warning symbols for issues

**REQ-5.3**: The panel SHALL highlight warnings:
- Many pending jobs (> 10): Yellow highlight
- Old pending jobs (> 5 minutes): Red highlight
- High failure rate (> 20%): Red highlight

Example:
```
┌─ Job Queue ───────────────────────────────────────────┐
│ Pending:    5 jobs  (oldest: 02:15) ⚠                 │
│ Processing: 3 jobs  [███░░░░░░░░░░░]                  │
│ Completed:  142 jobs (last hour)                      │
│ Failed:     2 jobs   (1.4% failure rate)              │
│                                                        │
│ Throughput: 28 jobs/min → (stable)                    │
│ Avg Time:   4.2s per job                              │
└────────────────────────────────────────────────────────┘
```

### 6. Activity Panel - Recent Job Activity Log

**REQ-6.1**: The activity panel SHALL display recent job events in chronological order (newest first or oldest first, user configurable).

**REQ-6.2**: Each event entry SHALL show:
- Timestamp (relative or absolute, configurable)
- Event type (job started, completed, failed, worker assigned)
- Job ID or correlation ID
- Document name
- Duration (for completed jobs)
- Status indicator (icon or color)

**REQ-6.3**: The panel SHALL support scrolling:
- Up/Down arrow keys to scroll
- Page Up/Page Down for fast scrolling
- Home/End keys for top/bottom
- Mouse wheel support (if available)

**REQ-6.4**: The panel SHALL buffer recent events (last 100-500 events).

**REQ-6.5**: Event types SHALL be color-coded:
- Job started: Blue
- Job completed: Green
- Job failed: Red
- Worker assigned: Cyan
- Worker idle: Gray

**REQ-6.6**: The panel SHALL support filtering (future enhancement):
- Press `f` to filter by job type
- Press `/` to search by document name

Example:
```
┌─ Recent Activity ─────────────────────────────────────┐
│ 10:30:15 ✓ Completed  lecture-01.ipynb       (45s)   │
│ 10:30:10 ⚙ Started    lecture-02.ipynb                │
│ 10:30:05 ✓ Completed  diagrams/arch.puml     (12s)   │
│ 10:30:00 ✗ Failed     broken.ipynb           (3s)    │
│ 10:29:55 ⚙ Started    lecture-03.ipynb                │
│ 10:29:50 → Assigned   nb-abc123 → lecture-01.ipynb   │
│ ...                                                    │
│ [↑↓ Scroll | Home/End | q Quit]                       │
└────────────────────────────────────────────────────────┘
```

### 7. Footer Panel - Help and Shortcuts

**REQ-7.1**: The footer panel SHALL display keyboard shortcuts:
- `q` - Quit application
- `r` - Force refresh now
- `p` - Pause/resume auto-refresh
- `↑↓` - Scroll activity log
- `f` - Filter view (future)
- `h` - Toggle help panel (future)

**REQ-7.2**: The footer SHALL indicate current mode:
- Normal mode: Show shortcuts
- Paused mode: Show "PAUSED - Press 'p' to resume"
- Filtering mode: Show filter status (future)

Example:
```
q:Quit | r:Refresh | p:Pause | ↑↓:Scroll | f:Filter | h:Help
```

### 8. Real-Time Updates and Performance

**REQ-8.1**: The application SHALL refresh data at configurable intervals (default: 2 seconds).

**REQ-8.2**: The refresh interval SHALL be configurable between 1-10 seconds:
- `--refresh=1`: Fast updates (1 second)
- `--refresh=2`: Default (2 seconds)
- `--refresh=5`: Slow updates (5 seconds)

**REQ-8.3**: The application SHALL support manual refresh with `r` key (immediate update).

**REQ-8.4**: The application SHALL support pause/resume with `p` key (stops auto-refresh).

**REQ-8.5**: The application SHALL query database efficiently:
- Use existing statistics APIs
- Minimize query overhead (< 100ms per refresh)
- Cache data between refreshes when possible

**REQ-8.6**: The application SHALL handle database locking gracefully:
- Retry queries up to 3 times with 50ms delay
- Show "Loading..." indicator during retries
- Display error message if database inaccessible

**REQ-8.7**: The application SHALL detect stale data:
- Warn if worker heartbeat is > 30 seconds old
- Warn if database hasn't been updated in > 1 minute
- Display warning in header panel

### 9. User Interaction and Keyboard Controls

**REQ-9.1**: The application SHALL support keyboard navigation:
- `q`, `Q`, `ESC`, `Ctrl+C`: Exit application
- `r`, `R`: Force refresh now
- `p`, `P`: Pause/resume auto-refresh
- `↑`, `k`: Scroll activity log up
- `↓`, `j`: Scroll activity log down
- `Page Up`, `Ctrl+U`: Scroll activity log up one page
- `Page Down`, `Ctrl+D`: Scroll activity log down one page
- `Home`, `g`: Jump to top of activity log
- `End`, `G`: Jump to bottom of activity log

**REQ-9.2**: The application SHALL provide visual feedback for key presses:
- Brief flash or indicator for refresh
- Mode indicator for pause/resume

**REQ-9.3**: The application SHALL ignore invalid key presses (no error messages).

### 10. Color Themes and Accessibility

**REQ-10.1**: The application SHALL support color themes:
- **Dark theme**: Light text on dark background (default)
- **Light theme**: Dark text on light background
- **Auto theme**: Detect terminal background and choose appropriate theme

**REQ-10.2**: The application SHALL support no-color mode (`--no-color`):
- Use symbols instead of colors for status (✓, ⚠, ✗)
- Use ASCII borders instead of Unicode box-drawing
- Ensure readability on monochrome displays

**REQ-10.3**: The application SHALL use accessible color combinations:
- Sufficient contrast ratios (WCAG AA compliant)
- Avoid red-green as sole differentiators
- Support terminal color limitations (8/16/256 colors)

### 11. Error Handling and Resilience

**REQ-11.1**: If the database is not found, the application SHALL:
- Display clear error message in main panel
- Suggest commands to initialize system
- Poll for database creation (every 5 seconds)
- Automatically connect when database appears

**REQ-11.2**: If the database connection is lost, the application SHALL:
- Display "Disconnected" status in header
- Continue attempting to reconnect (every 2 seconds)
- Restore display when connection restored
- Preserve activity log buffer

**REQ-11.3**: If terminal size is too small (< 80x24), the application SHALL:
- Display warning message asking user to resize
- Show minimum required size
- Gracefully degrade layout if possible

**REQ-11.4**: If worker data is stale (> 1 minute), the application SHALL:
- Display warning in header panel
- Gray out stale worker entries
- Suggest checking worker health

**REQ-11.5**: The application SHALL log errors to file if `--log-file` specified:
- TUI rendering errors
- Database query errors
- Terminal handling errors
- Do NOT log to stdout/stderr (interferes with TUI)

### 12. Integration with Existing Infrastructure

**REQ-12.1**: The application SHALL use existing monitoring infrastructure:
- `JobQueue` class for database queries
- Statistics APIs (`get_worker_stats()`, `get_queue_statistics()`)
- Worker event log for activity history
- Existing database schema (no changes required)

**REQ-12.2**: The application SHALL respect `--db-path` option consistent with other commands.

**REQ-12.3**: The application SHALL use the same configuration system as other CLM commands.

**REQ-12.4**: The application SHALL run independently of `clm build` (no coupling).

### 13. TUI Library Selection

**REQ-13.1**: The application SHALL use a mature Python TUI library:
- **Option A**: Rich + Textual (recommended)
  - Pros: Modern, feature-rich, async-ready, active development
  - Cons: Larger dependency
- **Option B**: urwid
  - Pros: Mature, lightweight
  - Cons: Less modern, fewer features
- **Option C**: blessed + custom layout
  - Pros: Lightweight, fine-grained control
  - Cons: More implementation work

**Recommendation**: Use Rich + Textual for modern TUI experience.

**REQ-13.2**: The chosen library SHALL support:
- Terminal resize handling
- Keyboard event handling
- Color themes and styling
- Panel/layout management
- Cross-platform support (Linux, macOS, Windows)

## Non-Requirements

**NR-1**: This application does NOT aim to modify system state (no start/stop workers, cancel jobs).

**NR-2**: This application does NOT aim to provide detailed job history (use database queries or web dashboard).

**NR-3**: This application does NOT aim to support mouse interaction (keyboard-only is sufficient).

**NR-4**: This application does NOT aim to support remote monitoring (run locally with database access).

**NR-5**: This application does NOT aim to show worker resource limits configuration.

## Success Criteria

1. **Real-Time Visibility**: Users can watch jobs being processed in real-time with 1-5 second updates.

2. **At-a-Glance Status**: Users can quickly identify worker status, queue depth, and system health.

3. **Performance Monitoring**: Users can observe worker load and job throughput to optimize configuration.

4. **Responsive Interface**: UI updates smoothly without flickering, handles terminal resize gracefully.

5. **Easy to Use**: Intuitive keyboard shortcuts, clear visual hierarchy, helpful footer text.

6. **Reliable**: Handles database errors, connection loss, and stale data gracefully.

## Open Questions

1. **Activity Log Ordering**: Should newest events be at top or bottom?
   - **Recommendation**: Newest at bottom (like terminal output), auto-scroll to bottom

2. **Worker Details**: Should we show CPU/memory for direct mode workers?
   - **Recommendation**: Only for Docker mode (easier to measure)

3. **Sound Alerts**: Should the app beep/alert on failures?
   - **Recommendation**: No, keep it silent (users can watch visually)

4. **Export Data**: Should users be able to export current view to file?
   - **Recommendation**: No, use `clm status --format=json` for that

5. **Multiple Database Support**: Should users monitor multiple CLM instances?
   - **Recommendation**: No, run multiple TUI instances if needed

6. **Progress Bars**: Should we show individual job progress (if available)?
   - **Recommendation**: Yes, if backend provides progress updates (future enhancement)

## Dependencies

- **Textual**: Modern Python TUI framework (https://github.com/Textualize/textual)
- **Rich**: Terminal formatting library (dependency of Textual)
- Existing `JobQueue` and statistics APIs
- Python 3.10+ (for async/await support)

## Implementation Approach

### Phase 1: Basic TUI Structure (2-3 days)
- Set up Textual application skeleton
- Implement screen layout with panels
- Add keyboard shortcuts (quit, refresh, pause)
- Test terminal resize handling

### Phase 2: Data Integration (2-3 days)
- Connect to SQLite database via JobQueue
- Query worker statistics and display in Workers panel
- Query queue statistics and display in Queue panel
- Implement auto-refresh loop

### Phase 3: Activity Log (2 days)
- Query recent job events from worker_events table
- Display in Activity panel with scrolling
- Add color-coding for event types
- Implement scroll controls (up/down, page up/down)

### Phase 4: Visual Polish (2 days)
- Add color themes (dark, light, auto)
- Add progress indicators and visual feedback
- Implement status highlights (warnings, errors)
- Add header and footer formatting

### Phase 5: Error Handling (1-2 days)
- Handle database connection errors
- Handle stale data warnings
- Handle terminal size issues
- Add error logging to file

### Phase 6: Testing & Documentation (1-2 days)
- Manual testing across different terminals
- Test with different database states
- Document keyboard shortcuts
- Update user guide

**Total Estimate**: 10-14 days

## Risks

1. **TUI Library Learning Curve**: Textual is relatively new, team may need learning time
   - Mitigation: Allocate time for learning, use examples from Textual docs

2. **Terminal Compatibility**: Different terminals may render differently
   - Mitigation: Test on common terminals (xterm, iTerm2, Windows Terminal)

3. **Performance with Large Datasets**: Rendering many events might be slow
   - Mitigation: Limit activity log to recent 500 events, optimize queries

4. **Database Locking**: Frequent queries might lock database
   - Mitigation: Use read-only connections, handle timeouts gracefully

5. **Screen Flicker**: Frequent redraws might cause flicker
   - Mitigation: Use Textual's diff-based rendering, only update changed panels

## Appendix A: Screen Mockups

### Full Screen Layout (120x40)

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ CLM Monitor v0.3.0 | ✓ System Healthy | 2025-11-15 10:30:15 | DB: ~/clm/clm_jobs.db | Refresh: 2s                  │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ ┌─ Workers ────────────────────────────────────┐ ┌─ Job Queue ──────────────────────────────────────────────────┐ │
│ │ Notebook (2 workers, direct mode)            │ │ Pending:    5 jobs  (oldest: 02:15) ⚠                       │ │
│ │   ⚙ nb-abc123  lecture-01.ipynb    02:15 90% │ │ Processing: 3 jobs  [███░░░░░░░░░░░]                        │ │
│ │   ✓ nb-def456  [idle]                        │ │ Completed:  142 jobs (last hour)                            │ │
│ │                                               │ │ Failed:     2 jobs   (1.4% failure rate)                    │ │
│ │ PlantUML (1 worker, docker mode)             │ │                                                              │ │
│ │   ⚙ pu-xyz789  arch.puml          00:45  45% │ │ Throughput: 28 jobs/min → (stable)                          │ │
│ │                                               │ │ Avg Time:   4.2s per job                                    │ │
│ │ DrawIO (0 workers)                           │ └──────────────────────────────────────────────────────────────┘ │
│ │   ⚠ No workers registered                    │                                                                  │
│ └──────────────────────────────────────────────┘                                                                  │
│ ┌─ Recent Activity ─────────────────────────────────────────────────────────────────────────────────────────────┐ │
│ │ 10:30:15 ✓ Completed  lecture-01.ipynb                                                           (45s)       │ │
│ │ 10:30:10 ⚙ Started    lecture-02.ipynb                                                                       │ │
│ │ 10:30:05 ✓ Completed  diagrams/architecture.puml                                                 (12s)       │ │
│ │ 10:30:00 ✗ Failed     broken.ipynb                                                               (3s)        │ │
│ │ 10:29:55 ⚙ Started    lecture-03.ipynb                                                                       │ │
│ │ 10:29:50 → Assigned   nb-abc123 → lecture-01.ipynb                                                           │ │
│ │ 10:29:45 ✓ Completed  intro.ipynb                                                                (23s)       │ │
│ │ 10:29:40 ⚙ Started    diagrams/architecture.puml                                                             │ │
│ │ ...                                                                                                           │ │
│ └───────────────────────────────────────────────────────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ q:Quit | r:Refresh | p:Pause | ↑↓:Scroll | f:Filter | h:Help                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Minimal Terminal (80x24)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ CLM Monitor | ✓ Healthy | 10:30:15 | Refresh: 2s                             │
├──────────────────────────────────────────────────────────────────────────────┤
│ Workers:                                                                     │
│   Notebook: 1 busy, 1 idle (direct)                                          │
│     ⚙ nb-abc  lecture-01.ipynb  02:15                                        │
│   PlantUML: 1 busy (docker)                                                  │
│   DrawIO: 0 ⚠                                                                │
│                                                                              │
│ Queue: 5 pending, 3 processing, 142 completed, 2 failed                      │
│                                                                              │
│ Activity:                                                                    │
│   10:30:15 ✓ Completed  lecture-01.ipynb  (45s)                             │
│   10:30:10 ⚙ Started    lecture-02.ipynb                                     │
│   10:30:05 ✓ Completed  arch.puml         (12s)                             │
│   10:30:00 ✗ Failed     broken.ipynb      (3s)                              │
│   10:29:55 ⚙ Started    lecture-03.ipynb                                     │
│   10:29:50 → Assigned   nb-abc → lecture-01.ipynb                            │
│   10:29:45 ✓ Completed  intro.ipynb       (23s)                             │
│   ...                                                                        │
├──────────────────────────────────────────────────────────────────────────────┤
│ q:Quit | r:Refresh | p:Pause | ↑↓:Scroll                                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Appendix B: Textual Code Structure

```python
# app.py - Main TUI application
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Header, Footer, Static, DataTable
from textual.timer import Timer

class CLMMonitor(App):
    """CLM Real-Time Monitoring TUI"""

    CSS_PATH = "monitor.css"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("p", "pause", "Pause/Resume"),
    ]

    def __init__(self, db_path: str, refresh_interval: int = 2):
        super().__init__()
        self.db_path = db_path
        self.refresh_interval = refresh_interval
        self.job_queue = JobQueue(db_path)
        self.paused = False

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield StatusHeader()
        yield Container(
            Horizontal(
                WorkersPanel(id="workers"),
                QueuePanel(id="queue"),
            ),
            ActivityPanel(id="activity"),
            id="main",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Set up refresh timer."""
        self.set_interval(self.refresh_interval, self.refresh_data)

    def refresh_data(self) -> None:
        """Query database and update panels."""
        if self.paused:
            return

        worker_stats = self.job_queue.get_worker_stats()
        queue_stats = self.job_queue.get_queue_statistics()
        recent_events = self.job_queue.get_recent_events(limit=100)

        self.query_one("#workers").update(worker_stats)
        self.query_one("#queue").update(queue_stats)
        self.query_one("#activity").update(recent_events)
```

## Appendix C: Installation and Dependencies

```bash
# Install CLM with TUI support
pip install -e ".[tui]"

# Or install dependencies manually
pip install textual rich
```

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
tui = [
    "textual>=0.50.0",
    "rich>=13.7.0",
]
```
