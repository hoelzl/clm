"""``--spec`` addressing for the harvest verbs and the course-level batch report (#1104).

``clm harvest report --spec SPEC --lang L`` runs the report over every recorded
deck of a course; ``task``/``accept``/``verify`` address one deck with
``--spec SPEC --topic T [--deck STEM]``. Deck and video resolution live in
:mod:`clm.voiceover.harvest_batch`; this module is the CLI glue: the shared
options, the DECK-or-``--spec`` rule, and the batch envelope.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from clm.voiceover.harvest_batch import RecordedDeck


def spec_address_options(*, batch: bool = False, recordings: bool = False):
    """``--spec``/``--topic``/``--deck`` (+ ``--section``, ``--recordings-root``)."""

    def decorate(f):
        options = [
            click.option(
                "--spec",
                "spec_file",
                type=click.Path(exists=True, dir_okay=False, path_type=Path),
                default=None,
                help="Address decks through this course spec instead of a DECK path "
                + (
                    "(report: every recorded deck of the course; see --topic/--section)."
                    if batch
                    else "(needs --topic)."
                ),
            ),
            click.option(
                "--topic",
                default=None,
                help="With --spec: an exact topic id (never a suffix or pattern).",
            ),
            click.option(
                "--deck",
                "deck_filter",
                default=None,
                help="With --spec: the deck file stem (e.g. slides_intro) when the "
                "topic holds several decks.",
            ),
        ]
        if batch:
            options.append(
                click.option(
                    "--section",
                    default=None,
                    help="With --spec: only this section (its id, or its 1-based "
                    "position for sections without an id).",
                )
            )
        if recordings:
            options.append(
                click.option(
                    "--recordings-root",
                    type=click.Path(file_okay=False, path_type=Path),
                    default=None,
                    help="With --spec: the recordings root whose final/ tree holds the "
                    "videos (default: recordings.root_dir from the CLM config).",
                )
            )
        for option in reversed(options):
            f = option(f)
        return f

    return decorate


def check_addressing(slides: Path | None, spec_file: Path | None, **spec_only: Any) -> None:
    """Exactly one of DECK or ``--spec``; the spec-only options need ``--spec``."""
    if slides is not None and spec_file is not None:
        raise click.UsageError(
            "give either DECK (with VIDEO arguments) or --spec, not both; with "
            "--spec the deck and its videos come from the spec and the recordings tree"
        )
    if slides is None and spec_file is None:
        raise click.UsageError("missing DECK (or address the deck with --spec … --topic …)")
    if spec_file is None:
        used = [f"--{name.replace('_', '-')}" for name, value in spec_only.items() if value]
        if used:
            raise click.UsageError(f"{', '.join(used)} only apply with --spec")


def recordings_root_or_exit(cli_root: Path | None) -> Path:
    """``--recordings-root``, else ``recordings.root_dir`` from the config."""
    if cli_root is not None:
        return cli_root
    configured = ""
    try:
        from clm.infrastructure.config import get_config

        configured = get_config().recordings.root_dir or ""
    except Exception:  # pragma: no cover - a broken config reads as unset
        configured = ""
    if not configured:
        raise click.UsageError(
            "no recordings root: pass --recordings-root or set recordings.root_dir "
            "in the CLM config"
        )
    return Path(configured)


def resolve_one_or_exit(
    spec_file: Path,
    lang: str,
    topic: str | None,
    deck_filter: str | None,
    recordings_root: Path | None = None,
) -> RecordedDeck:
    from clm.voiceover.harvest_batch import SpecAddressError, resolve_one_deck

    try:
        return resolve_one_deck(
            spec_file, lang, topic=topic, deck=deck_filter, recordings_root=recordings_root
        )
    except SpecAddressError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)


def videos_or_exit(found: RecordedDeck) -> list[Path]:
    """The deck's videos from the layout; exit 2 when there are none (or they clash)."""
    if found.error is not None:
        click.echo(f"error: {found.deck.name}: {found.error}", err=True)
        sys.exit(2)
    if not found.videos:
        click.echo(
            f"error: no recording of {found.deck_name!r} in {found.video_dir} "
            "(pass DECK and VIDEO arguments for videos outside the recordings tree)",
            err=True,
        )
        sys.exit(2)
    return found.videos


def task_command(
    spec_file: Path, found: RecordedDeck, lang: str, *, needs_deck: bool, root: Path | None
) -> str:
    """The follow-up ``harvest task`` command for one deck of the batch (D2)."""
    args = ["clm", "harvest", "task", "--spec", str(spec_file), "--topic", found.topic_id]
    if needs_deck:
        args += ["--deck", found.deck.name.split(".")[0]]
    if root is not None:
        args += ["--recordings-root", str(root)]
    args += ["--lang", lang, "--slide", "ID"]
    return shlex.join(args)


