"""``clm slides coverage`` — the voiceover-coverage agent toolkit (#963).

Phase 4 of the slide-format-redesign created this as a stand-alone command
that asked a local Ollama model per (slide, voiceover) pair; issue #963
re-cut it into the agent-toolkit contract (``clm info agent-tasks``).

Verbs:

- ``report`` (the bare default) — read-only. Frames the judgment: one item
  per (slide, lang) pair with its bullets, the voiceover (both language
  sides are independent pairs), the judge's system prompt verbatim as
  ``instructions``, the cache state (``pending`` / ``cached`` /
  ``no-voiceover``), and the ``answer_schema``. No model, no Ollama.
  ``--dump`` keeps its old behavior (print cached verdicts; PATH ignored).
- ``accept`` — validate an answer document (shape + per-pair freshness +
  coverage of exactly the pending pairs) and bank each verdict into the
  existing :class:`~clm.infrastructure.llm.cache.CoverageCache` rows —
  the same ``(slide_hash, voiceover_hash, prompt_version, lang)`` key the
  embedded judge wrote — then emit the findings.
- ``autopilot`` — the legacy in-process Ollama judge for the agent-less
  human; falls back to cache-only mode when the daemon is unreachable,
  never in CI.

Severity for "uncovered bullet" findings stays at ``warning`` per the
Phase 3-style rollout. Exit codes (``0`` clean / ``1`` work pending /
``2`` error): ``report`` exits ``1`` while findings exist or pairs are
pending; ``accept`` exits ``0`` banked (findings still exit ``1`` — the
gaps are real work), ``2`` rejected with nothing written.

This module must not import the Ollama client at top level — the
report/accept path is model-free (#963 acceptance).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

import click

from clm.cli._default_verb_group import DefaultVerbGroup
from clm.infrastructure.llm.cache import CoverageCache, resolve_cache_dir
from clm.slides.agent_task import EXIT_CLEAN, EXIT_ERROR, EXIT_WORK_PENDING
from clm.slides.coverage import CoverageOptions, check_coverage_in_file
from clm.slides.coverage_task import (
    ANSWER_VALIDATOR,
    TASK_SCHEMA,
    CoverageAnswer,
    build_report,
    prepare_accept,
)

CACHE_DB_NAME = "clm-llm.sqlite"


@click.group("coverage", cls=DefaultVerbGroup)
def coverage_group() -> None:
    """Check whether voiceover cells cover every bullet on their slide.

    \b
    Bare `clm slides coverage PATH` == `clm slides coverage report PATH`
    (read-only). Verbs:
      report     frame the judgment: pending pairs + cached verdicts + findings
      accept     validate an answer document and bank the verdicts into the cache
      autopilot  the legacy in-process Ollama judge (needs a local daemon)

    \b
    The framing and the banking follow the agent-task contract
    (`clm info sync-agents` → "Judging voiceover coverage"); the engine
    never calls a model outside `autopilot`.
    """


#: Shared options.
_PATH_ARG = click.argument(
    "path",
    type=click.Path(exists=True, path_type=Path),
    required=False,
)
_CACHE_DIR_OPTION = click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Directory for the LLM cache (default: --cache-dir > $CLM_CACHE_DIR > "
        "tool.clm.cache_dir in pyproject.toml > <cwd>/.clm-cache/)."
    ),
)


def _open_cache(cache_dir: Path | None) -> CoverageCache:
    return CoverageCache(resolve_cache_dir(cli_override=cache_dir) / CACHE_DB_NAME)


# ---------------------------------------------------------------------------
# report — the bare default verb (read-only, model-free)
# ---------------------------------------------------------------------------


@coverage_group.command("report")
@_PATH_ARG
@_CACHE_DIR_OPTION
@click.option(
    "--dump",
    is_flag=True,
    help=(
        "Print a readable text dump of cached verdicts instead of framing "
        "the coverage judgment. PATH is ignored when --dump is set."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def coverage_report_cmd(
    path: Path | None, cache_dir: Path | None, dump: bool, as_json: bool
) -> None:
    """Frame the voiceover-coverage judgment (writes nothing, no model).

    One item per (slide, lang) pair with bullets: ``pending`` pairs carry
    the voiceover and the content-hash freshness tokens an answer must
    echo; ``cached`` pairs carry their banked verdict; ``no-voiceover``
    pairs surface as findings immediately. The judge's system prompt is
    embedded verbatim in ``instructions``. Exit ``1`` while findings
    exist or pairs are pending, ``0`` when everything is covered.
    """
    if dump:
        _run_dump(cache_dir=cache_dir, as_json=as_json)
        sys.exit(EXIT_CLEAN)

    if path is None:
        raise click.UsageError("PATH is required (use --dump to inspect the cache)")

    cache = _open_cache(cache_dir)
    try:
        payload = build_report(path, cache=cache)
    finally:
        cache.close()

    counts = payload["counts"]
    pending = counts["pending"]
    exit_code = EXIT_WORK_PENDING if (pending or counts["findings"]) else EXIT_CLEAN
    payload["exit_code"] = exit_code
    if as_json:
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.exit(exit_code)
    for f in payload["findings"]:
        click.echo(
            f"{f['severity']}: {f['file']}:{f['line']} [{f['lang']}] "
            f"slide_id={f['slide_id']!r} — {f['message']}"
        )
        for bullet in f["uncovered_bullets"]:
            click.echo(f"    - {bullet}")
    click.echo()
    click.echo(
        f"{counts['pairs_total']} pair(s): {counts['pending']} pending judgment, "
        f"{counts['cached']} cached, {counts['no_voiceover']} without voiceover, "
        f"{counts['findings']} finding(s)."
    )
    if pending:
        click.echo(
            "Frame the answer from this report's `items` (status `pending`), then "
            f"bank it with `clm slides coverage accept {path} --answer answer.json` "
            "(no Ollama needed), or run `clm slides coverage autopilot` with one."
        )
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# accept — the validated bank
# ---------------------------------------------------------------------------


@coverage_group.command("accept")
@_PATH_ARG
@_CACHE_DIR_OPTION
@click.option(
    "--answer",
    "answer_src",
    required=True,
    help="The answer document framed by `report`: a file path, or '-' for stdin.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate the answer fully (shape + freshness + coverage) and bank nothing.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the JSON outcome envelope.")
def coverage_accept_cmd(
    path: Path | None,
    cache_dir: Path | None,
    answer_src: str,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Validate an answer document and bank the verdicts into the cache.

    Validates the answer against the report's schema and the LIVE pairs
    (per-pair content hashes — a concurrent deck edit rejects, never
    merges), requires coverage of exactly the pending pairs and verbatim
    bullet texts, then writes each verdict as the same cache row the
    embedded judge would have written. Uses no model and no Ollama;
    nothing is banked on any validation failure. After banking (and on
    ``--dry-run``), the findings are re-emitted from the cache.

    \b
    Exit codes: 0 banked and no findings · 1 banked but findings remain ·
    2 rejected / error (nothing banked).
    """
    if path is None:
        raise click.UsageError("PATH is required")

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
                    _rejection(reason),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            click.echo(f"rejected: {reason}", err=True)
        sys.exit(EXIT_ERROR)

    from clm.slides.agent_task import VALIDATORS, AnswerRejected

    cache = _open_cache(cache_dir)
    try:
        try:
            answer: CoverageAnswer = VALIDATORS.get(ANSWER_VALIDATOR)(payload)
            plan = prepare_accept(path, answer, cache=cache)
        except (AnswerRejected, KeyError, ValueError, TypeError) as exc:
            # AnswerRejected: a named violation. KeyError: the validator label
            # is unknown (registry drift). ValueError/TypeError: a non-object
            # payload slipping past json.loads.
            _reject(str(exc))
        if not dry_run:
            for slide_hash, voiceover_hash, lang, verdict in plan.rows:
                cache.put(
                    slide_hash,
                    voiceover_hash,
                    answer.prompt_version,
                    lang,
                    verdict.verdict,
                    verdict.to_json(),
                )
        # Emit the findings from the (possibly freshly banked) cache —
        # the same read the autopilot/report path performs.
        from clm.slides.coverage import check_coverage_in_directory

        if path.is_dir():
            result = check_coverage_in_directory(path, CoverageOptions(judge=None, cache=cache))
        else:
            result = check_coverage_in_file(path, CoverageOptions(judge=None, cache=cache))
    except OSError as exc:
        # SQLite/file races (the AV/OneDrive lock races this repo documents)
        # are errors, not tracebacks — and the 0/2 contract stays honest.
        _reject(f"cache read/write failed: {exc}")
    finally:
        cache.close()

    findings: list[dict[str, Any]] = [
        {
            "severity": f.severity,
            "file": f.file,
            "line": f.line,
            "lang": f.lang,
            "slide_id": f.slide_id,
            "message": f.message,
            "uncovered_bullets": list(f.uncovered_bullets),
        }
        for f in result.findings
    ]
    exit_code = EXIT_WORK_PENDING if result.has_findings else EXIT_CLEAN
    outcome = {
        "schema": TASK_SCHEMA,
        "tool": "coverage",
        "verb": "accept",
        "applied": not dry_run,
        "dry_run": dry_run,
        "source": str(path),
        "prompt_version": answer.prompt_version,
        "verdicts_banked": 0 if dry_run else len(plan.rows),
        "cache_hits": result.cache_hits,
        "pairs_skipped": result.pairs_skipped,
        "findings": findings,
        "exit_code": exit_code,
    }
    if as_json:
        click.echo(json.dumps(outcome, indent=2, ensure_ascii=False))
    else:
        for f in findings:
            click.echo(
                f"{f['severity']}: {f['file']}:{f['line']} [{f['lang']}] "
                f"slide_id={f['slide_id']!r} — {f['message']}"
            )
            for bullet in f["uncovered_bullets"]:
                click.echo(f"    - {bullet}")
        verb = "validated (dry-run)" if dry_run else "banked"
        click.echo(
            f"{verb} {len(plan.rows)} verdict(s); {result.cache_hits} cache hit(s), "
            f"{len(findings)} finding(s)."
        )
    sys.exit(exit_code)


