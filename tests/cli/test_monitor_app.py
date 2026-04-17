"""Tests for the Monitor TUI CLMMonitorApp and its widgets.

These tests exercise the application end-to-end through Textual's
``run_test`` pilot harness as well as the individual widget rendering
routines.  They also document three currently-broken behaviors via
``xfail`` markers so regressions in a future fix can be detected:

    * Bug #1  Stale "Started" entries and ``(?)`` durations in the
              activity log (duration truncation + no cleanup of old
              processing rows).
    * Bug #2  The status-header area is empty after a build — it
              should show the course spec currently being processed.
    * Bug #3  Panel scrolling lags behind the mouse because every
              refresh rebuilds the worker/queue panels from scratch.

The bug descriptions in each ``xfail`` reason give future-you a
one-line starting point when tackling the fix.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from clm.cli.monitor.data_provider import ActivityEvent, DataProvider
from clm.cli.monitor.widgets.activity_panel import ActivityPanel
from clm.cli.monitor.widgets.queue_panel import QueuePanel
from clm.cli.monitor.widgets.status_header import StatusHeader
from clm.cli.monitor.widgets.workers_panel import WorkersPanel
from clm.cli.status.models import (
    BusyWorkerInfo,
    DatabaseInfo,
    QueueStats,
    StatusInfo,
    SystemHealth,
    WorkerTypeStats,
)
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def jobs_db(tmp_path: Path) -> Path:
    """Initialize an empty jobs DB for tests."""
    db = tmp_path / "jobs.db"
    init_database(db)
    return db


def _status(
    *,
    health: SystemHealth = SystemHealth.HEALTHY,
    workers: dict[str, WorkerTypeStats] | None = None,
    queue: QueueStats | None = None,
) -> StatusInfo:
    return StatusInfo(
        timestamp=datetime(2026, 4, 17, 21, 0, 0),
        health=health,
        database=DatabaseInfo(path="/tmp/x.db", accessible=True, exists=True, size_bytes=0),
        workers=workers or {},
        queue=queue
        or QueueStats(pending=0, processing=0, completed_last_hour=0, failed_last_hour=0),
    )


def _event(
    event_type: str,
    *,
    job_id: str = "1",
    document_path: str = "topic_x/slides.py",
    duration_seconds: int | None = None,
    timestamp: datetime | None = None,
    error_message: str | None = None,
) -> ActivityEvent:
    return ActivityEvent(
        timestamp=timestamp or datetime(2026, 4, 17, 21, 0, 0),
        event_type=event_type,
        job_id=job_id,
        document_path=document_path,
        duration_seconds=duration_seconds,
        error_message=error_message,
    )


def _rendered_activity_text(panel: ActivityPanel) -> str:
    """Return the plain-text content of the activity panel's RichLog.

    The RichLog's ``lines`` are Strip objects; ``Strip.text`` gives the
    user-visible text without style markup.
    """
    from textual.widgets import RichLog

    log = panel.query_one("#activity-log", RichLog)
    return "\n".join(line.text for line in log.lines)


def _rendered_mounted_children_text(panel, content_id: str) -> str:
    """Concatenate the rendered text of every child Static in a scroll pane.

    Workers and queue panels dynamically mount Static widgets carrying
    Rich-markup strings; render them and strip markup by going through
    ``rich.text.Text.from_markup``.
    """
    from rich.text import Text as RichText
    from textual.containers import VerticalScroll

    content = panel.query_one(f"#{content_id}", VerticalScroll)
    parts: list[str] = []
    for child in content.children:
        rendered = child.render()
        if isinstance(rendered, str):
            parts.append(RichText.from_markup(rendered).plain)
        else:
            parts.append(str(rendered))
    return " ".join(parts)


def _rendered_workers_text(panel: WorkersPanel) -> str:
    return _rendered_mounted_children_text(panel, "workers-content")


def _rendered_queue_text(panel: QueuePanel) -> str:
    return _rendered_mounted_children_text(panel, "queue-content")


# ---------------------------------------------------------------------------
# DataProvider: duration precision bug
# ---------------------------------------------------------------------------


class TestDataProviderEventDuration:
    """Cover the duration field on events returned by get_recent_events."""

    def _seed_completed_job(
        self,
        db_path: Path,
        *,
        started_at: str,
        completed_at: str,
    ) -> int:
        with JobQueue(db_path) as jq:
            job_id = jq.add_job(
                job_type="notebook",
                input_file="topic_x/slides.py",
                output_file="out/x.ipynb",
                content_hash="hash",
                payload={},
            )
            conn = jq._get_conn()
            conn.execute(
                """
                UPDATE jobs
                SET status='completed', started_at=?, completed_at=?
                WHERE id=?
                """,
                (started_at, completed_at, job_id),
            )
            conn.commit()
        return job_id

    def test_multi_second_duration_is_reported(self, jobs_db: Path) -> None:
        """Completed jobs with a multi-second gap produce a non-zero duration."""
        self._seed_completed_job(
            jobs_db,
            started_at="2026-04-17 21:00:00",
            completed_at="2026-04-17 21:00:10",
        )
        provider = DataProvider(db_path=jobs_db)
        events = provider.get_recent_events(limit=5)
        provider.close()

        completed = [e for e in events if e.event_type == "job_completed"]
        assert len(completed) == 1
        assert completed[0].duration_seconds is not None
        assert completed[0].duration_seconds >= 9  # allow 1s julianday slack

    @pytest.mark.xfail(
        reason=(
            "Monitor bug #1: julianday() subtraction in get_recent_events loses "
            "precision (1s → 0.9999945s → CAST(.. AS INTEGER) = 0), so sub-2s "
            "jobs report duration=0 which the activity panel renders as '(?)'. "
            "Fix: round or use the ROUND() SQL function, or read duration_ms "
            "from a stored column."
        ),
        strict=True,
    )
    def test_one_second_duration_should_not_report_zero(self, jobs_db: Path) -> None:
        """A completed job with a 1-second gap should not report 0 duration."""
        self._seed_completed_job(
            jobs_db,
            started_at="2026-04-17 21:00:00",
            completed_at="2026-04-17 21:00:01",
        )
        provider = DataProvider(db_path=jobs_db)
        events = provider.get_recent_events(limit=5)
        provider.close()

        completed = [e for e in events if e.event_type == "job_completed"]
        assert len(completed) == 1
        # Expected: ~1. Actual due to bug: 0.
        assert completed[0].duration_seconds is not None
        assert completed[0].duration_seconds >= 1


# ---------------------------------------------------------------------------
# ActivityPanel: stale "Started" entries and duration rendering
# ---------------------------------------------------------------------------


class TestActivityPanelUnit:
    """Directly call ActivityPanel helpers on real widget instances."""

    def test_event_key_differs_by_event_type(self) -> None:
        started = _event("job_started", job_id="42")
        completed = _event("job_completed", job_id="42", duration_seconds=5)
        assert ActivityPanel._event_key(started) != ActivityPanel._event_key(completed)

    def test_format_error_plain_string(self) -> None:
        panel = ActivityPanel(id="ap")
        formatted = panel._format_error("boom")
        assert "boom" in formatted

    def test_format_error_truncated(self) -> None:
        panel = ActivityPanel(id="ap")
        formatted = panel._format_error("x" * 200)
        assert formatted.endswith("...")

    def test_format_error_categorized_json(self) -> None:
        panel = ActivityPanel(id="ap")
        msg = (
            '{"error_type": "user", "category": "syntax", '
            '"error_message": "Unexpected EOF", '
            '"actionable_guidance": "Add a closing brace", '
            '"details": {"cell_number": 3}}'
        )
        formatted = panel._format_error(msg)
        assert "User" in formatted or "user" in formatted
        assert "syntax" in formatted
        assert "Unexpected EOF" in formatted
        assert "Cell #3" in formatted

    def test_format_error_none_returns_unknown(self) -> None:
        panel = ActivityPanel(id="ap")
        assert "unknown" in panel._format_error(None)

    def test_format_error_json_without_categorization(self) -> None:
        panel = ActivityPanel(id="ap")
        msg = '{"error_message": "Something went wrong"}'
        formatted = panel._format_error(msg)
        assert "Something went wrong" in formatted


class TestActivityPanelViaPilot:
    """Drive ActivityPanel through a real Textual App using run_test()."""

    async def test_completed_event_renders_duration(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield ActivityPanel(id="activity-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(ActivityPanel)
            events = [
                _event("job_completed", job_id="1", duration_seconds=42),
            ]
            panel.update_events(events)
            await pilot.pause()
            rendered = _rendered_activity_text(panel)
            assert "Completed" in rendered
            assert "00:42" in rendered

    async def test_empty_events_shows_placeholder(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield ActivityPanel(id="activity-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(ActivityPanel)
            panel.update_events([])
            await pilot.pause()
            rendered = _rendered_activity_text(panel)
            assert "No recent activity" in rendered

    async def test_duplicate_events_not_double_written(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield ActivityPanel(id="activity-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(ActivityPanel)
            event = _event("job_completed", job_id="1", duration_seconds=5)
            panel.update_events([event])
            panel.update_events([event])
            panel.update_events([event])
            await pilot.pause()
            rendered = _rendered_activity_text(panel)
            assert rendered.count("Completed") == 1

    async def test_full_refresh_clears_seen_keys(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield ActivityPanel(id="activity-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(ActivityPanel)
            event = _event("job_completed", job_id="1", duration_seconds=5)
            panel.update_events([event])
            # Full refresh should redraw the same event rather than skip it
            panel.full_refresh_events([event])
            await pilot.pause()
            rendered = _rendered_activity_text(panel)
            # After clear + repopulate there is exactly one line.
            assert rendered.count("Completed") == 1

    @pytest.mark.xfail(
        reason=(
            "Monitor bug #1 (presentation half): events with "
            "duration_seconds=0 render '(?)' because the widget tests "
            "`if event.duration_seconds` — this is the downstream side of "
            "the julianday precision loss and also fires for legitimately "
            "instantaneous jobs (cached skips). Fix: render '00:00' when "
            "duration_seconds is 0, reserve '?' only for None."
        ),
        strict=True,
    )
    async def test_zero_duration_renders_as_instant_not_question_mark(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield ActivityPanel(id="activity-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(ActivityPanel)
            panel.update_events([_event("job_completed", job_id="1", duration_seconds=0)])
            await pilot.pause()
            rendered = _rendered_activity_text(panel)
            # Expected: shows a real duration like 00:00. Actual: shows '?'.
            assert "(?)" not in rendered
            assert "00:00" in rendered

    @pytest.mark.xfail(
        reason=(
            "Monitor bug #1 (stale Started lines): once a job emits its "
            "'job_started' line, the widget keeps that line forever even "
            "after the job reaches 'job_completed'. With many fast jobs "
            "the panel fills with ghost 'Started' entries that never get "
            "replaced. Fix: when a job_completed/failed arrives, remove "
            "the matching job_started line from the log, or coalesce both "
            "into a single entry."
        ),
        strict=True,
    )
    async def test_completing_a_job_removes_its_started_entry(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield ActivityPanel(id="activity-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(ActivityPanel)
            # First poll: saw the job as started.
            panel.update_events([_event("job_started", job_id="42", document_path="a.py")])
            # Second poll: same job is now completed.
            panel.update_events(
                [
                    _event(
                        "job_completed",
                        job_id="42",
                        document_path="a.py",
                        duration_seconds=3,
                    )
                ]
            )
            await pilot.pause()
            rendered = _rendered_activity_text(panel)
            # Expected: no stale 'Started' line remains.
            assert "Started" not in rendered
            assert "Completed" in rendered


# ---------------------------------------------------------------------------
# StatusHeader: empty title bug
# ---------------------------------------------------------------------------


class TestStatusHeaderEmptyTitleBug:
    """The header area remains empty after a build completes."""

    def test_loading_state_has_placeholder_text(self) -> None:
        header = StatusHeader(id="h")
        # Initial render: even before data arrives the header should have
        # some placeholder text.
        rendered = header._render_content()
        assert rendered.plain.strip() != ""

    def test_idle_state_shows_no_activity(self) -> None:
        header = StatusHeader(id="h")
        header.status = _status()
        rendered = header._render_content().plain
        assert "No activity" in rendered or "done" in rendered

    @pytest.mark.xfail(
        reason=(
            "Monitor bug #2: the header does not surface the course spec "
            "that is currently being processed, so during a large build "
            "the big top panel is visually empty / uninformative. Fix: "
            "track the active course spec in the data_provider (either "
            "via a dedicated table, an env stamp, or the most-frequent "
            "spec among processing jobs) and render it here."
        ),
        strict=True,
    )
    def test_header_shows_current_course_spec(self) -> None:
        header = StatusHeader(id="h")
        # Hypothetical future field — not present yet.
        status = _status(
            queue=QueueStats(pending=0, processing=2, completed_last_hour=100, failed_last_hour=0)
        )
        # Attach ad-hoc attribute for forward-compat: the real fix would
        # add current_course_spec to StatusInfo and _render_content would
        # use it.
        setattr(status, "current_course_spec", "python-best-practice.xml")
        header.status = status
        rendered = header._render_content().plain
        assert "python-best-practice" in rendered


class TestStatusHeaderRenderBranches:
    """Exercise the remaining render branches for coverage."""

    def test_warning_health_renders_warning_icon(self) -> None:
        header = StatusHeader(id="h")
        header.status = _status(health=SystemHealth.WARNING)
        plain = header._render_content().plain
        assert "Warning" in plain

    def test_error_health_renders_error_icon(self) -> None:
        header = StatusHeader(id="h")
        header.status = _status(health=SystemHealth.ERROR)
        plain = header._render_content().plain
        assert "Error" in plain

    def test_queue_with_processing_and_pending(self) -> None:
        header = StatusHeader(id="h")
        header.status = _status(
            queue=QueueStats(pending=3, processing=1, completed_last_hour=0, failed_last_hour=0)
        )
        plain = header._render_content().plain
        assert "1 processing" in plain
        assert "3 pending" in plain

    def test_queue_with_failures(self) -> None:
        header = StatusHeader(id="h")
        header.status = _status(
            queue=QueueStats(pending=0, processing=0, completed_last_hour=10, failed_last_hour=2)
        )
        plain = header._render_content().plain
        assert "10 done" in plain
        assert "2 failed" in plain


# ---------------------------------------------------------------------------
# WorkersPanel rendering via pilot
# ---------------------------------------------------------------------------


class TestWorkersPanelPilot:
    async def test_no_workers_registered_placeholder(self) -> None:
        """Status with empty workers dict shows the registration warning."""
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield WorkersPanel(id="workers-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(WorkersPanel)
            panel.update_status(_status())
            await pilot.pause()
            text = _rendered_workers_text(panel)
            assert "No workers registered" in text

    async def test_zero_worker_types_show_not_started_messages(self) -> None:
        """When a worker type has total=0, show 'No workers started'."""
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield WorkersPanel(id="workers-panel", classes="panel")

        workers = {
            "notebook": WorkerTypeStats(
                worker_type="notebook",
                execution_mode=None,
                total=0,
                idle=0,
                busy=0,
                hung=0,
                dead=0,
                busy_workers=[],
            ),
        }
        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(WorkersPanel)
            panel.update_status(_status(workers=workers))
            await pilot.pause()
            text = _rendered_workers_text(panel)
            assert "No workers started" in text

    async def test_busy_workers_list_entries(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield WorkersPanel(id="workers-panel", classes="panel")

        workers = {
            "notebook": WorkerTypeStats(
                worker_type="notebook",
                execution_mode="direct",
                total=2,
                idle=0,
                busy=2,
                hung=0,
                dead=0,
                busy_workers=[
                    BusyWorkerInfo(
                        worker_id="w1",
                        job_id="1",
                        document_path="module/topic/slides_intro.ipynb",
                        elapsed_seconds=30,
                        output_format="html",
                        kind="completed",
                    ),
                ],
            ),
        }
        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(WorkersPanel)
            panel.update_status(_status(workers=workers))
            await pilot.pause()
            text = _rendered_workers_text(panel)
            assert "Notebook" in text
            assert "2 busy" in text
            assert "slides_intro" in text

    async def test_hung_and_dead_workers_surfaced(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield WorkersPanel(id="workers-panel", classes="panel")

        workers = {
            "notebook": WorkerTypeStats(
                worker_type="notebook",
                execution_mode="docker",
                total=4,
                idle=1,
                busy=0,
                hung=1,
                dead=2,
                busy_workers=[],
            ),
        }
        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(WorkersPanel)
            panel.update_status(_status(workers=workers))
            await pilot.pause()
            text = _rendered_workers_text(panel)
            assert "1 idle" in text
            assert "1 hung" in text
            assert "2 dead" in text


class TestWorkersPanelFormatBusyWorker:
    """Unit-test the private _format_busy_worker string builder."""

    def test_windows_path_strips_to_filename(self) -> None:
        panel = WorkersPanel(id="wp")
        worker = BusyWorkerInfo(
            worker_id="w1",
            job_id="1",
            document_path=r"C:\repo\topic\slides.ipynb",
            elapsed_seconds=10,
        )
        text = panel._format_busy_worker(worker, "direct")
        assert "slides" in text
        assert "topic" not in text  # only filename remains

    def test_long_document_name_truncated(self) -> None:
        panel = WorkersPanel(id="wp")
        worker = BusyWorkerInfo(
            worker_id="w1",
            job_id="1",
            document_path="x" * 80 + ".ipynb",
            elapsed_seconds=5,
        )
        text = panel._format_busy_worker(worker, "direct")
        assert "..." in text

    def test_format_includes_elapsed_time(self) -> None:
        panel = WorkersPanel(id="wp")
        worker = BusyWorkerInfo(
            worker_id="w1",
            job_id="1",
            document_path="slides.py",
            elapsed_seconds=125,
        )
        text = panel._format_busy_worker(worker, "direct")
        assert "02:05" in text


# ---------------------------------------------------------------------------
# QueuePanel pilot tests
# ---------------------------------------------------------------------------


class TestQueuePanelPilot:
    async def test_warns_on_old_pending(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield QueuePanel(id="queue-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(QueuePanel)
            panel.update_status(
                _status(
                    queue=QueueStats(
                        pending=3,
                        processing=0,
                        completed_last_hour=0,
                        failed_last_hour=0,
                        oldest_pending_seconds=600,
                    )
                )
            )
            await pilot.pause()
            text = _rendered_queue_text(panel)
            assert "oldest:" in text
            assert "3 jobs" in text

    async def test_high_failure_rate_flagged(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield QueuePanel(id="queue-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(QueuePanel)
            panel.update_status(
                _status(
                    queue=QueueStats(
                        pending=0,
                        processing=0,
                        completed_last_hour=2,
                        failed_last_hour=8,
                    )
                )
            )
            await pilot.pause()
            text = _rendered_queue_text(panel)
            assert "80.0%" in text  # 8 / 10 = 80%

    async def test_empty_queue_shows_zero_stats(self) -> None:
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield QueuePanel(id="queue-panel", classes="panel")

        app = _TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(QueuePanel)
            panel.update_status(_status())
            await pilot.pause()
            text = _rendered_queue_text(panel)
            assert "Pending:" in text
            assert "0 jobs" in text


# ---------------------------------------------------------------------------
# CLMMonitorApp: run_test() smoke test
# ---------------------------------------------------------------------------


class TestMonitorAppRun:
    async def test_app_starts_and_refreshes(
        self, jobs_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clm.cli.monitor.app import CLMMonitorApp

        app = CLMMonitorApp(db_path=jobs_db, refresh_interval=1)
        async with app.run_test() as pilot:
            await pilot.pause()
            # App should have mounted successfully and populated widget refs.
            assert app.workers_panel is not None
            assert app.queue_panel is not None
            assert app.activity_panel is not None
            assert app.status_header is not None
            assert app.title == "CLM Monitor"
            assert "1" in app.sub_title

    async def test_pause_resume_toggles_paused_flag(self, jobs_db: Path) -> None:
        from clm.cli.monitor.app import CLMMonitorApp

        app = CLMMonitorApp(db_path=jobs_db, refresh_interval=2)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.paused is False
            await pilot.press("p")
            await pilot.pause()
            assert app.paused is True
            assert app.sub_title == "PAUSED"
            await pilot.press("p")
            await pilot.pause()
            assert app.paused is False

    async def test_pause_short_circuits_refresh(self, jobs_db: Path) -> None:
        from clm.cli.monitor.app import CLMMonitorApp

        app = CLMMonitorApp(db_path=jobs_db, refresh_interval=2)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()

            # While paused, refresh_data should be a no-op (no exception).
            app.refresh_data()
            app.action_refresh()

    async def test_manual_refresh_runs_full_refresh_on_activity(
        self, jobs_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clm.cli.monitor.app import CLMMonitorApp

        app = CLMMonitorApp(db_path=jobs_db, refresh_interval=2)
        full_refresh_called: list[list[ActivityEvent]] = []

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.activity_panel is not None
            monkeypatch.setattr(
                app.activity_panel,
                "full_refresh_events",
                lambda events: full_refresh_called.append(events),
            )
            await pilot.press("r")
            await pilot.pause()
            assert len(full_refresh_called) == 1

    async def test_refresh_error_notifies_without_crash(
        self, jobs_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clm.cli.monitor.app import CLMMonitorApp

        app = CLMMonitorApp(db_path=jobs_db, refresh_interval=2)

        def fake_get_status():
            raise RuntimeError("db gone")

        async with app.run_test() as pilot:
            await pilot.pause()
            monkeypatch.setattr(app.data_provider, "get_status", fake_get_status)
            # Should not raise — the exception handler inside refresh_data
            # notifies instead.
            app.refresh_data()
            app.action_refresh()


# ---------------------------------------------------------------------------
# Scrolling sluggishness (bug #3) — documented, not tested
# ---------------------------------------------------------------------------
#
# Bug #3 describes observable lag (1-2s) between the mouse and the
# scrollbar indicator on the workers/activity panels, becoming severe
# while refresh_data is rebuilding the workers panel every 2 seconds.
#
# The root cause lives in WorkersPanel._render_workers which calls
# content_widget.remove_children() on every tick — Textual then has to
# reconstruct the scroll layout, which interrupts ongoing mouse
# interaction.
#
# A meaningful perf test would need wall-clock timing that is too
# flaky for CI and too environment-dependent for the fast suite.
# Tracking it in docs/claude/TODO.md (see monitor-tui-followups
# section) instead of as a failing unit test — the fix itself is
# "diff the WorkerTypeStats and only remount the changed lines".