def run_batch_report(
    ctx: click.Context,
    *,
    spec_file: Path,
    lang: str,
    topic: str | None,
    section: str | None,
    deck_filter: str | None,
    cli_root: Path | None,
    resolve_only: bool,
    unharvested: bool,
    as_json: bool,
    build_report,
) -> None:
    """``harvest report --spec``: one envelope over the course's recorded decks (D5).

    *build_report* is ``(bundle, deck, videos) -> report dict``; it may raise —
    a deck that fails is recorded with ``status: error`` and the batch goes on.
    """
    from clm.slides.agent_task import envelope
    from clm.voiceover.harvest import report_exit_code, video_fingerprint
    from clm.voiceover.harvest_batch import (
        SpecAddressError,
        deck_already_harvested,
        resolve_recorded_decks,
        unclaimed_videos,
    )

    root = recordings_root_or_exit(cli_root)
    try:
        decks = resolve_recorded_decks(
            spec_file, lang, root, topic=topic, section=section, deck=deck_filter
        )
    except SpecAddressError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    per_topic: dict[str, int] = {}
    for found in decks:
        per_topic[found.topic_id] = per_topic.get(found.topic_id, 0) + 1

    from clm.core.course_paths import resolve_course_paths

    course_root, _ = resolve_course_paths(spec_file)
    entries: list[dict[str, Any]] = []
    for found in decks:
        entry = found.to_json(course_root)
        if found.error is not None:
            entry["status"] = "error"
        elif not found.videos:
            entry["status"] = "no_recording"
        elif resolve_only:
            entry["status"] = "recorded"
        elif unharvested and deck_already_harvested(found.deck, video_fingerprint(found.videos)):
            entry["status"] = "harvested"
        else:
            try:
                report = build_report(found)
            except click.exceptions.Exit:
                raise
            except (SystemExit, KeyboardInterrupt):
                raise
            except Exception as exc:
                entry["status"] = "error"
                entry["error"] = str(exc) or type(exc).__name__
            else:
                entry["status"] = "actionable" if report_exit_code(report) else "nothing"
                if entry["status"] == "actionable":
                    entry["next"] = task_command(
                        spec_file,
                        found,
                        lang,
                        needs_deck=per_topic[found.topic_id] > 1,
                        root=cli_root,
                    )
                entry["report"] = report
        entries.append(entry)

    statuses = ("actionable", "nothing", "harvested", "recorded", "no_recording", "error")
    summary: dict[str, Any] = {
        "decks": len(entries),
        "videos": sum(len(e["videos"]) for e in entries),
    }
    for status in statuses:
        summary[status] = sum(1 for e in entries if e["status"] == status)
    if topic is None and section is None and deck_filter is None:
        summary["unclaimed_videos"] = [
            str(p) for p in unclaimed_videos(spec_file, lang, root, decks)
        ]

    body = {
        "spec": str(spec_file),
        "video_language": lang,
        "recordings_root": str(root),
        "resolve_only": resolve_only,
        "summary": summary,
        "decks": entries,
    }
    if as_json:
        click.echo(
            json.dumps(
                envelope(1, tool="harvest", verb="batch-report", body=body),
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        _print_batch(body)

    if summary["error"]:
        sys.exit(2)
    sys.exit(1 if summary["actionable"] else 0)


def _print_batch(body: dict[str, Any]) -> None:
    summary = body["summary"]
    for entry in body["decks"]:
        parts = entry["parts"]
        videos = (
            f"{len(entry['videos'])} part(s)"
            if parts and parts != [0]
            else ("1 video" if entry["videos"] else "no video")
        )
        line = f"[{entry['status']:>12}] {entry['deck']}  ({videos})"
        click.echo(line)
        if entry.get("error"):
            click.echo(f"               error: {entry['error']}")
        for note in entry["notes"]:
            click.echo(f"               note: {note}")
        if entry.get("next"):
            click.echo(f"               next: {entry['next']}")
    counts = ", ".join(
        f"{summary[s]} {s}"
        for s in ("actionable", "nothing", "harvested", "recorded", "no_recording", "error")
        if summary[s]
    )
    click.echo(f"\n{summary['decks']} deck(s), {summary['videos']} video(s): {counts or 'none'}")
    for path in summary.get("unclaimed_videos", []):
        click.echo(f"unclaimed video (no deck of the spec matches it): {path}")
