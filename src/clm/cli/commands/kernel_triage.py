"""Re-test kernel workarounds and known-flaky decks (issue #330).

``clm kernel-triage`` answers the question every xeus-cpp / CppInterOp
image bump raises: *which of our crash workarounds are still needed?*
It collects two candidate sets,

* **workarounds** — every topic the spec marks ``evaluate="no"`` (decks
  whose kernel crashes were deterministic enough to disable evaluation),
* **known-flaky decks** — every deck the execution-telemetry database has
  recorded a flake or failure for within the lookback window,

then re-executes them against the current kernel and reports which
``evaluate="no"`` attributes can be lifted and which decks still flake.

The re-execution is a real ``clm build`` run in a subprocess (so Docker
worker images, payload construction, and retry behavior are exactly the
production paths) against a *generated triage spec*: a copy of the course
spec with non-target topics removed, ``evaluate="no"`` stripped from the
targets, and ``<output-targets>`` dropped. The build uses throwaway
cache/jobs databases (every deck executes exactly once per language —
within one build, kinds share the execution cache) and a throwaway output
directory, while telemetry is pointed at the REAL telemetry database so
triage runs extend the crash history.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import click

logger = logging.getLogger(__name__)

# Outcome → recommendation, keyed by (deck is an evaluate="no" workaround).
_RECOMMENDATIONS = {
    ("passed", True): 'workaround can be lifted — remove evaluate="no" from the topic',
    ("flaky", True): 'keep evaluate="no" (passed only after retry against the current kernel)',
    ("failed", True): 'keep evaluate="no" (still failing against the current kernel)',
    (
        "suppressed_failure",
        True,
    ): 'keep evaluate="no" (cells still fail; skip-errors absorbed them)',
    ("passed", False): "no flake in this run — keep watching",
    ("flaky", False): "still flaky against the current kernel",
    ("failed", False): "regressed to a hard failure — investigate before the next build",
    ("suppressed_failure", False): "cells failed but were absorbed by skip-errors",
}


@dataclass
class TriageDeck:
    """One deck selected for triage."""

    path: str  # absolute source path
    topic_id: str
    is_workaround: bool  # True: evaluate="no" topic; False: flaky history
    history: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = ""  # set by the rerun: passed | flaky | failed | suppressed_failure
    details: str = ""
    recommendation: str = ""


def _norm(path: str | Path) -> str:
    """Normalize a path for cross-source comparison (case folds on Windows)."""
    return os.path.normcase(str(Path(path).absolute()))


def _load_course(spec_file: Path, data_dir: Path | None):
    """Load the course read-only, mirroring ``clm cache explain``."""
    from clm.core.course import Course
    from clm.core.course_paths import resolve_course_paths
    from clm.core.course_spec import CourseSpec, CourseSpecError

    course_root, default_output = resolve_course_paths(spec_file.absolute(), data_dir)
    try:
        spec = CourseSpec.from_file(spec_file.absolute())
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse course spec: {e}") from None
    effective_output = None if spec.output_targets else default_output
    course = Course.from_spec(spec, course_root, effective_output)
    return course


def _event_to_dict(event) -> dict[str, Any]:
    return {
        "created_at": event.created_at,
        "outcome": event.outcome,
        "classification": event.classification,
        "attempts": event.attempts,
        "failure_type": event.failure_type,
        "failing_cell_index": event.failing_cell_index,
        "language": event.language,
    }


def _collect_targets(course, telemetry_store, since: str) -> tuple[list[TriageDeck], list[str]]:
    """Resolve the triage candidates from the spec and the telemetry history.

    Returns ``(decks, stale_telemetry_paths)``; the latter are telemetry
    entries whose source file is no longer part of the course (renamed or
    removed decks) — reported, never re-executed.
    """
    from clm.core.course_files.notebook_file import NotebookFile

    decks: dict[str, TriageDeck] = {}
    for course_file in course.files:
        if isinstance(course_file, NotebookFile) and course_file.skip_evaluation:
            key = _norm(course_file.path)
            decks[key] = TriageDeck(
                path=str(course_file.path),
                topic_id=course_file.topic.id,
                is_workaround=True,
            )

    stale: list[str] = []
    for input_file, events in telemetry_store.problem_files(since=since).items():
        history = [_event_to_dict(e) for e in events]
        key = _norm(input_file)
        if key in decks:
            decks[key].history = history
            continue
        course_file = course.find_course_file(Path(input_file))
        if course_file is None or not isinstance(course_file, NotebookFile):
            stale.append(input_file)
            continue
        decks[key] = TriageDeck(
            path=str(course_file.path),
            topic_id=course_file.topic.id,
            is_workaround=course_file.skip_evaluation,
            history=history,
        )

    ordered = sorted(decks.values(), key=lambda d: (not d.is_workaround, d.path))
    return ordered, sorted(stale)


def _topic_element_id(topic_elem: ET.Element) -> str:
    """A ``<topic>``'s id: the ``id=`` attribute or the text-content form."""
    attr_id = topic_elem.attrib.get("id", "").strip()
    if attr_id:
        return attr_id
    return (topic_elem.text or "").strip()


