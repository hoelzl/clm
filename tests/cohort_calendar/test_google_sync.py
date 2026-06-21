"""Tests for the Google Calendar push: planning, formatting, apply (``calendar push``).

Everything here runs without the ``[gcal]`` extra — the planning half is pure,
and the apply half takes an injected (fake) service object.
"""

import datetime as dt

import pytest

from clm.cli.commands.export.schedule import ScheduleDeck
from clm.cohort_calendar import google_sync as gs
from clm.cohort_calendar.projection import Assignment, Projection


def deck(title, topic, file, number=0):
    return ScheduleDeck(video_title=title, topic_id=topic, deck_file=file, number_in_section=number)


def sample() -> Projection:
    return Projection(
        assignments=(
            Assignment(
                dt.date(2026, 3, 2),
                dt.date(2026, 3, 2),
                (deck("Intro", "intro", "slides_010_intro", 1),),
                None,
                "video",
                ("slides_010_intro",),
                section_title="Week 01: Foundations",
            ),
            Assignment(
                dt.date(2026, 3, 3),
                dt.date(2026, 3, 3),
                (),
                "Review & Q&A",
                "insert",
                ("insert:2026-03-03",),
            ),
            Assignment(
                dt.date(2026, 3, 4),
                dt.date(2026, 3, 5),
                (deck("Spanned", "span", "slides_020_span", 2),),
                None,
                "video",
                ("slides_020_span",),
                section_title="Week 02: More",
            ),
        ),
        diagnostics=(),
    )


UID_INTRO = "jan-slides_010_intro@clm.cohort-calendar"
UID_INSERT = "jan-insert-2026-03-03@clm.cohort-calendar"
UID_SPAN = "jan-slides_020_span@clm.cohort-calendar"


def existing_event(
    uid,
    *,
    eid="ev1",
    summary="Intro",
    start="2026-03-02",
    end="2026-03-03",
    description="Week 01: Foundations\n\n01  Intro",
):
    event = {
        "id": eid,
        "summary": summary,
        "start": {"date": start},
        "end": {"date": end},
        "extendedProperties": {"private": {gs.MANAGED_KEY: "jan", gs.UID_KEY: uid}},
    }
    if description is not None:
        event["description"] = description
    return event


