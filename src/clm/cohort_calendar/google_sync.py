"""Push a projected cohort calendar to Google Calendar (``clm calendar push``).

The push is a one-way mirror of a :class:`~clm.cohort_calendar.projection.Projection`
into a set of **CLM-managed** events. Every event we create carries two private
extended properties:

* ``clm_managed=<namespace>`` — the cohort (channel) this event belongs to, and
  the filter used when listing existing events;
* ``clm_uid=<uid>`` — the same stable per-assignment UID the ``.ics`` export
  uses (:func:`clm.cohort_calendar.render.assignment_uid`), so re-pushing after
  a schedule change updates events in place instead of duplicating them.

Sync semantics: desired events are diffed against the managed events already in
the calendar — new UIDs are inserted, changed ones updated, vanished ones
deleted. Events *without* the ``clm_managed`` tag (the trainer's own entries in
the same calendar) are never listed, modified, or deleted.

Two credential types are accepted, auto-detected from the JSON file:

* an OAuth "Desktop app" client (``installed``/``web`` key) — a browser consent
  flow runs once, then the token is cached under the user config dir;
* a service-account key (``"type": "service_account"``) — no browser; share the
  target calendar with the service account's email ("make changes") instead.

All Google-API imports happen inside functions, so this module — and its pure
planning half — imports and tests without the ``[gcal]`` extra installed.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import platformdirs
from attrs import frozen

from clm.cohort_calendar.projection import Projection
from clm.cohort_calendar.render import assignment_body, assignment_summary, assignment_uid

logger = logging.getLogger(__name__)

#: Private extended-property keys stamped on every event CLM manages.
MANAGED_KEY = "clm_managed"
UID_KEY = "clm_uid"

#: Events-only scope — pushing needs event CRUD, never calendar-list management.
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

#: Transient HTTP statuses worth retrying: rate-limit + the 5xx backend errors
#: Google routinely returns mid-push for a 100+ event sync.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
#: Retries *after* the first attempt, and the base (doubling) backoff delay.
MAX_RETRIES = 5
_RETRY_BASE_DELAY = 1.0  # seconds; attempt n waits _RETRY_BASE_DELAY * 2**n

_INSTALL_HINT = (
    "Google Calendar push requires the [gcal] extra: "
    'pip install "coding-academy-lecture-manager[gcal]"'
)


class GoogleSyncError(Exception):
    """A dependency, credential, or API failure during a Google Calendar push."""


# --- pure planning half (no Google imports) -----------------------------------


@frozen
class SyncPlan:
    """The diff between the desired events and the managed events in the calendar."""

    inserts: tuple[dict[str, Any], ...]
    updates: tuple[tuple[str, dict[str, Any]], ...]  # (event_id, desired body)
    deletes: tuple[tuple[str, str], ...]  # (event_id, display label)
    unchanged: int

    @property
    def is_noop(self) -> bool:
        return not (self.inserts or self.updates or self.deletes)


def build_desired_events(
    projection: Projection, *, namespace: str, language: str = "de"
) -> dict[str, dict[str, Any]]:
    """Map each assignment to a Google event body, keyed by its stable UID.

    Events are all-day (``start.date`` / exclusive ``end.date``, mirroring the
    ``.ics`` DTEND convention) and marked ``transparent`` so they never block
    students' free/busy time. The ``summary`` is the short event title (first
    deck + "+N more"); the ``description`` carries the section title and the
    numbered slide list — the same text the ``.ics`` export emits.
    """
    desired: dict[str, dict[str, Any]] = {}
    for a in projection.assignments:
        uid = assignment_uid(a, namespace)
        if uid in desired:
            logger.warning("duplicate event UID %s; keeping the later assignment.", uid)
        body: dict[str, Any] = {
            "summary": assignment_summary(a, language) or (a.label or ""),
            "start": {"date": a.start_date.isoformat()},
            "end": {"date": (a.end_date + dt.timedelta(days=1)).isoformat()},
            "transparency": "transparent",
            "extendedProperties": {"private": {MANAGED_KEY: namespace, UID_KEY: uid}},
        }
        description = assignment_body(a, language)
        if description:
            body["description"] = description
        desired[uid] = body
    return desired


def _event_uid(event: dict[str, Any]) -> str | None:
    uid = event.get("extendedProperties", {}).get("private", {}).get(UID_KEY)
    return uid if isinstance(uid, str) else None


def _display(event: dict[str, Any]) -> str:
    date = (event.get("start") or {}).get("date", "")
    return f"{date}  {event.get('summary', '')}".strip()


def _needs_update(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    for field in ("start", "end"):
        if (existing.get(field) or {}).get("date") != desired[field]["date"]:
            return True
    for field in ("summary", "description"):
        if (existing.get(field) or "") != (desired.get(field) or ""):
            return True
    return False


def plan_sync(desired: dict[str, dict[str, Any]], existing: list[dict[str, Any]]) -> SyncPlan:
    """Diff *desired* event bodies against the *existing* managed events.

    Managed events whose UID is missing, duplicated, or no longer desired are
    deleted; the rest are matched by UID and updated only when a compared field
    (summary, description, dates) differs.
    """
    by_uid: dict[str, dict[str, Any]] = {}
    deletes: list[tuple[str, str]] = []
    for event in existing:
        uid = _event_uid(event)
        if uid and uid in desired and uid not in by_uid:
            by_uid[uid] = event
        else:
            deletes.append((event["id"], _display(event)))

    inserts: list[dict[str, Any]] = []
    updates: list[tuple[str, dict[str, Any]]] = []
    unchanged = 0
    for uid, body in desired.items():
        matched = by_uid.get(uid)
        if matched is None:
            inserts.append(body)
        elif _needs_update(matched, body):
            updates.append((matched["id"], body))
        else:
            unchanged += 1
    return SyncPlan(tuple(inserts), tuple(updates), tuple(deletes), unchanged)


def describe_plan(plan: SyncPlan) -> list[str]:
    """Human-readable plan lines: ``+`` insert, ``~`` update, ``-`` delete."""
    lines = [f"+ {_display(body)}" for body in plan.inserts]
    lines += [f"~ {_display(body)}" for _id, body in plan.updates]
    lines += [f"- {label}" for _id, label in plan.deletes]
    return lines


# --- Google API half (lazy imports) --------------------------------------------


def _import_gcal(module: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise GoogleSyncError(f"{_INSTALL_HINT} (missing {module}).") from exc


def default_token_cache() -> Path:
    """Where the OAuth user token is cached between runs."""
    return Path(platformdirs.user_config_dir("clm", appauthor=False)) / "google-calendar-token.json"


def load_credentials(credentials_path: Path, *, token_cache: Path | None = None) -> Any:
    """Load Google credentials, auto-detecting the JSON's credential type."""
    try:
        data = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoogleSyncError(f"cannot read credentials {credentials_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GoogleSyncError(f"{credentials_path} does not contain a credentials JSON object.")
    if data.get("type") == "service_account":
        service_account = _import_gcal("google.oauth2.service_account")
        return service_account.Credentials.from_service_account_file(
            str(credentials_path), scopes=SCOPES
        )
    if "installed" in data or "web" in data:
        return _oauth_user_credentials(credentials_path, token_cache or default_token_cache())
    raise GoogleSyncError(
        f"{credentials_path} is neither a service-account key "
        '("type": "service_account") nor an OAuth client ("installed"/"web").'
    )


def _oauth_user_credentials(client_secrets: Path, token_cache: Path) -> Any:
    """Cached-token OAuth flow: refresh if possible, else run the browser consent."""
    oauth2_credentials = _import_gcal("google.oauth2.credentials")
    flow_module = _import_gcal("google_auth_oauthlib.flow")
    transport = _import_gcal("google.auth.transport.requests")

    creds = None
    if token_cache.exists():
        try:
            creds = oauth2_credentials.Credentials.from_authorized_user_file(
                str(token_cache), SCOPES
            )
        except ValueError:
            creds = None  # corrupt/stale cache — fall through to the consent flow
    if creds is not None and creds.expired and creds.refresh_token:
        try:
            creds.refresh(transport.Request())
        except Exception as exc:
            logger.info("token refresh failed (%s); re-running the consent flow.", exc)
            creds = None
    if creds is None or not creds.valid:
        flow = flow_module.InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
        logger.info("Opening a browser for Google OAuth consent ...")
        creds = flow.run_local_server(port=0)
        token_cache.parent.mkdir(parents=True, exist_ok=True)
        token_cache.write_text(creds.to_json(), encoding="utf-8")
        logger.info("OAuth token cached at %s", token_cache)
    return creds


def build_service(credentials: Any) -> Any:
    """A Google Calendar v3 service client."""
    discovery = _import_gcal("googleapiclient.discovery")
    return discovery.build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _transient_status(exc: Exception) -> int | None:
    """The HTTP status of *exc* if it is a retryable transient error, else None.

    Duck-typed so this module needs no ``googleapiclient`` import: an
    ``HttpError`` exposes the status as ``exc.resp.status`` (and newer versions
    as ``exc.status_code``).
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    try:
        status = int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return status if status in _TRANSIENT_STATUS else None


def _execute(request: Any) -> Any:
    """Execute a Google API request, retrying transient 429/5xx with backoff.

    A single push can issue 100+ event mutations; Google intermittently returns
    a ``503 backendError`` on one of them. Without retry that aborts the whole
    push mid-run (leaving a partial calendar that the next run reconciles, but
    still a failure). Retries are bounded (:data:`MAX_RETRIES`) with exponential
    backoff; non-transient errors (e.g. 404/403) raise immediately.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return request.execute()
        except GoogleSyncError:
            raise
        except Exception as exc:
            status = _transient_status(exc)
            if status is None or attempt == MAX_RETRIES:
                raise GoogleSyncError(f"Google Calendar API request failed: {exc}") from exc
            delay = _RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "Google Calendar API %s (attempt %d/%d); retrying in %.1fs.",
                status,
                attempt + 1,
                MAX_RETRIES + 1,
                delay,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def fetch_managed_events(service: Any, calendar_id: str, namespace: str) -> list[dict[str, Any]]:
    """All non-cancelled events in *calendar_id* that CLM manages for *namespace*."""
    events: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        response = _execute(
            service.events().list(
                calendarId=calendar_id,
                privateExtendedProperty=f"{MANAGED_KEY}={namespace}",
                maxResults=2500,
                pageToken=page_token,
            )
        )
        events.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return events


def apply_plan(service: Any, calendar_id: str, plan: SyncPlan) -> None:
    """Execute a :class:`SyncPlan` against the calendar (inserts, updates, deletes)."""
    for body in plan.inserts:
        _execute(service.events().insert(calendarId=calendar_id, body=body))
    for event_id, body in plan.updates:
        _execute(service.events().update(calendarId=calendar_id, eventId=event_id, body=body))
    for event_id, _label in plan.deletes:
        _execute(service.events().delete(calendarId=calendar_id, eventId=event_id))
