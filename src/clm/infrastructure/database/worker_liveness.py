"""The single definition of "which workers can claim this build's jobs".

Two components used to answer that question with two different rules, and
the gap between them aborted healthy builds (issue #917, symptom 2):

- The pool's health monitor treats a worker row's **status** as the liveness
  authority. It polls the worker *process* every cycle and marks a vanished
  one ``dead``; a stale ``workers.last_heartbeat`` on a ``busy`` row is
  expected, because workers only refresh that column while idle-polling and
  between jobs — never mid-job.
- The submission gate in the SQLite backend required a heartbeat under 30 s
  old. So the moment every worker of a type was mid-job for longer than
  that (routine for executed notebooks, and worse when heartbeat UPDATEs
  queue behind a contended jobs-DB lock), a submission saw *zero* workers,
  skipped the activation wait (nothing was ``created``), and raised
  "No workers available" — killing a build whose pool was perfectly healthy.

This module is the one place both sides now consult. The rule mirrors the
claim rule in :meth:`JobQueue.get_next_job` (issue #620): a job stamped with
session *S* is claimable by workers owned by *S* and by unowned (legacy /
externally started) workers; an unstamped job is claimable by anyone.

- A worker **owned by this session** counts while its status is ``idle`` or
  ``busy``. Its heartbeat age is irrelevant here: the owning pool's health
  monitor is running in this very process and is the component that turns
  a dead process into a ``dead`` row.
- An **unowned** worker has no monitor vouching for it, so it counts only
  with a heartbeat inside :data:`WORKER_HEARTBEAT_GRACE_SECONDS` on either
  channel — ``workers.last_heartbeat`` or the ``worker_heartbeats`` beacon
  a busy worker refreshes for the job it holds.
- A worker owned by **another** session never counts: it will never claim
  our jobs, so counting it would let a build submit into a void.

:func:`job_worker_alive_clause` asks the neighbouring question, "is the worker
holding this job still working on it?", for the hung-job reset (#799 C3). It
trusts the owning session's monitor the same way, but of the two heartbeat
channels it accepts only the beacon, because only the beacon names the job: a
worker that gave up on a job goes back to idle-polling, and its fresh
``last_heartbeat`` would otherwise vouch for that job forever.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# Grace period for the heartbeat channels of workers no health monitor vouches
# for. Generous enough to ride out short busy stretches (idle workers heartbeat
# every ~2 s; busy notebook workers write per-cell heartbeats), small enough
# that rows left behind by crashed processes are still reaped on the next
# build. Shared with the pool manager's stale-row cleanup so "alive" means the
# same thing to the cleaner and to the submission gate.
WORKER_HEARTBEAT_GRACE_SECONDS = 120

# Worker statuses that mean "a process is (as far as anyone knows) behind
# this row and will poll for jobs". 'created' is pre-registration (handled
# separately by the activation wait); 'hung' and 'dead' are terminal.
CLAIMING_STATUSES: tuple[str, ...] = ("idle", "busy")


def execution_mode_clause(execution_mode: str | None) -> tuple[str, tuple[Any, ...]]:
    """SQL fragment restricting ``workers`` rows to one execution mode.

    Same direct/docker discriminator ``WorkerDiscovery`` uses: Direct executor
    IDs are ``direct-<type>-<uuid>``; anything else (Docker container IDs,
    ``docker-<type>-<uuid>`` pre-registrations) is Docker. ``None`` matches
    every mode.
    """
    if execution_mode is None:
        return "", ()
    return (
        "AND (CASE WHEN w.container_id LIKE 'direct-%' THEN 'direct' ELSE 'docker' END) = ?",
        (execution_mode,),
    )


def _window(grace_seconds: int) -> str:
    """SQLite ``datetime('now', ?)`` modifier for "within *grace_seconds*"."""
    return f"-{int(grace_seconds)} seconds"


def _fresh_beacon_clause(
    worker_ref: str, grace_seconds: int, *, job_ref: str | None = None
) -> tuple[str, tuple[Any, ...]]:
    """``EXISTS`` a fresh ``worker_heartbeats`` beacon of *worker_ref*.

    *worker_ref* and *job_ref* are SQL column references from the enclosing
    query; with *job_ref*, the beacon must name that job.
    """
    job_sql = f" AND h.job_id = {job_ref}" if job_ref is not None else ""
    return (
        "EXISTS (SELECT 1 FROM worker_heartbeats h "
        f"WHERE h.worker_id = {worker_ref}{job_sql} "
        "AND h.heartbeat_at > datetime('now', ?))",
        (_window(grace_seconds),),
    )


def _fresh_heartbeat_clause(grace_seconds: int) -> tuple[str, tuple[Any, ...]]:
    beacon_sql, beacon_params = _fresh_beacon_clause("w.id", grace_seconds)
    return (
        f"(w.last_heartbeat > datetime('now', ?) OR {beacon_sql})",
        (_window(grace_seconds), *beacon_params),
    )


def _claimable_for_session_clause(
    session_id: str | None, *, grace_seconds: int
) -> tuple[str, tuple[Any, ...]]:
    """Ownership predicate: which rows may claim jobs stamped *session_id*.

    With a session: rows owned by it (status is authoritative) or unowned rows
    with a fresh heartbeat. Without one (legacy / test callers whose jobs are
    unstamped and claimable by anyone): any row with a fresh heartbeat.
    """
    fresh_sql, fresh_params = _fresh_heartbeat_clause(grace_seconds)
    if session_id is None:
        return fresh_sql, fresh_params
    return (
        f"(w.session_id = ? OR (w.session_id IS NULL AND {fresh_sql}))",
        (session_id, *fresh_params),
    )


def job_worker_alive_clause(
    session_id: str | None, *, grace_seconds: int
) -> tuple[str, tuple[Any, ...]]:
    """SQL predicate: the worker holding a row of ``jobs`` is still working on it.

    For an ``UPDATE jobs`` or ``SELECT ... FROM jobs`` without an alias. The
    worker is alive if either holds:

    - its beacon for this job is fresher than *grace_seconds*. Every worker
      refreshes it every :data:`~clm.infrastructure.database.worker_heartbeats.JOB_BEACON_INTERVAL_SECONDS`
      while it holds the job, however long a cell stays silent. This is checked
      without needing a ``workers`` row, because another build's stale-row
      cleanup can delete a live worker's row (#799 C2);
    - its row is ``idle``/``busy`` and owned by *session_id*, whose pool
      monitor runs in the calling process and marks a vanished process
      ``dead``. This also covers workers that predate the beacon.

    ``workers.last_heartbeat`` doesn't count: it says the process is alive, not
    that it is still on this job. A job with no worker id, no fresh beacon and
    no owned row counts as abandoned.
    """
    beacon_sql, beacon_params = _fresh_beacon_clause(
        "jobs.worker_id", grace_seconds, job_ref="jobs.id"
    )
    if session_id is None:
        return f"({beacon_sql})", beacon_params
    status_placeholders = ",".join("?" * len(CLAIMING_STATUSES))
    return (
        f"""(
            {beacon_sql}
            OR EXISTS (
                SELECT 1 FROM workers w
                WHERE w.id = jobs.worker_id
                  AND w.status IN ({status_placeholders})
                  AND w.session_id = ?
            )
        )""",
        (*beacon_params, *CLAIMING_STATUSES, session_id),
    )


def count_available_workers(
    conn: sqlite3.Connection,
    job_type: str,
    *,
    execution_mode: str | None = None,
    session_id: str | None = None,
    grace_seconds: int = WORKER_HEARTBEAT_GRACE_SECONDS,
) -> int:
    """Number of workers that can claim a *job_type* job of this session.

    See the module docstring for the rule. This is a single indexed COUNT and
    is safe to call from any thread with its own connection.
    """
    mode_sql, mode_params = execution_mode_clause(execution_mode)
    own_sql, own_params = _claimable_for_session_clause(session_id, grace_seconds=grace_seconds)
    status_placeholders = ",".join("?" * len(CLAIMING_STATUSES))
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM workers w
        WHERE w.worker_type = ?
          AND w.status IN ({status_placeholders})
          {mode_sql}
          AND {own_sql}
        """,  # noqa: S608 — literal predicates, bound params
        (job_type, *CLAIMING_STATUSES, *mode_params, *own_params),
    ).fetchone()
    return int(row[0]) if row else 0