class TestBuildDesiredEvents:
    def test_uids_match_the_ics_export(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        assert set(desired) == {UID_INTRO, UID_INSERT, UID_SPAN}

    def test_video_event_body(self):
        body = gs.build_desired_events(sample(), namespace="jan")[UID_INTRO]
        assert body["summary"] == "Intro"
        assert body["start"] == {"date": "2026-03-02"}
        assert body["end"] == {"date": "2026-03-03"}  # exclusive end, like DTEND
        assert body["description"] == "Week 01: Foundations\n\n01  Intro"
        assert body["transparency"] == "transparent"
        private = body["extendedProperties"]["private"]
        assert private == {gs.MANAGED_KEY: "jan", gs.UID_KEY: UID_INTRO}

    def test_multi_deck_summary_and_numbered_body(self):
        proj = Projection(
            (
                Assignment(
                    dt.date(2026, 3, 6),
                    dt.date(2026, 3, 6),
                    (
                        deck("Funktionen", "py", "slides_040v_functions", 19),
                        deck("Imports", "py", "slides_044v_imports", 20),
                    ),
                    None,
                    "video",
                    ("slides_040v_functions",),
                    section_title="Woche 01: Python-Setup",
                ),
            ),
            (),
        )
        body = gs.build_desired_events(proj, namespace="jan", language="en")
        event = next(iter(body.values()))
        assert event["summary"] == "Funktionen (+1 more)"
        assert event["description"] == "Woche 01: Python-Setup\n\n19  Funktionen\n20  Imports"

    def test_activity_only_day_is_titled_not_blank(self):
        proj = Projection(
            (
                Assignment(
                    dt.date(2026, 9, 15),
                    dt.date(2026, 9, 15),
                    (),
                    None,
                    "video",
                    (),
                    section_title="Woche 20: Abschlussprojekt",
                    activity_labels=("Projektarbeit (kein Video)",),
                ),
            ),
            (),
        )
        event = next(iter(gs.build_desired_events(proj, namespace="jan").values()))
        assert event["summary"] == "Projektarbeit (kein Video)"
        assert event["description"] == "Woche 20: Abschlussprojekt"

    def test_insert_uses_label_and_has_no_description(self):
        body = gs.build_desired_events(sample(), namespace="jan")[UID_INSERT]
        assert body["summary"] == "Review & Q&A"
        assert "description" not in body

    def test_span_covers_all_dates(self):
        body = gs.build_desired_events(sample(), namespace="jan")[UID_SPAN]
        assert body["start"] == {"date": "2026-03-04"}
        assert body["end"] == {"date": "2026-03-06"}  # 5 Mar inclusive -> 6 Mar exclusive


class TestPlanSync:
    def test_everything_new_inserts_all(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        plan = gs.plan_sync(desired, [])
        assert len(plan.inserts) == 3
        assert not plan.updates and not plan.deletes and plan.unchanged == 0

    def test_identical_event_is_unchanged(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        plan = gs.plan_sync(desired, [existing_event(UID_INTRO)])
        assert len(plan.inserts) == 2
        assert not plan.updates and not plan.deletes and plan.unchanged == 1

    def test_shifted_date_updates_in_place(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        stale = existing_event(UID_INTRO, start="2026-03-09", end="2026-03-10")
        plan = gs.plan_sync(desired, [stale])
        assert [(eid, body["start"]["date"]) for eid, body in plan.updates] == [
            ("ev1", "2026-03-02")
        ]
        assert not plan.deletes

    def test_changed_summary_updates(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        plan = gs.plan_sync(desired, [existing_event(UID_INTRO, summary="Old title")])
        assert len(plan.updates) == 1

    def test_vanished_uid_is_deleted(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        gone = existing_event("jan-gone@clm.cohort-calendar", eid="ev9", summary="Removed")
        plan = gs.plan_sync(desired, [gone])
        assert [eid for eid, _label in plan.deletes] == ["ev9"]

    def test_duplicate_uid_keeps_one_deletes_rest(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        first = existing_event(UID_INTRO, eid="ev1")
        second = existing_event(UID_INTRO, eid="ev2")
        plan = gs.plan_sync(desired, [first, second])
        assert [eid for eid, _label in plan.deletes] == ["ev2"]
        assert plan.unchanged == 1

    def test_managed_event_without_uid_is_deleted(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        untagged = {"id": "ev3", "summary": "?", "start": {"date": "2026-03-02"}}
        plan = gs.plan_sync(desired, [untagged])
        assert [eid for eid, _label in plan.deletes] == ["ev3"]

    def test_describe_plan_lines(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        gone = existing_event("jan-gone@clm.cohort-calendar", eid="ev9", summary="Removed")
        shifted = existing_event(UID_INTRO, start="2026-03-09", end="2026-03-10")
        lines = gs.describe_plan(gs.plan_sync(desired, [gone, shifted]))
        assert "+ 2026-03-03  Review & Q&A" in lines
        assert "~ 2026-03-02  Intro" in lines
        assert "- 2026-03-02  Removed" in lines

    def test_noop_plan(self):
        plan = gs.plan_sync({}, [])
        assert plan.is_noop


# --- fake Google service --------------------------------------------------------


class _Request:
    def __init__(self, result=None, record=None, error=None):
        self._result = result if result is not None else {}
        self._record = record
        self._error = error

    def execute(self):
        if self._error is not None:
            raise self._error
        if self._record is not None:
            target, item = self._record
            target.append(item)
        return self._result


class FakeEvents:
    """Stand-in for ``service.events()`` recording mutations, serving list pages."""

    def __init__(self, pages, *, fail_insert=False):
        self._pages = pages
        self._fail_insert = fail_insert
        self.inserted = []
        self.updated = []
        self.deleted = []
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        index = 0 if kwargs.get("pageToken") is None else int(kwargs["pageToken"])
        page = dict(self._pages[index])
        if index + 1 < len(self._pages):
            page["nextPageToken"] = str(index + 1)
        return _Request(page)

    def insert(self, calendarId, body):
        if self._fail_insert:
            return _Request(error=RuntimeError("quota exceeded"))
        return _Request(record=(self.inserted, body))

    def update(self, calendarId, eventId, body):
        return _Request(record=(self.updated, (eventId, body)))

    def delete(self, calendarId, eventId):
        return _Request(record=(self.deleted, eventId))


class FakeService:
    def __init__(self, pages=None, **kwargs):
        self._events = FakeEvents(pages or [{"items": []}], **kwargs)

    def events(self):
        return self._events


class _FakeResp:
    def __init__(self, status):
        self.status = status


class _HttpErrorLike(Exception):
    """Mimics googleapiclient.errors.HttpError's status surface (``.resp.status``)."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = _FakeResp(status)


class _FlakyRequest:
    """A request that raises a transient/permanent error N times, then succeeds."""

    def __init__(self, *, fail_times, status=503, result=None):
        self.calls = 0
        self._fail_times = fail_times
        self._status = status
        self._result = result if result is not None else {"ok": True}

    def execute(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise _HttpErrorLike(self._status)
        return self._result


class TestExecuteRetry:
    def test_retries_transient_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(gs.time, "sleep", lambda _s: None)
        req = _FlakyRequest(fail_times=2, status=503)
        assert gs._execute(req) == {"ok": True}
        assert req.calls == 3  # two 503s, third try wins

    def test_gives_up_after_max_retries(self, monkeypatch):
        monkeypatch.setattr(gs.time, "sleep", lambda _s: None)
        req = _FlakyRequest(fail_times=99, status=503)
        with pytest.raises(gs.GoogleSyncError, match="HTTP 503"):
            gs._execute(req)
        assert req.calls == gs.MAX_RETRIES + 1  # first attempt + MAX_RETRIES

    def test_non_transient_raises_immediately_without_sleeping(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(gs.time, "sleep", slept.append)
        req = _FlakyRequest(fail_times=99, status=404)
        with pytest.raises(gs.GoogleSyncError, match="HTTP 404"):
            gs._execute(req)
        assert req.calls == 1
        assert slept == []

    def test_rate_limit_is_retried(self, monkeypatch):
        monkeypatch.setattr(gs.time, "sleep", lambda _s: None)
        req = _FlakyRequest(fail_times=1, status=429)
        assert gs._execute(req) == {"ok": True}
        assert req.calls == 2


class TestFetchManagedEvents:
    def test_filters_by_managed_tag_and_paginates(self):
        pages = [
            {"items": [existing_event(UID_INTRO, eid="ev1")]},
            {"items": [existing_event(UID_SPAN, eid="ev2")]},
        ]
        service = FakeService(pages)
        events = gs.fetch_managed_events(service, "cal-id", "jan")
        assert [e["id"] for e in events] == ["ev1", "ev2"]
        calls = service.events().list_calls
        assert len(calls) == 2
        assert all(c["privateExtendedProperty"] == "clm_managed=jan" for c in calls)
        assert all(c["calendarId"] == "cal-id" for c in calls)


class TestApplyPlan:
    def test_applies_inserts_updates_deletes(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        gone = existing_event("jan-gone@clm.cohort-calendar", eid="ev9")
        shifted = existing_event(UID_INTRO, eid="ev1", start="2026-03-09", end="2026-03-10")
        plan = gs.plan_sync(desired, [gone, shifted])

        service = FakeService()
        gs.apply_plan(service, "cal-id", plan)
        events = service.events()
        assert {b["summary"] for b in events.inserted} == {"Review & Q&A", "Spanned"}
        assert [eid for eid, _b in events.updated] == ["ev1"]
        assert events.deleted == ["ev9"]

    def test_api_failure_raises_google_sync_error(self):
        desired = gs.build_desired_events(sample(), namespace="jan")
        plan = gs.plan_sync(desired, [])
        service = FakeService(fail_insert=True)
        with pytest.raises(gs.GoogleSyncError, match="quota exceeded"):
            gs.apply_plan(service, "cal-id", plan)


class TestLoadCredentials:
    def test_missing_file(self, tmp_path):
        with pytest.raises(gs.GoogleSyncError, match="cannot read credentials"):
            gs.load_credentials(tmp_path / "nope.json")

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "creds.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(gs.GoogleSyncError, match="cannot read credentials"):
            gs.load_credentials(path)

    def test_non_object_json(self, tmp_path):
        path = tmp_path / "creds.json"
        path.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(gs.GoogleSyncError, match="credentials JSON object"):
            gs.load_credentials(path)

    def test_unrecognized_credential_type(self, tmp_path):
        path = tmp_path / "creds.json"
        path.write_text('{"foo": "bar"}', encoding="utf-8")
        with pytest.raises(gs.GoogleSyncError, match="neither a service-account key"):
            gs.load_credentials(path)
