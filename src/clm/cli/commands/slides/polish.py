"""``clm slides polish`` — the speaker-notes agent toolkit (#962).

The command began as a stand-alone in-process chat completion (one LLM
call per slide, ``[summarize]`` extra, no ``--json``); issue #962 re-cut
it into the agent-toolkit contract (``clm info agent-tasks``). The verbs
frame and land the cleanup of a deck's speaker notes — the same
``update_narrative`` write the voiceover pipeline uses.

Verbs:

- ``report`` (the bare default) — read-only. Counts the slides whose notes
  a ``task`` would frame and points at the ``task`` / ``accept`` loop.
- ``task`` — frame the notes cleanup as ONE JSON task document
  (instructions + the level prompt + per-slide rows with both language
  sides as context + ``answer_schema`` + freshness tokens). No model, no
  API key.
- ``accept`` — validate an answer document (shape + freshness + coverage)
  and write the polished notes through the ordinary narrative writer,
  atomically.
- ``autopilot`` — the legacy in-process chat completion for the agent-less
  human; key-gated (except the no-LLM ``verbatim`` level), never in CI.

The sync ledger is deliberately untouched: polishing one side of a
recorded pair leaves the ledger baseline alone, so the next
``clm slides sync report`` frames the twin's update (§6 one-sided trust).

Exit codes (``0`` clean / ``1`` work pending / ``2`` error): ``report``
exits ``1`` while polishable notes exist; ``task`` exits ``2`` when there
are no notes to frame; ``accept`` exits ``0`` written, ``2`` rejected with
nothing written.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import NoReturn

import click
from rich.console import Console

from clm.cli._default_verb_group import DefaultVerbGroup
from clm.core.utils.prog_lang_utils import comment_token_for_path
from clm.notebooks.slide_writer import update_narrative
from clm.slides.agent_task import (
    EXIT_CLEAN,
    EXIT_ERROR,
    EXIT_WORK_PENDING,
    VALIDATORS,
    AnswerRejected,
    rejection_payload,
)
from clm.slides.polish_task import (
    ANSWER_VALIDATOR,
    TASK_SCHEMA,
    PolishAnswer,
    TaskUnavailable,
    build_task,
    framed_groups,
    parse_range,
    prepare_accept,
)

# Backwards-compatible alias: external code imported the CLI's range parser.
_parse_range = parse_range

logger = logging.getLogger(__name__)
console = Console()


@click.group("polish", cls=DefaultVerbGroup)
def polish_group() -> None:
    """Polish speaker notes in a slide deck (agent toolkit).

    \b
    Bare `clm slides polish DECK --lang de` == `clm slides polish report …`
    (read-only). Verbs:
      report     count the notes a task would frame; point at the loop
      task       frame the notes cleanup as a JSON task (no model, no key)
      accept     validate an answer document and write the polished notes
      autopilot  the legacy in-process LLM cleanup (needs an API key)

    \b
    The framing and the write follow the agent-task contract
    (`clm info sync-agents` → "Polishing speaker notes"); the engine never
    calls a model outside `autopilot`.
    """


#: Shared SLIDES argument for every verb.
_SLIDES_ARG = click.argument(
    "slides",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
_LANG_OPTION = click.option(
    "--lang",
    required=True,
    type=click.Choice(["de", "en"]),
    help="Language of the notes to polish.",
)
_ACCEPT_LANG_OPTION = click.option(
    "--lang",
    "lang",
    default=None,
    type=click.Choice(["de", "en"]),
    help="Language of the framed notes. Default: the answer's echo.",
)
_LEVEL_OPTION = click.option(
    "--polish-level",
    "polish_level",
    default="standard",
    show_default=True,
    type=click.Choice(["verbatim", "light", "standard", "heavy", "rewrite"]),
    help="How aggressively to edit the notes. `verbatim` returns them unchanged.",
)
_ACCEPT_LEVEL_OPTION = click.option(
    "--polish-level",
    "polish_level",
    default=None,
    type=click.Choice(["verbatim", "light", "standard", "heavy", "rewrite"]),
    help="The level the task framed. Default: the answer's echo.",
)
_RANGE_OPTION = click.option(
    "--slides-range",
    default=None,
    help="Slide range to polish (e.g. '5-10').",
)


def _range_or_usage_error(slides_range: str | None) -> tuple[int, int] | None:
    try:
        return parse_range(slides_range)
    except ValueError as exc:
        raise click.UsageError(f"invalid --slides-range: {exc}") from exc


def _slide_count(slides: Path, lang: str) -> tuple[int, list]:
    """(total slide groups, groups whose notes a task would frame)."""
    from clm.core.slide_text.slide_parser import parse_slides

    groups = [g for g in parse_slides(slides, lang) if g.slide_type != "header"]
    return len(groups), framed_groups(slides, lang)


# ---------------------------------------------------------------------------
# report — the bare default verb (read-only)
# ---------------------------------------------------------------------------


@polish_group.command("report")
@_SLIDES_ARG
@_LANG_OPTION
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def polish_report_cmd(slides: Path, lang: str, as_json: bool) -> None:
    """Report how many slides carry notes to polish (writes nothing).

    Read-only, no model, no API key. Exits ``1`` while the deck has
    polishable notes (work pending), ``0`` when there are none.
    """
    total, polishable = _slide_count(slides, lang)
    pending = bool(polishable)
    exit_code = EXIT_WORK_PENDING if pending else EXIT_CLEAN
    if as_json:
        click.echo(
            json.dumps(
                {
                    "schema": TASK_SCHEMA,
                    "tool": "polish",
                    "verb": "report",
                    "source": str(slides),
                    "lang": lang,
                    "slides_total": total,
                    "slides_with_notes": len(polishable),
                    "slides_without_notes": total - len(polishable),
                    "verbs": {
                        "task": f"clm slides polish task {slides.name} --lang {lang}",
                        "accept": (
                            f"clm slides polish accept {slides.name} --lang {lang} "
                            "--answer answer.json"
                        ),
                        "autopilot": (f"clm slides polish autopilot {slides.name} --lang {lang}"),
                    },
                    "exit_code": exit_code,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        sys.exit(exit_code)
    if not pending:
        click.echo(f"No notes found to polish in {slides.name} (lang={lang}).")
        sys.exit(exit_code)
    click.echo(f"{len(polishable)} of {total} slide(s) in {slides.name} carry notes to polish.")
    click.echo(
        f"Frame the work with `clm slides polish task {slides.name} --lang {lang}`, "
        "answer it, then land it with `clm slides polish accept "
        f"{slides.name} --lang {lang} --answer answer.json` (no API key needed), "
        "or run `clm slides polish autopilot` with one."
    )
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# task — the framed notes cleanup (read-only, model-free)
# ---------------------------------------------------------------------------


@polish_group.command("task")
@_SLIDES_ARG
@_LANG_OPTION
@_LEVEL_OPTION
@_RANGE_OPTION
def polish_task_cmd(slides: Path, lang: str, polish_level: str, slides_range: str | None) -> None:
    """Frame the notes cleanup as a JSON task document (read-only).

    Emits ONE task document: caller instructions with the level prompt,
    one row per notes-carrying slide (its handle, the current notes, and
    the slide's content in both language sides for context), the
    ``answer_schema``, and the freshness tokens (``source_fingerprint``,
    ``twin_fingerprint``) that ``accept`` re-checks. Uses no model and no
    API key.

    \b
    Exit codes: 0 task emitted · 2 no notes to polish under --lang /
    --slides-range.
    """
    try:
        payload = build_task(
            slides,
            lang=lang,
            polish_level=polish_level,
            slides_range=_range_or_usage_error(slides_range),
        )
    except TaskUnavailable as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(EXIT_ERROR)
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    sys.exit(EXIT_CLEAN)


# ---------------------------------------------------------------------------
# accept — the validated write
# ---------------------------------------------------------------------------


@polish_group.command("accept")
@_SLIDES_ARG
@_ACCEPT_LANG_OPTION
@_ACCEPT_LEVEL_OPTION
@_RANGE_OPTION
@click.option(
    "--answer",
    "answer_src",
    required=True,
    help="The answer document framed by `task`: a file path, or '-' for stdin.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the polished deck here instead of modifying SLIDES in place.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate the answer fully (shape + freshness + coverage) and write nothing.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the JSON outcome envelope.")
def polish_accept_cmd(
    slides: Path,
    lang: str | None,
    polish_level: str | None,
    slides_range: str | None,
    answer_src: str,
    output: Path | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Validate an answer document and write the polished notes.

    Validates the answer against the task's schema and the LIVE files
    (fingerprint freshness — a concurrent edit rejects, never overwrites),
    requires coverage of exactly the framed slides, then writes through the
    ordinary narrative writer, atomically (tag ``notes``). Uses no model
    and no API key; nothing is written on any validation failure. The sync
    ledger is deliberately untouched — polishing one side of a recorded
    pair lets the next ``clm slides sync report`` frame the twin's update.
    ``--lang`` / ``--polish-level`` default to the answer's echo; an
    explicit flag that contradicts it is a rejection.

    \b
    Exit codes: 0 written · 2 rejected / error (nothing written).
    """
    if answer_src == "-":
        raw = click.get_text_stream("stdin").read()
    else:
        answer_path = Path(answer_src)
        if not answer_path.exists():
            raise click.UsageError(f"answer file not found: {answer_src}")
        raw = answer_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise click.UsageError(f"the answer is not valid JSON: {exc}") from exc

    def _reject(reason: str) -> NoReturn:
        if as_json:
            click.echo(
                json.dumps(
                    rejection_payload(TASK_SCHEMA, tool="polish", verb="accept", reason=reason),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            click.echo(f"rejected: {reason}", err=True)
        sys.exit(EXIT_ERROR)

    try:
        answer: PolishAnswer = VALIDATORS.get(ANSWER_VALIDATOR)(payload)
        lang = lang if lang is not None else answer.lang
        if polish_level is not None and answer.polish_level != polish_level:
            raise AnswerRejected(
                f"polish_level mismatch: the answer polishes at "
                f"{answer.polish_level!r}, but accept was given {polish_level!r} "
                "(pass the level the task framed, or omit the flag)"
            )
        plan = prepare_accept(
            slides,
            answer,
            lang=lang,
            slides_range=_range_or_usage_error(slides_range),
            output=output,
        )
    except (AnswerRejected, KeyError, ValueError, TypeError) as exc:
        # AnswerRejected: a named violation. KeyError: the validator label is
        # unknown (registry drift). ValueError/TypeError: a non-object payload
        # slipping past json.loads (e.g. a bare string/number answer).
        _reject(str(exc))

    if dry_run:
        outcome = {
            "schema": TASK_SCHEMA,
            "tool": "polish",
            "verb": "accept",
            "dry_run": True,
            "applied": False,
            "source": str(slides),
            "lang": lang,
            "polish_level": answer.polish_level,
            "slides_polished": len(plan.index_map),
        }
        if as_json:
            click.echo(json.dumps(outcome, indent=2, ensure_ascii=False))
        else:
            click.echo(f"dry-run: would polish {len(plan.index_map)} slide(s) in {slides.name}.")
        sys.exit(EXIT_CLEAN)

    from clm.infrastructure.utils.path_utils import atomic_write_all

    text = slides.read_text(encoding="utf-8")
    updated = update_narrative(
        text,
        plan.index_map,
        lang,
        tag="notes",
        comment_token=comment_token_for_path(slides),
    )
    dest = plan.output or slides
    from clm.core.slide_text.pairing import derive_split_twin

    twin = derive_split_twin(slides)
    if twin is not None and dest.resolve() == twin.resolve():
        # Polishing one half must never overwrite the other half — the twin
        # was framed as read-only context and just passed a freshness check,
        # so clobbering it here would be silent data loss with exit 0.
        _reject(
            f"--output {dest} is this deck's split twin — polish writes the "
            f"{lang} half; the twin only provides context. Choose a different "
            "output path."
        )
    atomic_write_all([(dest, updated)])

    outcome = {
        "schema": TASK_SCHEMA,
        "tool": "polish",
        "verb": "accept",
        "applied": True,
        "action": "polished",
        "source": str(slides),
        "output": str(dest),
        "lang": lang,
        "polish_level": answer.polish_level,
        "slides_polished": len(plan.index_map),
        "written": [str(dest)],
        "exit_code": EXIT_CLEAN,
    }
    if as_json:
        click.echo(json.dumps(outcome, indent=2, ensure_ascii=False))
    else:
        click.echo(
            f"Polished {len(plan.index_map)} slide(s) in {dest}"
            + (
                " (review the copy with `git diff` before committing."
                if plan.output is not None
                else " — review with `git diff` before committing."
            )
        )
    sys.exit(EXIT_CLEAN)


# ---------------------------------------------------------------------------
# autopilot — the legacy in-process LLM path (key-gated)
# ---------------------------------------------------------------------------


@polish_group.command("autopilot")
@_SLIDES_ARG
@_LANG_OPTION
@_RANGE_OPTION
@click.option("--dry-run", is_flag=True, help="Show polished text without writing.")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Output file.")
@click.option("--model", default=None, help="LLM model identifier.")
@_LEVEL_OPTION
def polish_autopilot_cmd(
    slides: Path,
    lang: str,
    slides_range: str | None,
    dry_run: bool,
    output: Path | None,
    model: str | None,
    polish_level: str,
) -> None:
    """Polish the notes in-process through an LLM (needs a key).

    The agent-less path: for a human with an ``$OPENAI_API_KEY``. The write
    engine and post-conditions are those of ``accept``; only the judgment
    source differs (the embedded model instead of an agent's answer
    document). The ``verbatim`` level makes no LLM call and needs no key.
    Agents and CI should use ``task`` / ``accept`` — no key, same result.
    """
    from clm.core.slide_text.slide_parser import parse_slides
    from clm.notebooks.polish_levels import PolishLevel
    from clm.notebooks.slide_writer import write_narrative

    console.print(f"[bold]Parsing slides:[/bold] {slides}")
    slide_groups = parse_slides(slides, lang)
    console.print(f"  Found {len(slide_groups)} slide groups")

    effective_level = PolishLevel(polish_level)
    rng = _range_or_usage_error(slides_range)

    # Collect slides with existing notes
    slides_with_notes: dict[int, tuple[str, str]] = {}  # idx -> (notes_text, slide_content)
    for sg in slide_groups:
        if not sg.has_notes or not sg.notes_text.strip():
            continue
        if rng is not None and not (rng[0] <= sg.index <= rng[1]):
            continue
        slides_with_notes[sg.index] = (sg.notes_text, sg.text_content)

    if not slides_with_notes:
        console.print("[yellow]No notes found to polish.[/yellow]")
        sys.exit(EXIT_CLEAN)

    # Fail fast when the model is needed but no key is available (the
    # verbatim passthrough makes no LLM call, so it stays key-free).
    if effective_level != PolishLevel.verbatim and not os.environ.get("OPENAI_API_KEY"):
        click.echo(
            "error: OPENAI_API_KEY is not set; cannot polish the notes. Set a "
            "key and re-run — or drive the model-free loop: `clm slides polish "
            "task` + `clm slides polish accept` (no key needed).",
            err=True,
        )
        sys.exit(EXIT_WORK_PENDING)

    console.print(f"  {len(slides_with_notes)} slides with notes to polish")
    polished_map = asyncio.run(
        _polish_all(slides_with_notes, model=model, polish_level=effective_level)
    )

    for idx in sorted(polished_map.keys()):
        title = ""
        for sg in slide_groups:
            if sg.index == idx:
                title = sg.title[:40]
                break
        console.print(f"\n[bold]Slide {idx}[/bold] ({title})")
        console.print(f"[dim]{polished_map[idx][:200]}[/dim]")

    if dry_run:
        console.print("\n[yellow]Dry run — no changes written.[/yellow]")
        sys.exit(EXIT_CLEAN)

    dest = write_narrative(slides, polished_map, lang, tag="notes", output_path=output)
    console.print(f"\n[green]Polished notes written to {dest}[/green]")
    sys.exit(EXIT_CLEAN)


async def _polish_all(
    slides_with_notes: dict[int, tuple[str, str]],
    *,
    model: str | None = None,
    polish_level=None,
) -> dict[int, str]:
    """Polish all notes via LLM."""
    from clm.notebooks.polish import polish_text
    from clm.notebooks.polish_levels import PolishLevel as _PolishLevel

    effective_level = polish_level if polish_level is not None else _PolishLevel.standard

    kwargs: dict = {}
    if model:
        kwargs["model"] = model
    kwargs["polish_level"] = effective_level

    polished: dict[int, str] = {}
    for idx, (notes_text, slide_content) in slides_with_notes.items():
        console.print(f"  Polishing slide {idx}...")
        polished[idx] = await polish_text(notes_text, slide_content, **kwargs)

    return polished
