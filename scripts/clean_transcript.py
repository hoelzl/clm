#!/usr/bin/env python3
"""Turn a Claude Code or Hermes session export into a readable transcript.

Why this exists
---------------
A session file is ~95% machinery: tool calls, tool results, and harness
metadata. The conversation itself -- what the two participants actually said --
is a small fraction, and it is the only part that lets a later session resume
as a participant rather than as a stranger who read the minutes.

Summaries cannot do that job. The good remarks in a discussion come from
holding two incidental things together, and summarising keeps conclusions
while discarding exactly the texture that cross-connections are made from.
So: keep the dialogue verbatim, and throw away the machinery.

What it keeps
-------------
- every user and assistant text block, verbatim
- questions put to the user, and the answers given
- one-line stubs for tool calls, because the dialogue refers to them
  ("the build finished", "113/113") and deleting them outright leaves
  dangling references

What it drops
-------------
- tool inputs and results (file contents are in Git; logs are reproducible)
- harness metadata: file snapshots, permission modes, queue operations
- system reminders and task notifications, which are not conversation
- injected prompt text and compaction summaries, which the harness stores as
  user turns although the owner never said them

That last one matters more than it sounds. A compaction summary is a lossy
derivative of dialogue that this transcript already holds verbatim, so keeping
it duplicates ten thousand characters *and* attributes them to the owner. The
one case where dropping it would lose something is cleaning a session that was
resumed after compaction into a *new* session file: there the summary is the
only trace of what came before. Clean the original file instead.

It also redacts the user profile path and Hermes profile paths, since a
cleaned transcript is meant to be committable and the raw one is not.

The output carries the discussions' doc-currency frontmatter
(docs/claude/discussions/README.md) with
``status: archived`` -- a transcript is a point-in-time record, never a
current-truth document. The frontmatter block is excluded from the
non-narrowing dialogue comparison, so re-running to extend a transcript only
has to preserve dialogue, not metadata.

Usage
-----
    python scripts/clean_transcript.py <session.jsonl> [-o out.md] [--stats]

A directory instead of a file means "the newest session in it" -- pass
the Claude project directory of the checkout the session ran in
(``scripts/audit_transcripts.py`` prints every slug that belongs to this
repository, worktrees included).

Hermes exports the source file with:
    hermes sessions export <session.jsonl> --format jsonl \
        --session-id <id> --redact --yes

A staged full-lineage copy (scripts/stage_hermes_session.py) is the better
source when one exists: CLI exports of open or compacted sessions can be
incomplete. See .agents/skills/save-discussion/SKILL.md.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys
from collections import Counter

# Blocks the harness injects into user turns. They are not things the user said.
NOISE_TAGS = (
    "system-reminder",
    "task-notification",
    "local-command-stdout",
    "local-command-caveat",
    "command-message",
    "command-args",
)

# Record-level flags marking a turn the harness authored and stored as the
# user's. `isMeta` carries injected prompt text, `isCompactSummary` the
# summary written when context ran out.
SYNTHETIC_FLAGS = ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly")

# Tools whose result is genuinely part of the conversation rather than machinery.
PRESERVE_RESULT = {"AskUserQuestion"}

# Profile paths to rewrite as `~`.
#
# Both Windows and POSIX forms are always active, not selected by
# `sys.platform`: a session recorded on one machine is routinely cleaned on
# another, and the path in the text belongs to the machine that *recorded* it.
#
# The real home goes first so the longest match wins, and it is derived rather
# than typed -- a hardcoded account name is the same defect one level down.
HOME_PATTERNS = [
    re.compile(re.escape(str(pathlib.Path.home())), re.IGNORECASE),
    re.compile(r"[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9._-]+"),
    re.compile(r"/Users/[A-Za-z0-9._-]+"),
]

# Hermes profile topology must not survive into a committed transcript at all,
# not even in `~`-normalized form: the profile name and layout are private.
# Applied AFTER HOME_PATTERNS, so every home variant has already collapsed to
# `~` and one pattern covers POSIX (`~/.hermes/...`) and Windows
# (`~/AppData/Local/hermes/...`, `~/AppData/Roaming/hermes/...`).
HERMES_PROFILE_PATH = re.compile(
    r"~[\\/](?:\.hermes|AppData[\\/](?:Local|Roaming)[\\/]hermes)\S*",
    re.IGNORECASE,
)

# Email addresses identify a person and are never load-bearing in a transcript.
# Redact rather than drop so the surrounding sentence remains useful.
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

COMMAND_TAG = re.compile(r"<command-name>\s*(\S+)\s*</command-name>", re.DOTALL)
BARE_COMMAND = re.compile(r"^/[A-Za-z][\w-]*\s*$")

HERMES_OUT_OF_BAND = re.compile(
    r"(?:^|\n)\[OUT-OF-BAND USER MESSAGE — a direct message from the user, "
    r"(?:delivered once at this position; not tool output and not a new delivery "
    r"when replayed from conversation history|delivered mid-turn; not tool output)\]"
    r"\n(?P<text>.*?)\n\[/OUT-OF-BAND USER MESSAGE\]\s*\Z",
    re.DOTALL,
)


def redact(text: str) -> str:
    for pattern in HOME_PATTERNS:
        text = pattern.sub("~", text)
    text = HERMES_PROFILE_PATH.sub("[REDACTED PROFILE PATH]", text)
    return EMAIL.sub("<email>", text)


def strip_noise(text: str) -> str:
    for tag in NOISE_TAGS:
        text = re.sub(rf"<{tag}>.*?</{tag}>", "", text, flags=re.DOTALL)
    return text.strip()


def hermes_out_of_band(text: str) -> str | None:
    """Return a genuine trailing Hermes mid-turn owner message, if present."""
    match = HERMES_OUT_OF_BAND.search(text)
    if not match:
        return None
    cleaned = strip_noise(redact(match.group("text")))
    return cleaned or None


def dialogue_lines(text: str) -> list[str]:
    """Return normalized dialogue lines, excluding generated transcript framing."""
    lines: list[str] = []
    in_header = False
    in_frontmatter = False
    for number, raw_line in enumerate(text.splitlines()):
        line = raw_line.strip()
        # YAML frontmatter (doc-currency gate) is metadata, not dialogue. It is
        # only frontmatter when it opens the file.
        if number == 0 and line == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        if line.startswith("<!--"):
            in_header = True
        if in_header:
            if line.endswith("-->"):
                in_header = False
            continue
        if not line or line in {"## Owner", "## Agent"}:
            continue
        if line.startswith("> [") or line.startswith(">     ->"):
            continue
        lines.append(redact(line))
    return lines


def missing_dialogue(old: str, new: str) -> list[str]:
    """Return dialogue lines whose old multiplicity is absent from the new text."""
    old_counts = Counter(dialogue_lines(old))
    new_counts = Counter(dialogue_lines(new))
    return list((old_counts - new_counts).elements())


def command_stub(text: str) -> str | None:
    """A slash-command invocation, reduced to a stub.

    Running one is an act, not an utterance, but it is an act the dialogue
    refers to -- so it gets the same treatment as a tool call. The harness
    records the same invocation twice, once bare and once tagged; both reduce
    to the same line and the caller collapses the repeat.
    """
    m = COMMAND_TAG.search(text)
    if m:
        return f"[command] {m.group(1)}"
    if BARE_COMMAND.match(text):
        return f"[command] {text.strip()}"
    return None


def stub(name: str, inp: dict) -> str:
    """One line describing a tool call, enough to keep references coherent."""

    def g(*keys):
        return next((inp[k] for k in keys if isinstance(inp.get(k), str)), "")

    def short(s):
        return (s if len(s) <= 90 else s[:87] + "...").replace("\n", " ")

    if name in ("Bash", "PowerShell"):
        return f"[{name}] {short(g('description') or g('command'))}"
    if name in ("Read", "Write", "Edit", "NotebookEdit", "Artifact"):
        return f"[{name}] {short(g('file_path'))}"
    if name in ("Grep", "Glob"):
        return f"[{name}] {short(g('pattern'))}"
    if name in ("TaskCreate", "TaskUpdate"):
        return f"[{name}] {short(g('subject') or g('status') or g('taskId'))}"
    if name == "Skill":
        return f"[Skill] {short(g('skill'))}"
    if name == "ToolSearch":
        return f"[ToolSearch] {short(g('query'))}"
    if name == "SendUserFile":
        files = inp.get("files") or []
        return f"[SendUserFile] {short(', '.join(map(str, files)))}"
    if name == "AskUserQuestion":
        qs = [q.get("question", "") for q in (inp.get("questions") or [])]
        return "[AskUserQuestion]\n" + "\n".join(f"    - {q}" for q in qs)
    return f"[{name}]"


def convert(path: pathlib.Path) -> tuple[list[str], dict]:
    out: list[str] = []
    stats = {
        "user": 0,
        "assistant": 0,
        "stubs": 0,
        "dropped": 0,
        "synthetic": 0,
        "queued": 0,
    }
    pending: list[str] = []  # consecutive tool stubs, collapsed together
    preserve: set[str] = set()  # tool_use_ids whose result we keep
    last_role: str | None = None

    def flush_stubs() -> None:
        if pending:
            out.append("> " + "\n> ".join("\n".join(pending).split("\n")))
            pending.clear()

    def push_stub(line: str) -> None:
        if not pending or pending[-1] != line:
            pending.append(line)

    for line in path.open(encoding="utf-8"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Owner messages typed while Claude is working are queue operations,
        # not user-role turns. Keep only the enqueue side; later queue
        # operations repeat the same content as it is consumed.
        if rec.get("type") == "queue-operation":
            if rec.get("operation") == "enqueue":
                text = strip_noise(redact(rec.get("content") or ""))
                if text and not command_stub(text):
                    flush_stubs()
                    if last_role != "user":
                        out.append("\n## Owner\n")
                        last_role = "user"
                    out.append(text)
                    stats["user"] += 1
                    stats["queued"] += 1
                    continue
            stats["dropped"] += 1
            continue

        msg = rec.get("message")
        if not isinstance(msg, dict):
            stats["dropped"] += 1
            continue

        role = msg.get("role")
        if role not in ("user", "assistant"):
            stats["dropped"] += 1
            continue

        if any(rec.get(flag) for flag in SYNTHETIC_FLAGS):
            stats["synthetic"] += 1
            continue

        content = msg.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if not isinstance(blocks, list):
            continue

        for b in blocks:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")

            if btype == "text":
                text = strip_noise(redact(b.get("text", "")))
                if not text:
                    continue
                cmd = command_stub(text) if role == "user" else None
                if cmd:
                    push_stub(cmd)
                    stats["stubs"] += 1
                    continue
                flush_stubs()
                if role != last_role:
                    out.append(f"\n## {'Owner' if role == 'user' else 'Agent'}\n")
                    last_role = role
                out.append(text)
                stats[role] += 1

            elif btype == "tool_use":
                name = b.get("name", "?")
                if name in PRESERVE_RESULT:
                    preserve.add(b.get("id", ""))
                push_stub(redact(stub(name, b.get("input") or {})))
                stats["stubs"] += 1

            elif btype == "tool_result":
                if b.get("tool_use_id") in preserve:
                    c = b.get("content")
                    text = c if isinstance(c, str) else json.dumps(c)
                    pending.append(f"    -> {strip_noise(redact(text))}")

    flush_stubs()
    return out, stats


def hermes_tool_stub(call: dict) -> str:
    """Reduce one Hermes/OpenAI-style tool call to a useful one-line stub."""
    function = call.get("function") or {}
    name = function.get("name") or call.get("name") or "?"
    arguments = function.get("arguments") or call.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}

    detail = next(
        (
            arguments[key]
            for key in ("path", "file_path", "query", "command", "pattern", "name")
            if isinstance(arguments.get(key), str)
        ),
        "",
    )
    detail = redact(detail.replace("\n", " "))
    if len(detail) > 90:
        detail = detail[:87] + "..."
    return f"[{name}]" + (f" {detail}" if detail else "")


def convert_hermes(session: dict) -> tuple[list[str], dict]:
    """Convert one object emitted by ``hermes sessions export --format jsonl``."""
    out: list[str] = []
    stats = {
        "user": 0,
        "assistant": 0,
        "stubs": 0,
        "dropped": 0,
        "synthetic": 0,
        "out_of_band": 0,
    }
    last_role: str | None = None

    for message in session.get("messages") or []:
        if not isinstance(message, dict):
            stats["dropped"] += 1
            continue
        if message.get("_compressed_summary"):
            stats["synthetic"] += 1
            continue
        role = message.get("role")
        if role == "tool":
            content = message.get("content")
            # Form responses are mediated owner dialogue, not ordinary tool output.
            if message.get("tool_name") in ("clarify", "functions.clarify") and isinstance(
                content, str
            ):
                try:
                    payload, _ = json.JSONDecoder().raw_decode(content.lstrip())
                except ValueError:
                    payload = {}
                for response in payload.get("responses", []) if isinstance(payload, dict) else []:
                    if not isinstance(response, dict):
                        continue
                    question = response.get("question")
                    if isinstance(question, str) and question:
                        if last_role != "assistant":
                            out.append("\n## Agent\n")
                            last_role = "assistant"
                        out.append(strip_noise(redact(question)))
                        choices = response.get("choices_offered", [])
                        if isinstance(choices, list):
                            out.extend(
                                "- " + redact(choice)
                                for choice in choices
                                if isinstance(choice, str)
                            )
                        stats["assistant"] += 1
                    answer = response.get("user_response")
                    if isinstance(answer, str) and answer:
                        if last_role != "user":
                            out.append("\n## Owner\n")
                            last_role = "user"
                        out.append(strip_noise(redact(answer)))
                        stats["user"] += 1
            owner_text = hermes_out_of_band(content) if isinstance(content, str) else None
            if owner_text:
                if last_role != "user":
                    out.append("\n## Owner\n")
                    last_role = "user"
                out.append(owner_text)
                stats["user"] += 1
                stats["out_of_band"] += 1
            stats["dropped"] += 1
            continue
        if role not in ("user", "assistant"):
            stats["dropped"] += 1
            continue

        text = message.get("content")
        # Codex commentary can be public message items with empty content.
        # Never use reasoning_content/reasoning items as a dialogue fallback.
        if role == "assistant" and not text:
            items = message.get("codex_message_items") or []
            if isinstance(items, str):
                items = json.loads(items)
            if isinstance(items, list):
                text = "\n\n".join(
                    part["text"]
                    for item in items
                    if isinstance(item, dict)
                    and item.get("type") == "message"
                    and item.get("role") == "assistant"
                    and item.get("channel") in (None, "commentary", "final")
                    for part in item.get("content", [])
                    if isinstance(part, dict)
                    and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)
                )
        text = strip_noise(redact(text)) if isinstance(text, str) else ""
        if role == "user" and text.startswith("[CONTEXT COMPACTION —"):
            stats["synthetic"] += 1
            text = ""

        if text:
            if role != last_role:
                out.append(f"\n## {'Owner' if role == 'user' else 'Agent'}\n")
                last_role = role
            out.append(text)
            stats[role] += 1

        calls = message.get("tool_calls") or []
        if isinstance(calls, str):
            try:
                calls = json.loads(calls)
            except json.JSONDecodeError:
                calls = []
        if isinstance(calls, list) and calls:
            lines = [hermes_tool_stub(call) for call in calls if isinstance(call, dict)]
            if lines:
                out.append("> " + "\n> ".join(lines))
                stats["stubs"] += len(lines)

    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", type=pathlib.Path)
    ap.add_argument("-o", "--out", type=pathlib.Path)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument(
        "--allow-narrowing",
        action="store_true",
        help="permit an existing cleaned transcript to lose dialogue lines",
    )
    args = ap.parse_args()

    # A directory means "the session currently being written" -- newest .jsonl.
    # Saves the caller from reconstructing a session UUID it may not know.
    if args.session.is_dir():
        sessions = sorted(
            args.session.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not sessions:
            print(f"no .jsonl sessions in {args.session}", file=sys.stderr)
            return 1
        args.session = sessions[0]
        print(f"using newest session: {args.session.name}", file=sys.stderr)

    if not args.session.is_file():
        print(f"no such session file: {args.session}", file=sys.stderr)
        return 1

    hermes_session = None
    try:
        with args.session.open(encoding="utf-8") as handle:
            first_record = json.loads(handle.readline())
        if isinstance(first_record, dict) and isinstance(first_record.get("messages"), list):
            hermes_session = first_record
    except (OSError, json.JSONDecodeError):
        pass

    if hermes_session is not None:
        parts, stats = convert_hermes(hermes_session)
        source_label = f"Hermes session {hermes_session.get('id', args.session.stem)}"
        activity = hermes_session.get("last_activity_at") or hermes_session.get("started_at")
        through = datetime.datetime.fromtimestamp(activity).strftime("%Y-%m-%d %H:%M")
    else:
        parts, stats = convert(args.session)
        source_label = args.session.name
        through = datetime.datetime.fromtimestamp(args.session.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M"
        )
    body = "\n\n".join(parts).strip() + "\n"

    # A transcript is a snapshot: the session keeps growing after it is written,
    # so record what it contains rather than letting a reader assume "all of it".
    header = (
        f"<!-- Cleaned from {source_label} by scripts/clean_transcript.py.\n"
        f"     Dialogue verbatim; tool calls reduced to one-line stubs; harness\n"
        f"     metadata and tool output removed.\n"
        f"     Content through {through}. Re-run to extend. -->\n\n"
    )
    # Doc-currency frontmatter (docs/claude/discussions/README.md): a transcript
    # is a frozen record, which is exactly what `archived` means.
    frontmatter = f"---\nstatus: archived\nupdated: {datetime.date.today().isoformat()}\n---\n\n"
    text = frontmatter + header + body

    if args.out:
        if args.out.is_file() and not args.allow_narrowing:
            old_text = args.out.read_text(encoding="utf-8")
            missing = missing_dialogue(old_text, text)
            if missing:
                print(
                    "refusing to replace the transcript: the new clean loses "
                    f"{len(missing)} existing dialogue line(s):",
                    file=sys.stderr,
                )
                for line in missing[:5]:
                    print(f"  {line}", file=sys.stderr)
                if len(missing) > 5:
                    print(f"  ... and {len(missing) - 5} more", file=sys.stderr)
                print(
                    "Use the fuller live source, merge the missing tail, or pass "
                    "--allow-narrowing only after reviewing the loss.",
                    file=sys.stderr,
                )
                return 1
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
        # Characters only, deliberately. Estimating tokens as chars/4 undercounts
        # this kind of text by about a third, and a wrong number gets quoted as
        # fact across every document that cites it.
        print(f"wrote {args.out}  ({len(text) / 1000:.1f} kchar, content through {through})")
    else:
        sys.stdout.write(text)

    if args.stats:
        raw = args.session.stat().st_size
        queued = (
            f", {stats['queued']} of the owner blocks typed mid-turn" if "queued" in stats else ""
        )
        out_of_band = (
            f", {stats['out_of_band']} of the owner blocks delivered out-of-band"
            if "out_of_band" in stats
            else ""
        )
        print(
            f"  source {raw / 1e6:.2f} MB -> {len(text) / 1e6:.2f} MB "
            f"({raw / max(len(text), 1):.1f}:1)\n"
            f"  {stats['user']} owner blocks, {stats['assistant']} agent blocks, "
            f"{stats['stubs']} tool stubs{queued}{out_of_band}, "
            f"{stats['dropped']} records dropped, "
            f"{stats['synthetic']} harness-authored turns dropped",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
