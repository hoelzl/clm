"""``clm slides coverage`` — Phase 4 of the slide-format-redesign.

Drives :func:`clm.slides.coverage.check_coverage_in_file` /
``check_coverage_in_directory`` over a deck or directory tree and
prints findings (human or JSON). The full sweep is intended as a
manual pre-commit step; the PostToolUse hook on PythonCourses surfaces
the same findings at edit time without blocking.

Severity for "uncovered bullet" findings starts at ``warning`` per
§3 Phase 4 of the handover. Once the false-positive rate against a
real ML AZAV deck is known, the rollout can promote to ``error``
(same option-B pattern Phase 3 used for the missing-slide_id warning).

Exit codes:

- ``0`` — no findings (or only findings at info severity)
- ``1`` — at least one finding at warning or error severity
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.infrastructure.llm.cache import CoverageCache, resolve_cache_dir
from clm.infrastructure.llm.ollama_client import (
    DEFAULT_COVERAGE_MODEL,
    CoverageJudge,
    OllamaCoverageJudge,
    is_available,
)
from clm.slides.coverage import (
    CoverageOptions,
    CoverageResult,
    check_coverage_in_directory,
    check_coverage_in_file,
)

CACHE_DB_NAME = "clm-llm.sqlite"


@click.command("coverage")
@click.argument("path", type=click.Path(exists=True, path_type=Path), required=False)
@click.option(
    "--llm-model",
    default=DEFAULT_COVERAGE_MODEL,
    show_default=True,
    help="Ollama model name used to judge coverage.",
)
@click.option(
    "--ollama-url",
    default=None,
    help="Base URL of the Ollama daemon. Defaults to $OLLAMA_URL or http://localhost:11434.",
)
@click.option(
    "--llm-timeout",
    type=float,
    default=120.0,
    show_default=True,
    help=(
        "Per-call timeout (seconds) for the coverage judge. Cold-load on "
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
@click.option(
    "--report-only",
    is_flag=True,
    help=(
        "Skip cache writes (reads still happen). Useful for measuring the "
        "current cache hit rate without persisting fresh verdicts."
    ),
)
@click.option(
    "--dump",
    is_flag=True,
    help=(
        "Print a readable text dump of cached verdicts instead of running "
        "a coverage check. PATH is ignored when --dump is set."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def coverage_cmd(
    path: Path | None,
    llm_model: str,
    ollama_url: str | None,
    llm_timeout: float,
    cache_dir: Path | None,
    report_only: bool,
    dump: bool,
    as_json: bool,
) -> None:
    """Check whether voiceover cells cover every bullet on their slide.

    PATH is a single ``.py`` slide file or a directory containing slide
    files. Findings are cached in the LLM cache database keyed by
    ``(slide_hash, voiceover_hash, prompt_version, lang)`` so re-runs
    are free when the deck hasn't changed.

    \b
    The judge runs against a local Ollama daemon. When Ollama is
    unreachable the command still works in cache-only mode: cached
    verdicts surface, fresh pairs are reported as skipped, no LLM
    calls are made.
    """
    if dump:
        _run_dump(cache_dir=cache_dir, as_json=as_json)
        sys.exit(0)

    if path is None:
        raise click.UsageError("PATH is required (use --dump to inspect the cache)")

    cache_root = resolve_cache_dir(cli_override=cache_dir)
    cache = CoverageCache(cache_root / CACHE_DB_NAME)

    ollama_judge = OllamaCoverageJudge(
        model=llm_model,
        base_url=ollama_url,
        timeout=llm_timeout,
    )
    judge: CoverageJudge | None = ollama_judge
    if not is_available(judge):
        click.echo(
            f"warning: Ollama is not reachable at {ollama_judge.base_url}; "
            "running in cache-only mode. Fresh pairs will be reported as skipped.",
            err=True,
        )
        judge = None

    options = CoverageOptions(
        judge=judge,
        cache=cache,
        report_only=report_only,
    )

    try:
        if path.is_dir():
            result = check_coverage_in_directory(path, options)
        elif path.is_file():
            result = check_coverage_in_file(path, options)
        else:
            raise click.ClickException(f"PATH must be a slide file or directory: {path}")
    finally:
        cache.close()

    if as_json:
        click.echo(json.dumps(_to_dict(result), indent=2))
    else:
        _print_human(result)

    sys.exit(_exit_code(result))


def _print_human(result: CoverageResult) -> None:
    for f in result.findings:
        click.echo(
            f"{f.severity}: {f.file}:{f.line} [{f.lang}] slide_id={f.slide_id!r} — {f.message}"
        )
        for bullet in f.uncovered_bullets:
            click.echo(f"    - {bullet}")
        if f.suggestion:
            click.echo(f"  suggestion: {f.suggestion}")

    click.echo()
    click.echo(
        f"{result.files_visited} file(s) visited, "
        f"{result.pairs_total} pair(s) found, "
        f"{result.pairs_checked} checked, "
        f"{result.cache_hits} cache hit(s), "
        f"{result.llm_calls} LLM call(s), "
        f"{result.pairs_skipped} skipped, "
        f"{result.pairs_in_workshop} workshop slide(s) excluded, "
        f"{len(result.findings)} finding(s)."
    )


def _to_dict(result: CoverageResult) -> dict[str, object]:
    return {
        "files_visited": result.files_visited,
        "pairs_total": result.pairs_total,
        "pairs_checked": result.pairs_checked,
        "cache_hits": result.cache_hits,
        "llm_calls": result.llm_calls,
        "pairs_skipped": result.pairs_skipped,
        "pairs_in_workshop": result.pairs_in_workshop,
        "findings": [
            {
                "severity": f.severity,
                "file": f.file,
                "line": f.line,
                "lang": f.lang,
                "slide_id": f.slide_id,
                "message": f.message,
                "suggestion": f.suggestion,
                "uncovered_bullets": list(f.uncovered_bullets),
            }
            for f in result.findings
        ],
    }


def _exit_code(result: CoverageResult) -> int:
    if any(f.severity in ("error", "warning") for f in result.findings):
        return 1
    return 0


# ---------------------------------------------------------------------------
# --dump implementation
# ---------------------------------------------------------------------------


def _run_dump(*, cache_dir: Path | None, as_json: bool) -> None:
    """Dump every cached coverage verdict for human inspection."""
    cache_root = resolve_cache_dir(cli_override=cache_dir)
    cache = CoverageCache(cache_root / CACHE_DB_NAME)
    try:
        entries = cache.iter_entries()
    finally:
        cache.close()

    if as_json:
        click.echo(json.dumps([_entry_to_dict(e) for e in entries], indent=2))
        return

    if not entries:
        click.echo(f"(no cached verdicts in {cache_root / CACHE_DB_NAME})")
        return

    for slide_hash, voice_hash, prompt_version, lang, verdict, gap_details, checked_at in entries:
        click.echo(
            f"[{checked_at}] {verdict.upper()} lang={lang} "
            f"prompt={prompt_version} slide={slide_hash[:12]} "
            f"voiceover={voice_hash[:12]}"
        )
        if gap_details:
            try:
                payload = json.loads(gap_details)
            except (ValueError, TypeError):
                click.echo(f"  (gap_details unparseable: {gap_details[:80]!r})")
                continue
            for bullet in payload.get("bullets", []):
                marker = "[+]" if bullet.get("covered") else "[-]"
                text = bullet.get("text", "")
                reason = bullet.get("reason", "")
                line = f"  {marker} {text}"
                if reason:
                    line = f"{line}  ({reason})"
                click.echo(line)


def _entry_to_dict(entry: tuple[str, str, str, str, str, str | None, str]) -> dict[str, object]:
    slide_hash, voice_hash, prompt_version, lang, verdict, gap_details, checked_at = entry
    return {
        "slide_hash": slide_hash,
        "voiceover_hash": voice_hash,
        "prompt_version": prompt_version,
        "lang": lang,
        "verdict": verdict,
        "gap_details": json.loads(gap_details) if gap_details else None,
        "checked_at": checked_at,
    }