def _rejection(reason: str) -> dict:
    from clm.slides.agent_task import rejection_payload

    return rejection_payload(TASK_SCHEMA, tool="coverage", verb="accept", reason=reason)


# ---------------------------------------------------------------------------
# autopilot — the legacy in-process Ollama judge (daemon-gated)
# ---------------------------------------------------------------------------


@coverage_group.command("autopilot")
@_PATH_ARG
@click.option(
    "--llm-model",
    default=None,
    help="Ollama model name used to judge coverage (default: qwen3:30b).",
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
@_CACHE_DIR_OPTION
@click.option(
    "--report-only",
    is_flag=True,
    help=(
        "Skip cache writes (reads still happen). Useful for measuring the "
        "current cache hit rate without persisting fresh verdicts."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def coverage_autopilot_cmd(
    path: Path | None,
    llm_model: str | None,
    ollama_url: str | None,
    llm_timeout: float,
    cache_dir: Path | None,
    report_only: bool,
    as_json: bool,
) -> None:
    """Judge coverage in-process through a local Ollama daemon.

    The agent-less path: a human with Ollama running. The banking keys and
    findings are those of ``accept``; only the judgment source differs
    (the embedded model instead of an answer document). When Ollama is
    unreachable the command still works in cache-only mode: cached
    verdicts surface, fresh pairs are reported as skipped, no LLM calls
    are made. Agents and CI should use ``report`` / ``accept`` — no
    daemon, same cache rows.
    """
    from clm.infrastructure.llm.ollama_client import (
        DEFAULT_COVERAGE_MODEL,
        CoverageJudge,
        OllamaCoverageJudge,
        is_available,
    )
    from clm.slides.coverage import (
        check_coverage_in_directory,
    )

    if path is None:
        raise click.UsageError(
            "PATH is required (use `coverage report --dump` to inspect the cache)"
        )

    cache = _open_cache(cache_dir)

    ollama_judge = OllamaCoverageJudge(
        model=llm_model or DEFAULT_COVERAGE_MODEL,
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
        click.echo(json.dumps(_result_to_dict(result), indent=2))
    else:
        _print_autopilot(result)
    sys.exit(_exit_code(result))


def _print_autopilot(result) -> None:
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


def _result_to_dict(result) -> dict[str, object]:
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


def _exit_code(result) -> int:
    if any(f.severity in ("error", "warning") for f in result.findings):
        return 1
    return 0


# ---------------------------------------------------------------------------
# --dump implementation (unchanged behavior)
# ---------------------------------------------------------------------------


def _run_dump(*, cache_dir: Path | None, as_json: bool) -> None:
    """Dump every cached coverage verdict for human inspection."""
    cache = _open_cache(cache_dir)
    try:
        entries = cache.iter_entries()
    finally:
        cache.close()

    if as_json:
        click.echo(json.dumps([_entry_to_dict(e) for e in entries], indent=2))
        return

    if not entries:
        click.echo(
            f"(no cached verdicts in {resolve_cache_dir(cli_override=cache_dir) / CACHE_DB_NAME})"
        )
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
