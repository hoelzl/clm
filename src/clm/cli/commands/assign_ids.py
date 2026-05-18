"""``clm slides assign-ids`` — Phase 2 of the slide-format-redesign.

Wraps :func:`clm.slides.assign_ids.assign_ids_in_file` /
``assign_ids_in_directory`` with the flag matrix specified in §2.3 of
the redesign handover and prints a human-readable (or JSON) report.

Exit codes:

- ``0`` — all visited cells assigned successfully, or no work to do
- ``1`` — at least one soft refusal (extractable, needs author input)
- ``2`` — at least one hard refusal (no-content cell, blocks the run)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.infrastructure.llm.cache import TitleSuggestionCache, resolve_cache_dir
from clm.infrastructure.llm.ollama_client import (
    DEFAULT_TITLE_MODEL,
    OllamaTitleSuggester,
    TitleSuggester,
    is_available,
)
from clm.slides.assign_ids import (
    AssignOptions,
    AssignResult,
    assign_ids_in_directory,
    assign_ids_in_file,
)

CACHE_DB_NAME = "clm-llm.sqlite"


@click.command("assign-ids")
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
        "bold, image alt) for headingless slides. Hard-refusal cells "
        "still refuse."
    ),
)
@click.option(
    "--llm-suggest",
    is_flag=True,
    help=(
        "Use the local LLM (Ollama) to propose a short title for "
        "headingless-but-extractable cells. Suggestions are cached in "
        "the clm-llm.sqlite cache. Falls back silently when Ollama is "
        "unreachable."
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
    "--llm-model",
    default=DEFAULT_TITLE_MODEL,
    show_default=True,
    help="Ollama model name used with --llm-suggest.",
)
@click.option(
    "--ollama-url",
    default=None,
    help=("Base URL of the Ollama daemon. Defaults to $OLLAMA_URL or http://localhost:11434."),
)
@click.option(
    "--llm-timeout",
    type=float,
    default=120.0,
    show_default=True,
    help=(
        "Per-call timeout (seconds) for the LLM suggester. Cold-load on "
        "a 30B local model can take a minute; bump this if you see "
        "timeouts."
    ),
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Directory for the LLM cache (default: --cache-dir > $CLM_CACHE_DIR > "
        "tool.clm.cache_dir in pyproject.toml > <cwd>/.clm-cache/)."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def assign_ids_cmd(
    path: Path,
    force: bool,
    accept_content_derived: bool,
    llm_suggest: bool,
    report_only: bool,
    llm_model: str,
    ollama_url: str | None,
    llm_timeout: float,
    cache_dir: Path | None,
    as_json: bool,
) -> None:
    """Generate stable ``slide_id`` metadata for slide/subslide cells.

    PATH is a single .py slide file or a directory containing slide files.

    \b
    Three-category policy:
      headed       Slug derived from the first markdown heading.
      extractable  Refused by default; --accept-content-derived or
                   --llm-suggest opt into auto-acceptance.
      no content   Hard refuse; author must write slide_id="..." by hand.

    \b
    Special cases:
      * Title slides (j2 header() macro) always become slide_id="title".
      * `!`-prefixed ids (preserve marker) are never regenerated.
      * Voiceover/notes cells inherit the slide_id of the slide they describe.
    """
    suggester: TitleSuggester | None = None
    cache: TitleSuggestionCache | None = None

    if llm_suggest:
        suggester = OllamaTitleSuggester(
            model=llm_model,
            base_url=ollama_url,
            timeout=llm_timeout,
        )
        if not is_available(suggester):
            click.echo(
                "warning: --llm-suggest requested but Ollama is not "
                f"reachable at {suggester.base_url}; falling back to "
                "refusal for headingless slides.",
                err=True,
            )
            suggester = None
        else:
            cache_root = resolve_cache_dir(cli_override=cache_dir)
            cache = TitleSuggestionCache(cache_root / CACHE_DB_NAME)

    options = AssignOptions(
        force=force,
        accept_content_derived=accept_content_derived,
        llm_suggest=llm_suggest and suggester is not None,
        report_only=report_only,
        llm_suggester=suggester,
        llm_cache=cache,
    )

    try:
        if path.is_dir():
            result = assign_ids_in_directory(path, options)
        elif path.is_file():
            result = assign_ids_in_file(path, options)
        else:
            raise click.ClickException(f"PATH must be a slide file or directory: {path}")
    finally:
        if cache is not None:
            cache.close()

    if as_json:
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
        click.echo(f"refuse-soft {r.file}:{r.line} — {r.reason}{title}{proposal}")

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