def write_triage_spec(spec_file: Path, target_topic_ids: set[str], out_file: Path) -> None:
    """Write a triage copy of ``spec_file`` restricted to the target topics.

    * non-target ``<topic>`` elements are removed,
    * ``evaluate`` attributes are stripped from the targets (that is the
      point of the exercise: execute them again),
    * ``<output-targets>`` is dropped (one output tree is enough; per-target
      fan-out — including JupyterLite sites — adds no execution coverage),
    * sections/subsections left without any ``<topic>`` are disabled.

    The copy is written NEXT TO the original spec so every relative path
    (modules, includes, dir-groups) resolves unchanged.
    """
    tree = ET.parse(spec_file)
    root = tree.getroot()
    parent_map = {child: parent for parent in root.iter() for child in parent}

    for topic_elem in list(root.iter("topic")):
        if _topic_element_id(topic_elem) in target_topic_ids:
            topic_elem.attrib.pop("evaluate", None)
        else:
            parent_map[topic_elem].remove(topic_elem)

    for targets_elem in list(root.iter("output-targets")):
        parent_map[targets_elem].remove(targets_elem)

    for subsection in root.iter("subsection"):
        if subsection.find(".//topic") is None:
            subsection.set("enabled", "false")
    for section in root.iter("section"):
        if section.find(".//topic") is None:
            section.set("enabled", "false")

    tree.write(out_file, encoding="utf-8", xml_declaration=True)


def _extract_build_json(stdout: str) -> dict[str, Any] | None:
    """Locate the build-summary JSON object in ``--output-mode json`` stdout."""
    decoder = json.JSONDecoder()
    idx = stdout.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(stdout[idx:])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and "status" in obj:
            return obj
        idx = stdout.find("{", idx + 1)
    return None


