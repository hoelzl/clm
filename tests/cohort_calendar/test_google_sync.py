"""Tests for the Google Calendar push: planning, formatting, apply (``calendar push``).

Everything here runs without the ``[gcal]`` extra — the planning half is pure,
and the apply half takes an injected (fake) service object.
"""

import datetime as dt
import os
import stat
from pathlib import Path
from types import SimpleNamespace

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

    def test_same_stem_decks_yield_two_events_not_one(self):
        # Issue #436: two distinct decks sharing the slide-file stem
        # "slides_010_review.de" must produce two events, not collide into one.
        proj = Projection(
            (
                Assignment(
                    dt.date(2026, 3, 2),
                    dt.date(2026, 3, 2),
                    (deck("Review W1-6", "review_w01_w06", "slides_010_review.de"),),
                    None,
                    "video",
                    ("module_545_foundations/review_w01_w06/slides_010_review.de",),
                    section_title="W01",
                ),
                Assignment(
                    dt.date(2026, 3, 9),
                    dt.date(2026, 3, 9),
                    (deck("Review W5-9", "review_w05_w09", "slides_010_review.de"),),
                    None,
                    "video",
                    ("module_550_ml_azav/review_w05_w09/slides_010_review.de",),
                    section_title="W06",
                ),
            ),
            (),
        )
        desired = gs.build_desired_events(proj, namespace="jan")
        assert len(desired) == 2
        # Both survive into a sync plan (neither silently dropped).
        assert len(gs.plan_sync(desired, []).inserts) == 2

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

    def test_oauth_token_cache_is_chmod_0600(self, tmp_path, monkeypatch):
        class Credentials:
            valid = True

            def to_json(self):
                return '{"refresh_token": "secret"}'

        credentials = Credentials()

        class InstalledAppFlow:
            @staticmethod
            def from_client_secrets_file(_path, _scopes):
                return SimpleNamespace(run_local_server=lambda **_kwargs: credentials)

        modules = {
            "google.oauth2.credentials": SimpleNamespace(Credentials=object()),
            "google_auth_oauthlib.flow": SimpleNamespace(InstalledAppFlow=InstalledAppFlow),
            "google.auth.transport.requests": SimpleNamespace(Request=object),
        }
        monkeypatch.setattr(gs, "_import_gcal", modules.__getitem__)
        chmod_calls = []
        real_chmod = Path.chmod

        def record_chmod(path: Path, mode: int) -> None:
            chmod_calls.append((path, mode))
            real_chmod(path, mode)

        monkeypatch.setattr(Path, "chmod", record_chmod)
        token_cache = tmp_path / "nested" / "token.json"

        result = gs._oauth_user_credentials(tmp_path / "client.json", token_cache)

        assert result is credentials
        assert token_cache.read_text(encoding="utf-8") == '{"refresh_token": "secret"}'
        assert len(chmod_calls) == 1
        assert chmod_calls[0][0] != token_cache
        assert chmod_calls[0][1] == 0o600
        if os.name != "nt":
            assert stat.S_IMODE(token_cache.stat().st_mode) == 0o600

    def test_existing_oauth_token_cache_permissions_are_repaired(self, tmp_path, monkeypatch):
        token_cache = tmp_path / "token.json"
        token_cache.write_text('{"refresh_token": "existing"}', encoding="utf-8")
        credentials = SimpleNamespace(valid=True, expired=False, refresh_token="existing")
        loader = SimpleNamespace(from_authorized_user_info=lambda *_args, **_kwargs: credentials)
        modules = {
            "google.oauth2.credentials": SimpleNamespace(Credentials=loader),
            "google_auth_oauthlib.flow": SimpleNamespace(InstalledAppFlow=object()),
            "google.auth.transport.requests": SimpleNamespace(Request=object),
        }
        monkeypatch.setattr(gs, "_import_gcal", modules.__getitem__)
        fchmod_calls = []
        real_fchmod = os.fchmod

        def record_fchmod(fd: int, mode: int) -> None:
            fchmod_calls.append((fd, mode))
            real_fchmod(fd, mode)

        monkeypatch.setattr(os, "fchmod", record_fchmod)

        result = gs._oauth_user_credentials(tmp_path / "client.json", token_cache)

        assert result is credentials
        assert len(fchmod_calls) == 1
        assert fchmod_calls[0][1] == 0o600

    def test_refreshed_oauth_token_is_persisted_privately(self, tmp_path, monkeypatch):
        token_cache = tmp_path / "token.json"
        token_cache.write_text('{"access_token": "stale"}', encoding="utf-8")

        class Credentials:
            valid = False
            expired = True
            refresh_token = "refresh"

            def refresh(self, _request) -> None:
                self.valid = True

            def to_json(self) -> str:
                return '{"access_token": "fresh"}'

        credentials = Credentials()
        loader = SimpleNamespace(from_authorized_user_info=lambda *_args, **_kwargs: credentials)
        modules = {
            "google.oauth2.credentials": SimpleNamespace(Credentials=loader),
            "google_auth_oauthlib.flow": SimpleNamespace(InstalledAppFlow=object()),
            "google.auth.transport.requests": SimpleNamespace(Request=object),
        }
        monkeypatch.setattr(gs, "_import_gcal", modules.__getitem__)
        chmod_calls = []
        real_chmod = Path.chmod

        def record_chmod(path: Path, mode: int) -> None:
            chmod_calls.append((path, mode))
            real_chmod(path, mode)

        monkeypatch.setattr(Path, "chmod", record_chmod)

        result = gs._oauth_user_credentials(tmp_path / "client.json", token_cache)

        assert result is credentials
        assert token_cache.read_text(encoding="utf-8") == '{"access_token": "fresh"}'
        assert len(chmod_calls) == 1
        assert chmod_calls[0][0] != token_cache
        assert chmod_calls[0][1] == 0o600

    def test_refresh_persistence_failure_does_not_restart_consent(self, tmp_path, monkeypatch):
        token_cache = tmp_path / "token.json"
        token_cache.write_text('{"access_token": "stale"}', encoding="utf-8")

        class Credentials:
            valid = False
            expired = True
            refresh_token = "refresh"

            def refresh(self, _request) -> None:
                self.valid = True

        credentials = Credentials()
        loader = SimpleNamespace(from_authorized_user_info=lambda *_args, **_kwargs: credentials)
        consent_started = False

        class InstalledAppFlow:
            @staticmethod
            def from_client_secrets_file(_path, _scopes):
                nonlocal consent_started
                consent_started = True
                raise AssertionError("consent must not restart after a successful refresh")

        modules = {
            "google.oauth2.credentials": SimpleNamespace(Credentials=loader),
            "google_auth_oauthlib.flow": SimpleNamespace(InstalledAppFlow=InstalledAppFlow),
            "google.auth.transport.requests": SimpleNamespace(Request=object),
        }
        monkeypatch.setattr(gs, "_import_gcal", modules.__getitem__)

        def fail_persistence(_creds, _token_cache):
            raise PermissionError("cache is not writable")

        monkeypatch.setattr(gs, "_cache_oauth_credentials", fail_persistence)

        with pytest.raises(PermissionError, match="not writable"):
            gs._oauth_user_credentials(tmp_path / "client.json", token_cache)

        assert credentials.valid is True
        assert consent_started is False

    @pytest.mark.skipif(os.name == "nt", reason="Creating file symlinks needs Windows privileges")
    def test_oauth_token_cache_replaces_symlink_not_target(self, tmp_path):
        victim = tmp_path / "victim.json"
        victim.write_text("bystander", encoding="utf-8")
        token_cache = tmp_path / "token.json"
        token_cache.symlink_to(victim)
        credentials = SimpleNamespace(to_json=lambda: '{"refresh_token": "private"}')

        gs._cache_oauth_credentials(credentials, token_cache)

        assert victim.read_text(encoding="utf-8") == "bystander"
        assert token_cache.read_text(encoding="utf-8") == '{"refresh_token": "private"}'
        assert not token_cache.is_symlink()
        assert stat.S_IMODE(token_cache.stat().st_mode) == 0o600

    def test_partial_oauth_token_write_removes_private_temporary(self, tmp_path, monkeypatch):
        real_named_temporary_file = gs.tempfile.NamedTemporaryFile

        class PartialWriter:
            def __init__(self, *args, **kwargs):
                self._context = real_named_temporary_file(*args, **kwargs)

            def __enter__(self):
                self._file = self._context.__enter__()
                self.name = self._file.name
                return self

            def write(self, value):
                self._file.write(value[:8])
                self._file.flush()
                raise OSError("simulated partial write")

            def __exit__(self, *args):
                return self._context.__exit__(*args)

        monkeypatch.setattr(gs.tempfile, "NamedTemporaryFile", PartialWriter)
        credentials = SimpleNamespace(to_json=lambda: '{"refresh_token": "private"}')

        with pytest.raises(OSError, match="partial write"):
            gs._cache_oauth_credentials(credentials, tmp_path / "token.json")

        assert list(tmp_path.iterdir()) == []

    def test_existing_oauth_token_symlink_is_rejected_without_touching_target(
        self, tmp_path, monkeypatch
    ):
        victim = tmp_path / "victim.json"
        victim.write_text('{"refresh_token": "bystander"}', encoding="utf-8")
        original_mode = stat.S_IMODE(victim.stat().st_mode)
        token_cache = tmp_path / "token.json"
        try:
            token_cache.symlink_to(victim)
        except OSError:
            pytest.skip("Creating file symlinks needs Windows developer mode or privileges")
        modules = {
            "google.oauth2.credentials": SimpleNamespace(Credentials=object()),
            "google_auth_oauthlib.flow": SimpleNamespace(InstalledAppFlow=object()),
            "google.auth.transport.requests": SimpleNamespace(Request=object),
        }
        monkeypatch.setattr(gs, "_import_gcal", modules.__getitem__)

        with pytest.raises(gs.GoogleSyncError, match="symbolic link"):
            gs._oauth_user_credentials(tmp_path / "client.json", token_cache)

        assert token_cache.is_symlink()
        assert victim.read_text(encoding="utf-8") == '{"refresh_token": "bystander"}'
        assert stat.S_IMODE(victim.stat().st_mode) == original_mode

    def test_oauth_token_swap_during_open_is_rejected_without_touching_target(
        self, tmp_path, monkeypatch
    ):
        token_cache = tmp_path / "token.json"
        token_cache.write_text('{"refresh_token": "original"}', encoding="utf-8")
        victim = tmp_path / "victim.json"
        victim.write_text('{"refresh_token": "bystander"}', encoding="utf-8")
        original_mode = stat.S_IMODE(victim.stat().st_mode)
        real_open = os.open

        def swap_before_open(path, flags):
            token_cache.unlink()
            try:
                token_cache.symlink_to(victim)
            except OSError:
                pytest.skip("Creating file symlinks needs Windows developer mode or privileges")
            return real_open(path, flags)

        monkeypatch.setattr(gs.os, "open", swap_before_open)
        modules = {
            "google.oauth2.credentials": SimpleNamespace(Credentials=object()),
            "google_auth_oauthlib.flow": SimpleNamespace(InstalledAppFlow=object()),
            "google.auth.transport.requests": SimpleNamespace(Request=object),
        }
        monkeypatch.setattr(gs, "_import_gcal", modules.__getitem__)

        with pytest.raises(gs.GoogleSyncError, match="symbolic link|changed while opening"):
            gs._oauth_user_credentials(tmp_path / "client.json", token_cache)

        assert victim.read_text(encoding="utf-8") == '{"refresh_token": "bystander"}'
        assert stat.S_IMODE(victim.stat().st_mode) == original_mode
