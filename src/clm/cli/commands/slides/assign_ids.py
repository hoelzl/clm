"""``clm slides assign-ids`` — Phase 2 of the slide-format-redesign.

Wraps :func:`clm.slides.assign_ids.assign_ids_in_file` /
``assign_ids_in_directory`` with the flag matrix specified in §2.3 of
the redesign handover and prints a human-readable (or JSON) report.
Since #963 it is a hidden verb group: ``run`` (the bare default) is the
minting run; ``accept`` lands agent-proposed titles for refused cells
(the framing is the ``run --report-only --report-refusals --context
--json`` worklist). The former ``--llm-suggest`` Ollama path was removed
— no verb here imports the Ollama client.

Exit codes:

- ``0`` — all visited cells assigned successfully, or no work to do
- ``1`` — at least one soft refusal (extractable, needs author input)
- ``2`` — at least one hard refusal (no-content cell, blocks the run);
  for ``accept``: the answer was rejected (nothing written)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn

import click

from clm.cli._default_verb_group import DefaultVerbGroup
from clm.cli.commands.shared import has_deck_scope, resolve_scoped_files
from clm.slides.assign_ids import (
    AssignOptions,
    AssignResult,
    assign_ids_in_directory,
    assign_ids_in_file,
    assign_ids_in_files,
)
from clm.slides.refusal_report import (
    build_refusal_worklist,
    render_worklist,
    worklist_to_dict,
)


class _AssignIdsVerbGroup(DefaultVerbGroup):
    """Bare ``assign-ids PATH …`` runs the minting verb (hidden plumbing)."""

    default_verb = "run"


@click.group("assign-ids", cls=_AssignIdsVerbGroup, hidden=True)
def assign_ids_group() -> None:
    """Generate stable ``slide_id`` metadata for slide/subslide cells (plumbing).

    \b
    Bare `clm slides assign-ids PATH …` == `clm slides assign-ids run PATH …`.
    Verbs:
      run      the minting run (the Phase-2 flag matrix)
      accept   land agent-proposed titles for refused cells (#963)
    """


@assign_ids_group.command("run", hidden=True)
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Regenerate ids on cells where the algorithm can produce one. "
        "Cells with `!`-prefixed ids and cells the algorithm cannot "
        "propose for are left untouched."
    ),
)
@click.option(
    "--accept-content-derived",
    is_flag=True,
    help=(
        "Bulk-accept content-derived proposals (first bullet, prominent "
        "bold, image alt, prose, and the code AST constructs) for "
        "headingless slides. Bare-expression code cells and hard-refusal "
        "cells still refuse."
    ),
)
@click.option(
    "--accept-code-derived",
    is_flag=True,
    help=(
        "Bulk-accept a first-code-line slug for bare-expression code cells "
        "that have no heading and no extractable construct (e.g. "
        "`(1 + 1j) * (1 + 1j)` -> `1-1j-1-1j`, `letters[0:3]` -> "
        "`letters-0-3`). Comment-token-aware, so it works for non-Python "
        "decks too. Genuinely empty / magic-only cells still refuse. "
        "Independent of --accept-content-derived; the conversion pipeline "
        "usually passes both."
    ),
)
@click.option(
    "--report-only",
    "--dry-run",
    "report_only",
    is_flag=True,
    help="List planned assignments and refusals without modifying any file.",
)
@click.option(
    "--only",
    type=click.Choice(["bilingual", "split"]),
    default=None,
    help="Scope a directory run to only bilingual decks (no .de/.en tag) or only "
    "split halves. E.g. --only bilingual mints bilingual decks while leaving "
    ".de/.en pairs for `clm slides sync`.",
)
@click.option(
    "--exclude",
    multiple=True,
    metavar="GLOB",
    help="Skip decks matching GLOB (matched against the full path and each path "
    "component, so `--exclude _archive` skips an _archive/ dir). Repeatable.",
)
@click.option(
    "--shipping-only",
    is_flag=True,
    help="Scope a directory run to decks reachable from course specs (the shipping set).",
)
@click.option(
    "--specs-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="For --shipping-only: directory of *.xml specs. Default: <course-root>/course-specs/.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Course data directory (contains slides/). For --shipping-only scope resolution.",
)
@click.option(
    "--report-refusals",
    is_flag=True,
    help="Emit a hand-authoring worklist of the refusals (hard ones first) instead "
    "of the assignment listing — the cells that still need a slide_id.",
)
@click.option(
    "--context",
    is_flag=True,
    help="With --report-refusals, include each refused cell's marker, body, and the "
    "nearest preceding slide_id/heading so you can author an id in place. Implies "
    "--report-refusals.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def assign_ids_cmd(
    path: Path,
    force: bool,
    accept_content_derived: bool,
    accept_code_derived: bool,
    report_only: bool,
    only: str | None,
    exclude: tuple[str, ...],
    shipping_only: bool,
    specs_dir: Path | None,
    data_dir: Path | None,
    report_refusals: bool,
    context: bool,
    as_json: bool,
) -> None:
    """Generate stable ``slide_id`` metadata for slide/subslide cells (plumbing).

    PATH is a single .py slide file or a directory containing slide files.

    \b
    This is a low-level tool for agents/scripts and is hidden from the normal
    command surface. For everyday authoring, id minting happens automatically:
      * ``clm slides sync`` mints a shared id onto both halves of a split deck.
      * ``clm slides normalize`` runs the same minting as one of its passes.
      * On a directory, this command mints EN-authority ids across each
        ``.de.py``/``.en.py`` pair at once (parity-safe). Running it on a
        *single* split half can mint a divergent slug — prefer the funnels above.

    \b
    Three-category policy:
      headed       Slug derived from the first markdown heading.
      extractable  Refused by default; --accept-content-derived opts into
                   auto-acceptance (agent titles land via `assign-ids accept`).
      code-derived Bare-expression code cells (no heading, no construct);
                   --accept-code-derived slugs the first code line.
      no content   Hard refuse; author must write slide_id="..." by hand
                   (genuinely empty / pure-punctuation / magic-only cells)
                   — or propose titles via `assign-ids accept`.

    \b
    Special cases:
      * Title slides (j2 header() macro) always become slide_id="title".
      * `!`-prefixed ids (preserve marker) are never regenerated.
      * Voiceover/notes cells inherit the slide_id of the slide they describe.
    """
    options = AssignOptions(
        force=force,
        accept_content_derived=accept_content_derived,
        accept_code_derived=accept_code_derived,
        report_only=report_only,
    )

    scoped = has_deck_scope(only, exclude, shipping_only)
    if scoped and not path.is_dir():
        raise click.UsageError(
            "--only / --exclude / --shipping-only apply to a directory, not a single file."
        )

    if scoped:
        files = resolve_scoped_files(
            path,
            only=only,
            exclude=exclude,
            shipping_only=shipping_only,
            specs_dir=specs_dir,
            data_dir=data_dir,
        )
        result = assign_ids_in_files(files, options)
    elif path.is_dir():
        result = assign_ids_in_directory(path, options)
    elif path.is_file():
        result = assign_ids_in_file(path, options)
    else:
        raise click.ClickException(f"PATH must be a slide file or directory: {path}")

    # --context implies the refusal-worklist view.
    report_refusals = report_refusals or context

    if report_refusals:
        worklist = build_refusal_worklist(result.refusals, with_context=context)
        if as_json:
            click.echo(json.dumps(worklist_to_dict(worklist), indent=2))
        else:
            click.echo(render_worklist(worklist))
    elif as_json:
        click.echo(json.dumps(_to_dict(result), indent=2))
    else:
        _print_human(result, report_only=report_only)

    sys.exit(_exit_code(result))


def _print_human(result: AssignResult, *, report_only: bool) -> None:
    prefix = "[report-only] " if report_only else ""

    for a in result.assignments:
        click.echo(
            f'{prefix}assign {a.file}:{a.line} -> slide_id="{a.slide_id}" (source={a.source})'
        )

    soft = [r for r in result.refusals if r.severity == "soft"]
    hard = [r for r in result.refusals if r.severity == "hard"]

    for r in soft:
        proposal = f' proposed="{r.proposed_slug}"' if r.proposed_slug else ""
        title = f' title="{r.proposed_title}"' if r.proposed_title else ""
        # The remedy is this surface's to name: the flag lives on THIS command
        # (via `normalize` the same refusal must name the full command, #892).
        remedy = f"; pass {r.accept_flag} to accept" if r.accept_flag else ""
        click.echo(f"refuse-soft {r.file}:{r.line} — {r.reason}{remedy}{title}{proposal}")

    for r in hard:
        click.echo(f"refuse-hard {r.file}:{r.line} — {r.reason}")

    click.echo()
    click.echo(
        f"{prefix}{result.files_visited} file(s) visited, "
        f"{result.files_modified} modified, "
        f"{len(result.assignments)} assigned, "
        f"{len(soft)} soft refusal(s), "
        f"{len(hard)} hard refusal(s)."
    )


def _to_dict(result: AssignResult) -> dict:
    return {
        "files_visited": result.files_visited,
        "files_modified": result.files_modified,
        "assignments": [
            {
                "file": a.file,
                "line": a.line,
                "slide_id": a.slide_id,
                "source": a.source,
            }
            for a in result.assignments
        ],
        "refusals": [
            {
                "file": r.file,
                "line": r.line,
                "severity": r.severity,
                "reason": r.reason,
                "proposed_slug": r.proposed_slug,
                "proposed_title": r.proposed_title,
                "accept_flag": r.accept_flag,
            }
            for r in result.refusals
        ],
    }


def _exit_code(result: AssignResult) -> int:
    if result.has_hard_refusals:
        return 2
    if any(r.severity == "soft" for r in result.refusals):
        return 1
    return 0


# ---------------------------------------------------------------------------
# accept — land agent-proposed titles for refused cells (#963)
# ---------------------------------------------------------------------------


@assign_ids_group.command("accept", hidden=True)
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--answers",
    "answers_src",
    required=True,
    help="The answer document: a file path, or '-' for stdin.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate the answers fully (shape + freshness + slugs) and write nothing.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the JSON outcome envelope.")
def assign_ids_accept_cmd(path: Path, answers_src: str, dry_run: bool, as_json: bool) -> None:
    """Land agent-proposed titles for refused cells (no LLM, no Ollama).

    Frame the work with `assign-ids run PATH --report-only
    --report-refusals --context --json`, propose a short English title for
    each refused cell, and answer with one {file, line, title, body} row
    per cell (body = the worklist's context.body, echoed verbatim).
    Accept re-checks each row against the live file (body freshness, the
    cell still id-less), slugs the titles through the engine's own
    slugifier, keeps split halves consistent, and stamps the ids
    atomically. Nothing is written on any validation failure.

    \b
    Exit codes: 0 stamped · 2 rejected / error (nothing written).
    """
    from clm.slides.agent_task import (
        EXIT_CLEAN,
        EXIT_ERROR,
        VALIDATORS,
        AnswerRejected,
        rejection_payload,
    )
    from clm.slides.assign_ids_accept import ANSWER_VALIDATOR, apply_answers

    if answers_src == "-":
        raw = click.get_text_stream("stdin").read()
    else:
        answers_path = Path(answers_src)
        if not answers_path.exists():
            raise click.UsageError(f"answers file not found: {answers_src}")
        raw = answers_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise click.UsageError(f"the answer is not valid JSON: {exc}") from exc

    def _reject(reason: str) -> NoReturn:
        if as_json:
            click.echo(
                json.dumps(
                    rejection_payload(1, tool="assign-ids", verb="accept", reason=reason),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            click.echo(f"rejected: {reason}", err=True)
        sys.exit(EXIT_ERROR)

    try:
        answer = VALIDATORS.get(ANSWER_VALIDATOR)(payload)
        scope = path.resolve()
        for row in answer.rows:
            row_path = Path(row.file).resolve()
            in_scope = scope == row_path or scope in row_path.parents
            if not in_scope:
                raise AnswerRejected(
                    f"{row.file}:{row.line}: the answer targets a file outside PATH "
                    f"({path}) — scope the answer to the framed run"
                )
        outcome = apply_answers(answer, dry_run=dry_run)
    except (AnswerRejected, KeyError, ValueError, TypeError, OSError) as exc:
        _reject(str(exc))

    payload_out = outcome.to_payload()
    payload_out["dry_run"] = dry_run
    if as_json:
        click.echo(json.dumps(payload_out, indent=2, ensure_ascii=False))
    else:
        for entry in outcome.stamped:
            click.echo(
                f"{'[dry-run] ' if dry_run else ''}stamp {entry['file']}:{entry['line']} "
                f'-> slide_id="{entry["slide_id"]}" (title="{entry["title"]}")'
            )
        verb = "validated (dry-run)" if dry_run else "stamped"
        click.echo(
            f"{verb} {len(outcome.stamped)} slide_id(s) across {len(outcome.written)} file(s)."
        )
    sys.exit(EXIT_CLEAN)