def _run_triage_build(
    triage_spec: Path,
    build_dir: Path,
    telemetry_db: Path,
    *,
    data_dir: Path | None,
    workers: str | None,
    notebook_workers: int | None,
    max_workers: int | None,
    notebook_image: str | None,
    timeout: float,
) -> tuple[dict[str, Any] | None, int, str]:
    """Run ``clm build`` on the triage spec; return (summary_json, rc, output)."""
    cmd = [
        sys.executable,
        "-m",
        "clm",
        "--cache-db-path",
        str(build_dir / "cache.db"),
        "--jobs-db-path",
        str(build_dir / "jobs.db"),
        "--telemetry-db-path",
        str(telemetry_db),
        "build",
        str(triage_spec),
        "--output-dir",
        str(build_dir / "output"),
        "--output-mode",
        "json",
    ]
    if data_dir is not None:
        cmd += ["--data-dir", str(data_dir)]
    if workers is not None:
        cmd += ["--workers", workers]
    if notebook_workers is not None:
        cmd += ["--notebook-workers", str(notebook_workers)]
    if max_workers is not None:
        cmd += ["--max-workers", str(max_workers)]
    if notebook_image is not None:
        cmd += ["--notebook-image", notebook_image]

    logger.info("Running triage build: %s", " ".join(cmd))
    try:
        proc = subprocess.run(  # noqa: S603 — argv built from our own CLI inputs
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        logger.error("Triage build timed out after %.0fs", timeout)
        return None, -1, stdout + "\n" + stderr
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return _extract_build_json(proc.stdout or ""), proc.returncode, combined


def _classify_rerun_outcomes(
    decks: list[TriageDeck],
    summary_json: dict[str, Any] | None,
    telemetry_store,
    run_started: str,
) -> None:
    """Fill ``outcome``/``details``/``recommendation`` on each triage deck."""
    flaky_by_path: dict[str, dict[str, Any]] = {}
    errors_by_path: dict[str, list[dict[str, Any]]] = {}
    if summary_json is not None:
        for entry in summary_json.get("flaky_files", []):
            flaky_by_path[_norm(entry.get("file_path", ""))] = entry
        for entry in summary_json.get("errors", []):
            errors_by_path.setdefault(_norm(entry.get("file_path", "")), []).append(entry)

    events_by_path: dict[str, list] = {}
    for event in telemetry_store.events(since=run_started):
        events_by_path.setdefault(_norm(event.input_file), []).append(event)

    for deck in decks:
        key = _norm(deck.path)
        run_events = events_by_path.get(key, [])
        if key in errors_by_path:
            deck.outcome = "failed"
            messages = [e.get("message", "").split("\n")[0][:160] for e in errors_by_path[key]]
            deck.details = "; ".join(m for m in messages if m)
        elif any(e.outcome == "suppressed_failure" for e in run_events):
            deck.outcome = "suppressed_failure"
        elif key in flaky_by_path:
            entry = flaky_by_path[key]
            deck.outcome = "flaky"
            deck.details = (
                f"attempts: {entry.get('max_attempts')}, "
                f"{', '.join(entry.get('failure_types') or []) or 'unknown failure type'}"
            )
        elif summary_json is not None:
            deck.outcome = "passed"
        else:
            deck.outcome = "unknown"
            deck.details = "triage build produced no machine-readable summary"

        # Enrich failures with the structured telemetry from this run.
        if deck.outcome == "failed" and run_events:
            last = run_events[0]
            cell = (
                f" at cell {last.failing_cell_index}" if last.failing_cell_index is not None else ""
            )
            deck.details = (
                f"{last.classification} {last.failure_type or 'failure'}{cell} "
                f"after {last.attempts} attempts" + (f" — {deck.details}" if deck.details else "")
            )

        deck.recommendation = _RECOMMENDATIONS.get(
            (deck.outcome, deck.is_workaround),
            "no recommendation (triage build did not complete)",
        )


def _print_history(deck: TriageDeck) -> None:
    for event in deck.history[:3]:
        cell = (
            f" cell {event['failing_cell_index']}"
            if event.get("failing_cell_index") is not None
            else ""
        )
        click.echo(
            f"      {event['created_at']}  {event['outcome']} "
            f"({event['classification']}, {event['failure_type'] or 'n/a'}{cell}, "
            f"attempts {event['attempts']}, {event['language'] or '?'})"
        )
    if len(deck.history) > 3:
        click.echo(f"      ... and {len(deck.history) - 3} earlier event(s)")


def _print_stale(stale: list[str]) -> None:
    if not stale:
        return
    click.echo(f"\nstale telemetry entries not in this course ({len(stale)}):")
    for path in stale:
        click.echo(f"  - {path}")


def _print_report(
    decks: list[TriageDeck],
    stale: list[str],
    *,
    reran: bool,
    build_status: str | None,
) -> None:
    outcome_marks = {
        "passed": "+",
        "flaky": "~",
        "failed": "x",
        "suppressed_failure": "~",
        "unknown": "?",
    }
    workarounds = [d for d in decks if d.is_workaround]
    flaky = [d for d in decks if not d.is_workaround]

    click.echo(f'evaluate="no" workarounds ({len(workarounds)}):')
    for deck in workarounds:
        if reran:
            mark = outcome_marks.get(deck.outcome, "?")
            click.echo(f"  {mark} {deck.path} [{deck.topic_id}]: {deck.outcome}")
            if deck.details:
                click.echo(f"      {deck.details}")
            click.echo(f"      -> {deck.recommendation}")
        else:
            click.echo(f"  - {deck.path} [{deck.topic_id}]")
            _print_history(deck)
    if not workarounds:
        click.echo("  (none)")

    click.echo(f"\nknown-flaky decks from telemetry ({len(flaky)}):")
    for deck in flaky:
        if reran:
            mark = outcome_marks.get(deck.outcome, "?")
            click.echo(f"  {mark} {deck.path} [{deck.topic_id}]: {deck.outcome}")
            if deck.details:
                click.echo(f"      {deck.details}")
            click.echo(f"      -> {deck.recommendation}")
        else:
            click.echo(f"  - {deck.path} [{deck.topic_id}]")
            _print_history(deck)
    if not flaky:
        click.echo("  (none)")

    _print_stale(stale)

    if reran and build_status is not None:
        click.echo(f"\ntriage build status: {build_status}")


@click.command("kernel-triage")
@click.argument(
    "spec-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory. Default: inferred from the spec location.",
)
@click.option(
    "--report-only",
    is_flag=True,
    help=(
        "Only list the triage candidates and their telemetry history; do not re-execute anything."
    ),
)
@click.option(
    "--since-days",
    type=int,
    default=90,
    show_default=True,
    help="Lookback window for known-flaky decks from the telemetry database.",
)
@click.option(
    "--workers",
    type=click.Choice(["direct", "docker"], case_sensitive=False),
    help="Worker execution mode for the triage build (overrides config).",
)
@click.option(
    "--notebook-workers",
    type=int,
    help="Number of notebook workers for the triage build.",
)
@click.option("--max-workers", type=int, help="Hard cap on effective worker count per type.")
@click.option(
    "--notebook-image",
    type=str,
    help="Docker image for notebook workers (the kernel under test).",
)
@click.option(
    "--build-timeout",
    type=float,
    default=3600.0,
    show_default=True,
    help="Timeout (seconds) for the triage build subprocess.",
)
@click.option(
    "--keep-build-dir",
    is_flag=True,
    help="Keep the throwaway build directory (output, cache/jobs dbs) for inspection.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def kernel_triage_cmd(
    ctx,
    spec_file: Path,
    data_dir: Path | None,
    report_only: bool,
    since_days: int,
    workers: str | None,
    notebook_workers: int | None,
    max_workers: int | None,
    notebook_image: str | None,
    build_timeout: float,
    keep_build_dir: bool,
    as_json: bool,
):
    """Re-test kernel-crash workarounds and known-flaky decks.

    Collects every topic the spec disables evaluation for
    (``evaluate="no"``) plus every deck with a recorded kernel flake or
    crash in the telemetry database, re-executes them against the current
    kernel via a real ``clm build`` (throwaway output/cache, telemetry
    recorded to the real database), and reports which workarounds can be
    lifted. Run it after every xeus-cpp/CppInterOp image bump.

    \b
    Examples:
        clm kernel-triage cpp-course.xml
        clm kernel-triage cpp-course.xml --workers docker --notebook-image full
        clm kernel-triage cpp-course.xml --report-only
        clm kernel-triage cpp-course.xml --json
    """
    from clm.infrastructure.database.execution_telemetry import (
        ExecutionTelemetryStore,
        default_telemetry_db_path,
    )

    telemetry_db: Path = (ctx.obj or {}).get(
        "TELEMETRY_DB_PATH", default_telemetry_db_path(Path("clm_cache.db"))
    )
    telemetry_store = ExecutionTelemetryStore(telemetry_db)

    spec_file = spec_file.absolute()
    course = _load_course(spec_file, data_dir)

    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%S")
    decks, stale = _collect_targets(course, telemetry_store, since)

    result: dict[str, Any] = {
        "spec": str(spec_file),
        "telemetry_db": str(telemetry_db),
        "mode": "report-only" if report_only else "rerun",
        "stale_telemetry": stale,
    }

    if not decks:
        result["decks"] = []
        if as_json:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                'Nothing to triage: no evaluate="no" topics in the spec and no '
                f"flaky decks recorded in {telemetry_db} within {since_days} days."
            )
            _print_stale(stale)
        return

    build_status: str | None = None
    if not report_only:
        target_topic_ids = {deck.topic_id for deck in decks}
        run_started = (datetime.now(timezone.utc) - timedelta(seconds=2)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        build_dir = Path(tempfile.mkdtemp(prefix="clm-kernel-triage-"))
        triage_spec = spec_file.parent / f".clm-triage-{os.getpid()}{spec_file.suffix}"
        try:
            write_triage_spec(spec_file, target_topic_ids, triage_spec)
            if not as_json:
                click.echo(
                    f"Re-executing {len(decks)} deck(s) from "
                    f"{len(target_topic_ids)} topic(s) against the current kernel..."
                )
            summary_json, returncode, output = _run_triage_build(
                triage_spec,
                build_dir,
                telemetry_db,
                data_dir=data_dir,
                workers=workers,
                notebook_workers=notebook_workers,
                max_workers=max_workers,
                notebook_image=notebook_image,
                timeout=build_timeout,
            )
            if summary_json is None:
                logger.error(
                    "Triage build (exit %s) produced no JSON summary. Output:\n%s",
                    returncode,
                    output[-4000:],
                )
            build_status = summary_json.get("status") if summary_json else "no-summary"
            _classify_rerun_outcomes(decks, summary_json, telemetry_store, run_started)
        finally:
            triage_spec.unlink(missing_ok=True)
            if keep_build_dir:
                click.echo(f"Triage build directory kept: {build_dir}", err=True)
            else:
                shutil.rmtree(build_dir, ignore_errors=True)

    result["build_status"] = build_status
    result["decks"] = [
        {
            "path": deck.path,
            "topic_id": deck.topic_id,
            "is_workaround": deck.is_workaround,
            "history": deck.history,
            "outcome": deck.outcome,
            "details": deck.details,
            "recommendation": deck.recommendation,
        }
        for deck in decks
    ]

    if as_json:
        click.echo(json.dumps(result, indent=2))
        return

    _print_report(decks, stale, reran=not report_only, build_status=build_status)
