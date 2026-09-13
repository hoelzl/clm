#!/usr/bin/env python3
"""Report Claude and Hermes sessions that no discussion register accounts for.

Why this exists
---------------
A session's conversation layer exists only if somebody runs the save before
that session ends. When one is missed, the only signal is a gap in the
register's session *numbering* — and noticing a gap is a hunch, not a check.
This is the check.

Claude sessions partition by project slug. The slug encodes the *working
directory*, so it separates machines and checkouts: a register path from
another slug is *expected* to be absent from this disk, and comparing bare
basenames reports healthy sessions on another machine as lost. In this
repository most sessions run from a **git worktree** under
`.claude/worktrees/<name>`, which has its own slug
(`<repo slug>--claude-worktrees-<name>`), so the audit covers the main
checkout's slug *and* every worktree slug derived from it, whichever
checkout it is run from. Hermes sessions are selected from the profile
`state.db` by their recorded repository root or working directory (a
worktree path is under the main checkout, so it is included too);
`--hermes-db` selects another profile. Anything this prints is scoped to
session sources this machine actually holds. When a conversation ran in
another repository but influenced this one, repeat `--hermes-workspace` to
select its recorded origin while comparing against the discussion register
under `--repo`.

Advisory, never a gate
----------------------
An unsaved session is not a broken tree — it is a judgement call about whether
a conversation was worth keeping, and plenty are not. So this prints and
exits 0, and is deliberately not wired into CI or a pre-commit hook.

Standard library only.
"""

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISCUSSIONS = os.path.join(REPO, "docs", "claude", "discussions")
PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
STAGING = os.path.join(os.path.expanduser("~"), ".clm-transcripts")
WORKTREES_SEGMENT = os.path.join(".claude", "worktrees")

CLAUDE_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
HERMES_SESSION_ID = re.compile(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{6}")
SESSION_ID = re.compile(rf"(?:{CLAUDE_SESSION_ID.pattern}|{HERMES_SESSION_ID.pattern})")
HERMES_OUT_OF_BAND = re.compile(
    r"(?:^|\n)\[OUT-OF-BAND USER MESSAGE — a direct message from the user, "
    r"(?:delivered once at this position; not tool output and not a new delivery "
    r"when replayed from conversation history|delivered mid-turn; not tool output)\]"
    r"\n(?P<text>.*?)\n\[/OUT-OF-BAND USER MESSAGE\]\s*\Z",
    re.DOTALL,
)
HERMES_SYNTHETIC_PREFIXES = (
    "[CONTEXT COMPACTION —",
    "[ASYNC DELEGATION",
)


def slug_for(path):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def main_checkout(path):
    """The main checkout for ``path``: itself, or the repo a worktree belongs to."""
    path = os.path.abspath(path)
    marker = os.sep + WORKTREES_SEGMENT + os.sep
    at = os.path.normcase(path).find(os.path.normcase(marker))
    return path[:at] if at > 0 else path


def claude_session_dirs(repo, projects):
    """Claude project directories of the main checkout and its worktrees.

    Matched case-insensitively: the slug of one checkout has been recorded
    with both a lower- and an upper-case drive letter on this machine.
    """
    root_slug = slug_for(main_checkout(repo)).lower()
    worktree_prefix = (
        root_slug + "-" + re.sub(r"[^A-Za-z0-9]", "-", WORKTREES_SEGMENT).lower() + "-"
    )
    if not os.path.isdir(projects):
        return []
    return [
        os.path.join(projects, name)
        for name in sorted(os.listdir(projects))
        if name.lower() == root_slug or name.lower().startswith(worktree_prefix)
    ]


def ids_in(filenames_match, discussions=DISCUSSIONS):
    """Session ids mentioned by the discussion files this predicate selects."""
    found = set()
    if not os.path.isdir(discussions):
        return found
    for root, _dirs, files in os.walk(discussions):
        for name in files:
            if not name.endswith(".md") or not filenames_match(root, name):
                continue
            try:
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as handle:
                    found.update(SESSION_ID.findall(handle.read()))
            except OSError:
                continue
    return found


def summarise(path):
    """Date, owner-turn count and opening line — enough to triage without loading."""
    owner_turns = 0
    first = ""
    last_ts = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                last_ts = record.get("timestamp") or last_ts
                if record.get("type") != "user":
                    continue
                content = record.get("message", {}).get("content")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                text = text.strip()
                # Harness-authored turns (command output, reminders, injected
                # context) open with a tag and are not the owner speaking.
                if text and not text.startswith("<"):
                    owner_turns += 1
                    if not first:
                        first = " ".join(text.split())[:90]
    except OSError:
        pass
    return owner_turns, first, last_ts[:10]


def audit_claude(repo, projects, registered, transcribed):
    directories = claude_session_dirs(repo, projects)
    if not directories:
        print(
            f"Claude: no session directory for {main_checkout(repo)} (or its worktrees) under {projects}"
        )
        return 0

    sessions = sorted(
        (
            (name, os.path.join(directory, name))
            for directory in directories
            for name in os.listdir(directory)
            if name.endswith(".jsonl")
        ),
        key=lambda item: os.path.getmtime(item[1]),
    )

    local_ids = {os.path.splitext(name)[0] for name, _path in sessions}
    unsaved = []
    unindexed = []
    for name, path in sessions:
        session_id = os.path.splitext(name)[0]
        if session_id in registered:
            continue
        entry = (session_id, path)
        (unindexed if session_id in transcribed else unsaved).append(entry)

    registered_claude = {item for item in registered if CLAUDE_SESSION_ID.fullmatch(item)}
    elsewhere = sorted(registered_claude - local_ids)

    print("Claude slugs for this repository (main checkout + worktrees):")
    for directory in directories:
        print(f"  {os.path.basename(directory)}")
    print(
        f"{len(sessions)} session(s) here, "
        f"{len(sessions) - len(unsaved) - len(unindexed)} in a register, "
        f"{len(elsewhere)} register id(s) not in this machine's session "
        "directories (expected, not missing)"
    )

    for session_id, _path in unindexed:
        print(
            f"\ncleaned but not indexed: {session_id} — a transcript exists, no register row does"
        )

    if not unsaved:
        if not unindexed:
            print("\nEvery session on this machine is in a register.")
        return 0

    print(f"\n{len(unsaved)} session(s) on this machine that no register mentions:\n")
    for session_id, path in unsaved:
        owner_turns, first, day = summarise(path)
        size = os.path.getsize(path) / 1e6
        staged = os.path.join(STAGING, f"{day}-{session_id}.md")
        mark = f"  [staged copy: {staged}]" if os.path.isfile(staged) else ""
        print(f"  {day}  {session_id}  {size:5.2f} MB  {owner_turns:2d} owner turn(s){mark}")
        print(f"      slug: {os.path.basename(os.path.dirname(path))}")
        if first:
            print(f"      opens: {first}")

    return len(unsaved)


def path_is_under(path, repo):
    if not path:
        return False
    try:
        path = os.path.normcase(os.path.abspath(path))
        repo = os.path.normcase(os.path.abspath(repo))
        return os.path.commonpath((path, repo)) == repo
    except (OSError, ValueError):
        return False


def hermes_owner_summary(connection, session_id):
    owner_turns = 0
    first = ""
    rows = connection.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY rowid",
        (session_id,),
    )
    for role, content in rows:
        if not isinstance(content, str):
            continue
        text = ""
        if role == "user" and not content.startswith(HERMES_SYNTHETIC_PREFIXES):
            text = content.strip()
        elif role == "tool":
            match = HERMES_OUT_OF_BAND.search(content)
            text = match.group("text").strip() if match else ""
        if not text:
            continue
        owner_turns += 1
        if not first:
            first = " ".join(text.split())[:90]
    return owner_turns, first