def fresh_job_beacon_clause(
    worker_ref: str, job_ref: str, *, grace_seconds: int = WORKER_HEARTBEAT_GRACE_SECONDS
) -> tuple[str, tuple[Any, ...]]:
    """``EXISTS`` a beacon fresher than *grace_seconds* from *worker_ref* naming *job_ref*.

    *worker_ref* and *job_ref* are SQL column references of the enclosing query.
    Needs no ``workers`` row, so it also answers for a worker whose row is gone.
    """
    return _fresh_beacon_clause(worker_ref, grace_seconds, job_ref=job_ref)


def live_sessions(
    conn: sqlite3.Connection, *, grace_seconds: int = WORKER_HEARTBEAT_GRACE_SECONDS
) -> set[str]:
    """Sessions with at least one worker showing life on either heartbeat channel.

    Such a session's build is running, and its own pool monitor looks after its
    rows. Another build must not delete them: a row whose heartbeat looks stale
    may be a busy worker in a long silent cell (#799 C2). A session in which
    no worker shows life within *grace_seconds* belongs to a build that is gone.
    """
    fresh_sql, fresh_params = _fresh_heartbeat_clause(grace_seconds)
    rows = conn.execute(
        f"""
        SELECT DISTINCT w.session_id FROM workers w
        WHERE w.session_id IS NOT NULL AND {fresh_sql}
        """,  # noqa: S608 — literal predicates, bound params
        fresh_params,
    ).fetchall()
    return {row[0] for row in rows}


