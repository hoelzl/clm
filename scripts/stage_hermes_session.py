#!/usr/bin/env python3
"""Stage a Hermes session — including archived pre-compaction turns — to disk.

Why this exists: Hermes keeps every message row across compaction
(`messages.active=0, compacted=1`), but **no CLI export path reaches the
archived rows** — `hermes sessions export` returns the active view only, and
`--lineage logical` selects session lineage, not archived messages (verified
on Hermes 2026-08-23). A session that crossed a compaction boundary between
two saves loses that window to exactly this gap: the bytes are in the DB, the
interface will not hand them back.

This script reads the profile `state.db` read-only (URI `mode=ro`, no locks,
safe against a live session) and writes the same JSON shape
`hermes sessions export --format jsonl` produces — one session object with a
`messages` list — so `scripts/clean_transcript.py` processes the result
unchanged. Two differences from the CLI export, both deliberate:

  - **every row is included**, archived and active, in rowid (chronological)
    order, with each message's `active`/`compacted` flags preserved;
  - the object carries `"full_lineage": true` so a later reader can tell a
    complete staging from a CLI export that silently dropped the archive.

Wiring (the hook half lives in the profile config, not this repo). The
config shape is a flat dict keyed by event name under `hooks:` in the
profile's `config.yaml`:

    hooks:
      on_session_end:
        - command: python <repo>/scripts/stage_hermes_session.py
          timeout: 30

`on_session_end` fires at the end of **every turn**, not once at session
death — for staging that is the desired property: a snapshot per turn means
compaction can never strand more than the current turn. The hook fails open
by design (spawn/timeout errors log a warning and contribute nothing), and
this script's hook mode additionally always exits 0, so a staging failure can
never disturb the session. First use requires consent:
`hermes hooks test on_session_end` fires a synthetic payload through the real
wiring, which both verifies the script and records the consent.

An existing staged copy is replaced for more messages, or equal-count metadata
enrichment preserving every existing message field. This lets a closed session
gain newly supported public-message fields without replacing prior dialogue.
A smaller result or equal-count changed content leaves the file untouched.

Modes:
  - hook mode (default, JSON payload on stdin): never exits non-zero; failures
    print to stderr and return 0 so the harness ignores them.
  - `--session-id ID` (manual): errors exit non-zero; `--strict` additionally
    fails when nothing was staged.

Standard library only.
"""

import json
import os
import pathlib
import sqlite3
import sys
import tempfile

STAGING_DIR = pathlib.Path.home() / ".clm-transcripts"


def resolve_databases(explicit):
    """Candidate profile state.db files, most specific first.

    Hermes profile homes differ by platform: ``$HERMES_HOME`` when set, then
    the Windows install layout ``%LOCALAPPDATA%/hermes/profiles/*/state.db``,
    then the POSIX layout ``~/.hermes/profiles/*/state.db``. A session may
    live in any profile (work happens under several), so all of them are
    searched, not just the current one.
    """
    if explicit:
        return [pathlib.Path(explicit).expanduser()]
    home = os.environ.get("HERMES_HOME")
    if home:
        return [pathlib.Path(home).expanduser() / "state.db"]
    roots = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(pathlib.Path(local_app_data) / "hermes" / "profiles")
    roots.append(pathlib.Path.home() / ".hermes" / "profiles")
    databases = []
    for root in roots:
        if root.is_dir():
            databases.extend(
                sorted(p / "state.db" for p in root.iterdir() if (p / "state.db").is_file())
            )
    return databases