def audit_hermes(repo, database, registered, transcribed, workspaces=None):
    if not database:
        print("\nHermes: not checked (no --hermes-db and HERMES_HOME is unset).")
        return 0
    if not os.path.isfile(database):
        print(f"\nHermes: not checked; no session database at {database}")
        return 0

    selected_workspaces = workspaces or [repo]
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT id, source, cwd, git_repo_root, "
            "COALESCE(last_activity_at, started_at) FROM sessions ORDER BY started_at"
        )
        sessions = [
            (session_id, activity)
            for session_id, source, cwd, git_repo_root, activity in rows
            if source != "subagent"
            and any(
                path_is_under(git_repo_root, workspace) or path_is_under(cwd, workspace)
                for workspace in selected_workspaces
            )
        ]

        local_ids = {session_id for session_id, _activity in sessions}
        registered_hermes = {item for item in registered if HERMES_SESSION_ID.fullmatch(item)}
        unsaved = []
        unindexed = []
        for session_id, activity in sessions:
            if session_id in registered_hermes:
                continue
            entry = (session_id, activity)
            (unindexed if session_id in transcribed else unsaved).append(entry)

        elsewhere = registered_hermes - local_ids
        print(
            f"\nHermes: {len(sessions)} session(s) for the selected workspace(s), "
            f"{len(sessions) - len(unsaved) - len(unindexed)} in a register, "
            f"{len(elsewhere)} register id(s) not in this profile's database"
        )
        for session_id, _activity in unindexed:
            print(
                f"\nHermes cleaned but not indexed: {session_id} — a transcript exists, "
                "no register row does"
            )

        if not unsaved:
            if not unindexed:
                print("Every Hermes session for this repo is in a register.")
            return 0

        print(f"\n{len(unsaved)} Hermes session(s) that no register mentions:\n")
        for session_id, activity in unsaved:
            owner_turns, first = hermes_owner_summary(connection, session_id)
            day = datetime.datetime.fromtimestamp(activity or 0).date().isoformat()
            print(f"  {day}  {session_id}  {owner_turns:2d} owner turn(s)")
            if first:
                print(f"      opens: {first}")
        return len(unsaved)
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--claude-projects", default=PROJECTS)
    parser.add_argument("--hermes-db")
    parser.add_argument(
        "--hermes-workspace",
        action="append",
        help=(
            "include Hermes sessions recorded under this workspace while comparing "
            "against --repo's discussion registers; repeatable"
        ),
    )
    args = parser.parse_args(argv)

    discussions = os.path.join(os.path.abspath(args.repo), "docs", "claude", "discussions")
    registered = ids_in(lambda root, name: name == "register.md", discussions)
    transcribed = ids_in(lambda root, name: os.path.basename(root) == "transcripts", discussions)
    hermes_db = args.hermes_db
    if not hermes_db and os.environ.get("HERMES_HOME"):
        hermes_db = os.path.join(os.environ["HERMES_HOME"], "state.db")

    claude_unsaved = audit_claude(
        os.path.abspath(args.repo), args.claude_projects, registered, transcribed
    )
    hermes_unsaved = audit_hermes(
        main_checkout(args.repo),
        hermes_db,
        registered,
        transcribed,
        args.hermes_workspace,
    )
    if claude_unsaved or hermes_unsaved:
        print(
            "\nOwner turns are the triage signal: a two-turn build session has little\n"
            "conversation to resume, while a ten-turn one is where forks were argued.\n"
            "Save what is worth resuming with the save-discussion skill; record the\n"
            "rest as deliberately skipped so the gap is not rediscovered."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
