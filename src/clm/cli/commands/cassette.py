"""``clm cassette`` command group: offline cassette diagnostics and repair.

Currently hosts the ``doctor`` subcommand (issue #125), which detects and
optionally repairs chain-orphan interactions in canonical HTTP-replay
cassettes. See :mod:`clm.workers.notebook.cassette_doctor` for the detection
and repair logic.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

import click
from rich.markup import escape

from clm.cli.commands.shared import cli_console, get_logger
from clm.core.course_paths import resolve_course_paths
from clm.workers.notebook.cassette_doctor import (
    DEFAULT_MIN_TEXT_LEN,
    BaselineError,
    BaselineOutcome,
    CassetteReport,
    SecretScanReport,
    apply_baseline,
    baseline_entries_from_document,
    baseline_root_name,
    build_baseline,
    diagnose_cassettes,
    iter_cassette_paths,
    load_baseline_document,
    scan_cassettes_for_secrets,
)

logger = get_logger(__name__)


@click.group("cassette")
def cassette_group() -> None:
    """Inspect and repair HTTP-replay cassettes."""


def _resolve_walk_root(spec_file: Path | None) -> Path:
    """Resolve the directory tree to walk for cassettes.

    When a spec file is given, cassettes live alongside the source ``.py``
    files under the course root (resolved the same way ``clm build`` does).
    Without a spec, the current working directory is walked — convenient for
    repairing a single topic directory in place.
    """
    if spec_file is None:
        return Path.cwd()
    course_root, _ = resolve_course_paths(spec_file)
    return course_root


def _render_text_report(reports: list[CassetteReport], *, fix: bool) -> None:
    """Print a human-readable per-cassette report to the console."""
    console = cli_console
    total_orphans = 0
    total_fixed = 0
    inspected = 0
    skipped = 0

    for report in reports:
        if report.error is not None:
            skipped += 1
            console.print(
                f"[yellow]! {escape(str(report.path))}[/yellow]: skipped ({escape(report.error)})"
            )
            continue
        inspected += 1
        if not report.has_orphans:
            continue
        total_orphans += len(report.orphans)
        if report.fixed:
            total_fixed += 1
        status = " [green](repaired)[/green]" if report.fixed else ""
        console.print(
            f"[bold]{escape(str(report.path))}[/bold]: "
            f"{len(report.orphans)} chain-orphan(s) "
            f"of {report.interaction_count} interaction(s){status}"
        )
        for orphan in report.orphans:
            # The excerpt is *recorded LLM output*, so it routinely contains
            # square brackets — `[/INST]` is Mistral's and Llama's instruct
            # delimiter, and an ordinary markdown link is `[text](url)`.
            # Unescaped, the first raised ``MarkupError`` as a traceback out
            # of the CLI and the second silently ate the link text.
            console.print(
                f"    [[{orphan.index}]] {escape(orphan.method)} {escape(orphan.uri)}\n"
                f"        request-body: {escape(orphan.request_fingerprint)}\n"
                f"        response ({orphan.text_len} chars): "
                f"{escape(repr(orphan.text_excerpt))}"
            )

    console.print()
    console.print(
        f"Cassettes inspected: {inspected}" + (f"  (skipped {skipped})" if skipped else "")
    )
    console.print(f"Chain-orphans found: {total_orphans}")
    if fix:
        console.print(f"Cassettes repaired:  {total_fixed}")
    elif total_orphans:
        console.print(
            "Re-run with [bold]--fix[/bold] to remove orphan interactions "
            "so the next build re-records them."
        )


@cassette_group.command("doctor")
@click.argument(
    "spec-file",
    required=False,
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--fix",
    is_flag=True,
    default=False,
    help=(
        "Rewrite cassettes to drop chain-orphan interactions so the next "
        "build re-records them. Default off (diagnostic only)."
    ),
)
@click.option(
    "--min-text-len",
    type=click.IntRange(min=1),
    default=DEFAULT_MIN_TEXT_LEN,
    show_default=True,
    help=(
        "Minimum extracted response-content length (chars) for an "
        "interaction to be treated as a chain-edge candidate. Shorter "
        "responses are too likely to appear incidentally in unrelated "
        "request bodies to flag reliably."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a machine-readable JSON report on stdout instead of text.",
)
def doctor(spec_file: Path | None, fix: bool, min_text_len: int, as_json: bool) -> None:
    """Detect (and optionally repair) orphan chain-pointing cassette interactions.

    Walks every ``*.http-cassette.yaml`` under the spec's source tree (or the
    current directory when SPEC-FILE is omitted). For each interaction, the
    chat-completion text content is extracted and treated as a chain-edge
    candidate when at least ``--min-text-len`` characters long. If no other
    interaction's request body embeds that text, the interaction is flagged
    as a chain-orphan — almost always a chain-opener whose closer was never
    recorded (the canonical-poisoning case from issue #115 that the
    completion-marker fix cannot retroactively repair).

    \b
    Examples:
        clm cassette doctor course.xml                 # report orphans
        clm cassette doctor course.xml --fix           # remove them
        clm cassette doctor course.xml --json          # machine-readable
        clm cassette doctor course.xml --min-text-len 80
        clm cassette doctor                            # walk current dir
    """
    root = _resolve_walk_root(spec_file)
    paths = list(iter_cassette_paths(root))

    reports = diagnose_cassettes(paths, min_text_len=min_text_len, fix=fix)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "root": str(root),
                    "min_text_len": min_text_len,
                    "fix": fix,
                    "cassette_count": len(reports),
                    "orphan_count": sum(len(r.orphans) for r in reports),
                    "cassettes": [r.to_dict() for r in reports],
                },
                indent=2,
            )
        )
        return

    if not paths:
        cli_console.print(f"No cassettes found under {escape(str(root))}.")
        return

    _render_text_report(reports, fix=fix)


def root_name_warning(recorded: str | None, actual: str) -> str | None:
    """The "this baseline was written for another root" line, or ``None``.

    Pure and public for the same reason :func:`secret_report_summary` is: the
    Rich console binds to the real stderr at import time and is invisible to
    ``CliRunner``, so a warning printed inline is a warning nothing can
    assert — and three separate mutations of this rule survived the whole
    suite (review round 2).

    Both names are Rich-escaped. A directory called ``PythonCourses[old]``
    is perfectly legal and made the message print the *wrong* name, silently,
    in the one place whose job is telling two roots apart; a name with an
    unbalanced closing tag raised ``MarkupError`` out of the CLI.
    """
    if not recorded or recorded == actual:
        return None
    return (
        f"[yellow]Baseline was written for a root named '{escape(recorded)}', "
        f"scanning '{escape(actual)}'[/yellow] — entries are relative to the root, "
        "so check this is the tree you meant."
    )


def secret_report_summary(
    reports: list[SecretScanReport], baselined: bool = False
) -> tuple[int, int, int, int]:
    """``(cassettes with findings, findings, gating findings, unreadable)``.

    Pure, so the sentence the report ends on can be tested — the Rich console
    binds to the real stderr at import time and is invisible to ``CliRunner``,
    which left the whole summary unasserted and let it say "0 findings" on a
    run that had just listed one (found in review).
    """
    dirty = sum(1 for r in reports if r.error is None and r.findings)
    total = sum(len(r.findings) for r in reports if r.error is None)
    gating = sum(
        1 for r in reports if r.error is None for f in r.findings if not (baselined and f.accepted)
    )
    skipped = sum(1 for r in reports if r.error is not None)
    return dirty, total, gating, skipped


def _render_secret_report(
    reports: list[SecretScanReport], outcome: BaselineOutcome | None = None
) -> None:
    """Print the audit.

    Without a baseline every finding gates. With one, accepted findings are
    still listed — a repo should be able to see what it has accepted — but
    marked, and only the rest count.

    Every interpolated value is Rich-escaped: paths, header names and URIs
    all come from the filesystem or from a cassette, i.e. from outside, and
    a stray ``[/x]`` in one of them would raise ``MarkupError`` out of a CI
    gate (or worse, quietly restyle the report).
    """
    console = cli_console
    dirty, total, gating_total, skipped = secret_report_summary(reports, outcome is not None)

    for report in reports:
        if report.error is not None:
            console.print(
                f"[yellow]! {escape(str(report.path))}[/yellow]: skipped ({escape(report.error)})"
            )
            continue
        if not report.findings:
            continue
        gating = [f for f in report.findings if not (outcome and f.accepted)]
        colour = "red" if gating else "dim"
        console.print(
            f"[{colour}]{'x' if gating else '-'} {escape(str(report.path))}[/{colour}]: "
            f"{len(report.findings)} finding(s)"
        )
        for finding in report.findings:
            mark = " [dim](accepted)[/dim]" if outcome and finding.accepted else ""
            console.print(
                f"    interaction {finding.index}: {escape(finding.location)} "
                f"'{escape(finding.key)}'  {escape(finding.uri)}{mark}"
            )

    # Say what is true. Recomputing "with secrets" to mean "with *gating*
    # secrets" made the summary read "0 with secrets (0 finding(s))"
    # immediately below a listed finding — the repo demonstrably has one.
    console.print(
        f"\n{len(reports)} cassette(s) scanned, {dirty} with findings "
        f"({total} finding(s)), {skipped} unreadable."
    )
    if outcome is not None:
        console.print(
            f"{len(outcome.accepted)} finding(s) accepted by the baseline, {len(outcome.new)} new."
        )
        if outcome.stale_cleared:
            # Not a failure, and the wording matters: these are decks that
            # were re-recorded, i.e. somebody doing the thing the audit asks
            # for. Failing here would make the gate punish the fix.
            console.print(
                f"[dim]{len(outcome.stale_cleared)} baseline entr(y/ies) are cleared[/dim] "
                "— those decks were re-recorded. Regenerate with --write-baseline:"
            )
            for entry in outcome.stale_cleared:
                console.print(f"    {escape(entry.path)}: {escape(entry.location)}")
        if outcome.stale_unreadable:
            # These matched nothing only because nothing could be read out
            # of the file. Reporting them as "re-recorded" — in the same
            # report that calls the file unreadable — was the report
            # contradicting itself.
            console.print(
                f"[yellow]{len(outcome.stale_unreadable)} baseline entr(y/ies) are in "
                f"cassettes that could not be read[/yellow] — not cleanup; fix the file:"
            )
            for entry in outcome.stale_unreadable:
                console.print(f"    {escape(entry.path)}: {escape(entry.location)}")
        if outcome.stale_missing:
            # A different thing entirely, and it used to be reported with
            # the reassuring "re-record cleanup, most likely" wording above:
            # these files were never scanned. Sparse checkout, content that
            # did not materialise, moved decks, wrong root.
            console.print(
                f"[yellow]{len(outcome.stale_missing)} baseline entr(y/ies) name files that "
                f"were not scanned at all[/yellow] — not re-record cleanup; check the scan root:"
            )
            for entry in outcome.stale_missing:
                console.print(f"    {escape(entry.path)}: {escape(entry.location)}")
    if gating_total:
        console.print(
            "Re-record the affected deck(s) against the live service; the "
            "recorder strips these on the way in now. Nothing was rewritten."
        )


@cassette_group.command("scan")
@click.argument(
    "spec-file",
    required=False,
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a machine-readable JSON report on stdout instead of text.",
)
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Accept the findings recorded in this file; only new ones fail the "
        "exit code. Write it with --write-baseline."
    ),
)
@click.option(
    "--write-baseline",
    "write_baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the current findings to this file as an accepted baseline, then stop.",
)
def scan(
    spec_file: Path | None,
    as_json: bool,
    baseline_path: Path | None,
    write_baseline_path: Path | None,
) -> None:
    """Audit committed cassettes for recorded secrets (read-only).

    Walks every ``*.http-cassette.yaml`` under the spec's source tree (or
    the current directory when SPEC-FILE is omitted) and reports any value
    the recorder would strip today: secret request headers and query
    parameters, secret request-body parameters (JSON or form-encoded),
    ``Set-Cookie`` response headers, and OAuth-shaped keys in JSON response
    bodies. Cassettes recorded before the response-side filter existed
    predate all of that, so this is how you find the ones worth
    re-recording.

    Every finding is one that re-recording the deck actually clears —
    the audit asks "would the recorder change this file today?", not "is
    this file free of every secret". Exits non-zero when anything is
    found, or when a cassette could not be read at all (an unreadable
    file is not evidence of cleanliness), so it can gate a repo audit.
    Never rewrites: the fix is to re-record the deck against the live
    service (``clm cassette doctor --fix`` is a different, narrower
    repair).

    ``--baseline`` makes the scan usable as a repo gate. A repo whose
    existing findings are all known and benign — course repos hold
    hundreds of non-credential response cookies — can never turn a bare
    scan green, and an unsatisfiable gate gets switched off. Write the
    current state with ``--write-baseline``, commit it, and from then on
    only a *new* finding fails. Accepted findings are still reported.

    \b
    Examples:
        clm cassette scan course.xml            # audit a course repo
        clm cassette scan course.xml --json     # machine-readable
        clm cassette scan                       # walk current dir
        clm cassette scan --write-baseline .clm-cassette-baseline.json
        clm cassette scan --baseline .clm-cassette-baseline.json   # CI gate
    """
    if baseline_path and write_baseline_path:
        raise click.UsageError(
            "--baseline and --write-baseline are mutually exclusive: pass "
            "--write-baseline alone to regenerate the file."
        )

    root = _resolve_walk_root(spec_file)
    paths = list(iter_cassette_paths(root))
    reports = scan_cassettes_for_secrets(paths)
    finding_count = sum(len(r.findings) for r in reports)
    unreadable = sum(1 for r in reports if r.error is not None)

    if write_baseline_path is not None:
        entry_count = _write_baseline(write_baseline_path, reports, root)
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "root": str(root),
                        "baseline": str(write_baseline_path),
                        "entry_count": entry_count,
                        "cassette_count": len(reports),
                        "finding_count": finding_count,
                        "unreadable_count": unreadable,
                    },
                    indent=2,
                )
            )
        else:
            cli_console.print(
                f"Wrote {entry_count} baseline entr(y/ies) for {finding_count} finding(s) "
                f"to {escape(str(write_baseline_path))}."
            )
        # An unreadable cassette is not baselineable, so a later
        # ``--baseline`` run would still fail on it. Exiting zero here
        # would promise a green gate this file cannot deliver.
        if unreadable:
            cli_console.print(
                f"[yellow]{unreadable} cassette(s) could not be read[/yellow] and are not in "
                "the baseline — they will keep failing the gate until they parse."
            )
            raise SystemExit(1)
        return

    outcome = None
    if baseline_path is not None:
        try:
            document = load_baseline_document(baseline_path)
            entries = baseline_entries_from_document(document)
        except BaselineError as exc:
            # Never degrade to "accept everything" (a false all-clear) or
            # "accept nothing" (an unsatisfiable gate) — stop and say why.
            raise click.ClickException(str(exc)) from exc
        try:
            outcome = apply_baseline(reports, root, entries)
        except BaselineError as exc:  # a cassette outside the scan root
            raise click.ClickException(str(exc)) from exc

        # A hint, not proof: entries are relative, so applying a baseline at
        # a different root silently re-interprets all of them, and a
        # colliding relative path is then accepted without ever having been
        # baselined. Warned rather than refused because a repo may
        # legitimately check out under a different directory name — and
        # printed *before* any refusal below, because it is the single most
        # useful thing to know when a run is about to fail for a root the
        # operator cannot see.
        warning = root_name_warning(baseline_root_name(document), root.name)
        if warning:
            cli_console.print(warning)

    gating_count = len(outcome.new) if outcome else finding_count

    if as_json:
        payload = {
            "root": str(root),
            "cassette_count": len(reports),
            # Still the total, so a consumer that has always read this
            # keeps reading the same thing. The exit code keys on
            # ``new_count`` once a baseline is in play.
            "finding_count": finding_count,
            # Reported next to the findings so a CI consumer keying on
            # ``finding_count == 0`` cannot read "unreadable" as "clean" —
            # the exit code already treats them as a failure.
            "unreadable_count": unreadable,
            "cassettes": [r.to_dict() for r in reports],
        }
        if outcome is not None:
            payload["baseline"] = str(baseline_path)
            payload["accepted_count"] = len(outcome.accepted)
            payload["new_count"] = len(outcome.new)
            payload["stale_count"] = len(outcome.stale)
            # Split, because the two mean opposite things: "cleared" is a
            # deck that was re-recorded, "missing" is a file this run never
            # saw. A CI consumer wanting to be strict about coverage keys on
            # the second.
            payload["stale_cleared_count"] = len(outcome.stale_cleared)
            payload["stale_unreadable_count"] = len(outcome.stale_unreadable)
            payload["stale_missing_count"] = len(outcome.stale_missing)
            payload["stale_entries"] = [e.to_dict() for e in outcome.stale]
        click.echo(json.dumps(payload, indent=2))
    elif not paths:
        # **No ``return`` here, and it is load-bearing**: an empty tree is a
        # legitimate zero-finding result for a bare scan, but a *baselined*
        # run over one is the wrong-scan-root case, and returning here would
        # skip the refusal below and exit 0 — in text mode only, which is
        # how it hid. Not the exit-code check: that is a no-op for an empty
        # tree either way.
        cli_console.print(f"No cassettes found under {escape(str(root))}.")
        if outcome is not None:
            # Which entries went missing, not just how many. This is the
            # run that is about to be refused for a wrong scan root, so the
            # list of paths it expected is the most useful thing on screen —
            # and a bare "No cassettes found" used to be all of it.
            _render_secret_report(reports, outcome)
    else:
        _render_secret_report(reports, outcome)

    # Only *after* reporting. Failing before it hid any new finding the run
    # had also turned up — and then told the operator to regenerate the
    # baseline, which would have blessed the very secret it never showed
    # them (found in review round 2). Report first, refuse second.
    if outcome is not None and outcome.describes_another_tree:
        raise click.ClickException(
            f"{len(outcome.stale_missing)} of {outcome.entry_count} baseline entr(y/ies) name "
            f"files that were not scanned at all under {root} ({len(paths)} cassette(s) found). "
            "Either this is not the tree the baseline was written for — entries are keyed on "
            "paths *relative* to the scan root — or those decks moved or were deleted."
            + (
                # Deliberately does not say "regenerating is right", even
                # though for a renamed deck it usually is: the whole point
                # of printing the report first is that regenerating accepts
                # whatever is there, and one of these runs will one day have
                # a real secret in it.
                " Read the new findings listed above first — some may be the same decks under "
                "new paths, but regenerating accepts whatever is there, secrets included."
                if outcome.new
                else " Regenerate with --write-baseline once you are sure which."
            )
        )

    # Fails closed on an unreadable cassette too: a file the audit could
    # not parse is not a file it can vouch for, and a repo where every
    # cassette is truncated would otherwise pass the gate green. A
    # baseline cannot excuse one either.
    if gating_count or unreadable:
        raise SystemExit(1)


def _write_baseline(path: Path, reports: list[SecretScanReport], root: Path) -> int:
    """Write the baseline document; return its entry count.

    The file is committed to a course repo, so it must not flap between
    checkouts: LF-only, sorted entries, ``indent=2``. Written through a
    sibling temp file and ``os.replace`` so a crash mid-write cannot leave a
    half-written baseline that the next run reads as a corrupt gate.
    """
    try:
        document = build_baseline(reports, root)
    except BaselineError as exc:
        raise click.ClickException(str(exc)) from exc
    payload = json.dumps(document, indent=2) + "\n"
    tmp = path.parent / f"{path.name}.tmp-{os.getpid()}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise click.ClickException(f"could not write baseline '{path}': {exc}") from exc
    return len(document["entries"])