def load_session(db_path, session_id):
    """Read one session (all rows, archived included) read-only."""
    uri = "file:{}?mode=ro".format(db_path.as_posix().replace("?", "%3f").replace("#", "%23"))
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.cursor()
        # Column names vary across Hermes versions (provider/created_at vs
        # billing_provider/started_at); take what this schema offers, and
        # keep the DATABASE column names — clean_transcript.py reads
        # last_activity_at/started_at by those exact names.
        available = {r[1] for r in cur.execute("PRAGMA table_info(sessions)")}
        wanted = [
            "id",
            "title",
            "model",
            "billing_provider",
            "started_at",
            "last_activity_at",
            "cwd",
        ]
        picks = [c for c in wanted if c in available]
        row = cur.execute(
            "SELECT {} FROM sessions WHERE id = ?".format(", ".join(picks)),
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        session = dict(zip(picks, row, strict=False))
        session["full_lineage"] = True
        session["messages"] = []
        msg_cols = {r[1] for r in cur.execute("PRAGMA table_info(messages)")}
        msg_wanted = [
            ("id", "id"),
            ("role", "role"),
            ("content", "content"),
            ("tool_call_id", "tool_call_id"),
            ("tool_calls", "tool_calls"),
            ("tool_name", "tool_name"),
            ("timestamp", "timestamp"),
            ("token_count", "token_count"),
            ("finish_reason", "finish_reason"),
            ("reasoning", "reasoning"),
            ("active", "active"),
            ("compacted", "compacted"),
            ("_compressed_summary", "_compressed_summary"),
            ("codex_message_items", "codex_message_items"),
        ]
        msg_picks = [c for _a, c in msg_wanted if c in msg_cols]
        keys = [alias for alias, col in msg_wanted if col in msg_cols]
        for row in cur.execute(
            "SELECT {} FROM messages WHERE session_id = ? ORDER BY rowid".format(
                ", ".join(msg_picks)
            ),
            (session_id,),
        ):
            message = dict(zip(keys, row, strict=False))
            if message.get("tool_calls"):
                try:
                    message["tool_calls"] = json.loads(message["tool_calls"])
                except (json.JSONDecodeError, TypeError):
                    pass
            for flag in ("active", "compacted", "_compressed_summary"):
                if flag in message:
                    message[flag] = bool(message[flag])
            session["messages"].append(message)
        return session
    finally:
        con.close()


def stage(session, staging_dir=STAGING_DIR):
    """Write the session object unless a larger copy is already staged.

    Returns (path or None, note) — the note is human-readable and testable.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    target = staging_dir / ("{}.jsonl".format(session["id"]))
    payload = json.dumps(session, ensure_ascii=False, default=str)
    new_count = len(session["messages"])

    if target.exists():
        try:
            old_messages = json.loads(target.read_text(encoding="utf-8"))["messages"]
            old_count = len(old_messages)
            enriched = (
                old_count == new_count
                and old_messages != session["messages"]
                and all(
                    all(key in new and new[key] == value for key, value in old.items())
                    for old, new in zip(old_messages, session["messages"], strict=False)
                )
            )
        except (json.JSONDecodeError, KeyError, OSError):
            old_count = -1
            enriched = False
        if old_count >= new_count and not enriched:
            return None, f"kept existing staging ({old_count} msgs >= {new_count})"

    fd, tmp = tempfile.mkstemp(dir=str(staging_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    archived = sum(1 for m in session["messages"] if not m["active"])
    return target, f"staged {new_count} messages ({archived} archived)"


def find_and_stage(session_id, databases, staging_dir=STAGING_DIR):
    for db_path in databases:
        if not db_path.is_file():
            continue
        try:
            session = load_session(db_path, session_id)
        except sqlite3.Error as exc:
            print(f"stage_hermes_session: {db_path} unreadable ({exc})", file=sys.stderr)
            continue
        if session is not None:
            return stage(session, staging_dir)
    return None, f"session {session_id} not found in {len(databases)} database(s)"


def run_self_test():
    import shutil

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="stage-hermes-selftest-"))
    try:
        db = tmp / "state.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, model TEXT, provider TEXT, created_at TEXT, updated_at TEXT, cwd TEXT)"
        )
        con.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL, token_count INTEGER, finish_reason TEXT, reasoning TEXT, active INTEGER, compacted INTEGER)"
        )
        sid = "s-test-1"
        con.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)", (sid, "t", "m", "p", "0", "0", "/x")
        )
        con.execute(
            "INSERT INTO messages (session_id, role, content, active, compacted) VALUES (?,?,?,?,?)",
            (sid, "user", "archived owner turn", 0, 1),
        )
        con.execute(
            "INSERT INTO messages (session_id, role, content, active, compacted) VALUES (?,?,?,?,?)",
            (sid, "assistant", "archived reply", 0, 1),
        )
        con.execute(
            "INSERT INTO messages (session_id, role, content, active, compacted) VALUES (?,?,?,?,?)",
            (sid, "user", "[CONTEXT COMPACTION", 1, 0),
        )
        con.execute(
            "INSERT INTO messages (session_id, role, content, active, compacted) VALUES (?,?,?,?,?)",
            (sid, "user", "live owner turn", 1, 0),
        )
        con.commit()
        con.close()

        staging = tmp / "staging"
        session = load_session(db, sid)
        assert session is not None and session["full_lineage"] is True
        assert [m["content"] for m in session["messages"]] == [
            "archived owner turn",
            "archived reply",
            "[CONTEXT COMPACTION",
            "live owner turn",
        ], "rowid order must interleave archived before active, chronologically"
        assert (
            session["messages"][0]["active"] is False and session["messages"][3]["active"] is True
        )

        path, note = stage(session, staging)
        assert path is not None and "2 archived" in note, note
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["id"] == sid and len(written["messages"]) == 4

        # Non-narrowing: a smaller re-read must not replace the staged copy.
        smaller = dict(session, messages=session["messages"][:2])
        path2, note2 = stage(smaller, staging)
        assert path2 is None and "kept existing" in note2, note2
        assert (
            len(json.loads((staging / (f"{sid}.jsonl")).read_text(encoding="utf-8"))["messages"])
            == 4
        )

        # Growth replaces: a larger copy wins.
        grown = dict(
            session,
            messages=session["messages"] + [dict(session["messages"][-1], content="newer turn")],
        )
        path3, note3 = stage(grown, staging)
        assert path3 is not None and "5 messages" in note3, note3

        # Unknown session: not found, no crash.
        result, note4 = find_and_stage("s-missing", [db], staging)
        assert result is None and "not found" in note4, note4

        print("stage_hermes_session self-test: all cases pass")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    if "--self-test" in argv:
        return run_self_test()

    explicit_db = None
    session_id = None
    strict = "--strict" in argv
    args = list(argv)
    for flag, setter in (("--db", "db"), ("--session-id", "sid")):
        if flag in args:
            pos = args.index(flag)
            value = args[pos + 1]
            del args[pos : pos + 2]
            if setter == "db":
                explicit_db = value
            else:
                session_id = value

    if session_id is None:
        # Hook mode: JSON payload on stdin; never fail the harness.
        try:
            payload = json.load(sys.stdin)
            session_id = payload.get("session_id")
        except Exception as exc:  # noqa: BLE001 — hook mode must not raise
            print(f"stage_hermes_session: bad hook payload ({exc})", file=sys.stderr)
            return 0
        if not session_id:
            return 0
        try:
            path, note = find_and_stage(session_id, resolve_databases(explicit_db))
            print(f"stage_hermes_session: {note}", file=sys.stderr)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"stage_hermes_session: {exc}", file=sys.stderr)
            return 0

    try:
        path, note = find_and_stage(session_id, resolve_databases(explicit_db))
    except Exception as exc:  # noqa: BLE001
        print(f"stage_hermes_session: {exc}", file=sys.stderr)
        return 1
    print(f"stage_hermes_session: {note}")
    if path is None and strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