def count_other_session_workers(
    conn: sqlite3.Connection,
    job_type: str,
    *,
    execution_mode: str | None,
    session_id: str,
) -> int:
    """Diagnostic: live *job_type* workers owned by a *different* session.

    They can never claim this session's jobs, so :func:`count_available_workers`
    ignores them — but their presence is the most likely explanation when a
    build finds no claimable worker although ``clm status`` shows a pool
    (persistent workers left running by an earlier build, for instance), so
    the "no workers" error names them.
    """
    mode_sql, mode_params = execution_mode_clause(execution_mode)
    status_placeholders = ",".join("?" * len(CLAIMING_STATUSES))
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM workers w
        WHERE w.worker_type = ?
          AND w.status IN ({status_placeholders})
          {mode_sql}
          AND w.session_id IS NOT NULL AND w.session_id != ?
        """,  # noqa: S608 — literal predicates, bound params
        (job_type, *CLAIMING_STATUSES, *mode_params, session_id),
    ).fetchone()
    return int(row[0]) if row else 0


def count_workers_awaiting_activation(
    conn: sqlite3.Connection,
    job_type: str,
    *,
    execution_mode: str | None = None,
    session_id: str | None = None,
) -> int:
    """Number of pre-registered (``created``) workers that would serve us.

    A ``created`` row has no heartbeat yet by definition, so ownership alone
    decides: with a session, rows owned by it or unowned; without one, any.
    """
    mode_sql, mode_params = execution_mode_clause(execution_mode)
    own_sql = ""
    own_params: tuple[Any, ...] = ()
    if session_id is not None:
        own_sql = "AND (w.session_id = ? OR w.session_id IS NULL)"
        own_params = (session_id,)
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM workers w
        WHERE w.worker_type = ?
          AND w.status = 'created'
          {mode_sql}
          {own_sql}
        """,  # noqa: S608 — literal predicates, bound params
        (job_type, *mode_params, *own_params),
    ).fetchone()
    return int(row[0]) if row else 0
