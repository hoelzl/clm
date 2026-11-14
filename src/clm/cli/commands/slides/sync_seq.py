"""``clm slides sync report`` / ``apply`` on the sequence core (phase C).

Selected with ``CLM_SYNC_CORE=sequence``; the positional engine
(``sync_v3``) stays the default. Design:
``docs/claude/design/sync-sequence-core.md`` (§4 a pass, §7 the agent
contract). Both verbs run :func:`clm.slides.doc_pass.run_pass`:

* ``report`` — read-only. The rows a pass asks (questions, with their
  answers) and the changes it would make on its own (mechanical rows). The
  JSON envelope is schema 6, ``engine: "sequence"``.
* ``apply`` — the same pass with the decision document's answers. With
  nothing left open it writes both halves at once and records the deck;
  with any question open it writes nothing (decision O2: the whole deck).
  A renderer assertion (§6.4) writes nothing and exits 2.

``record`` and ``verify`` are the same verbs for both engines.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.slides import doc_apply, doc_ledger, doc_pass
from clm.slides.agent_task import EXIT_CLEAN, EXIT_ERROR, EXIT_WORK_PENDING, envelope
from clm.slides.doc_lenses import DocLensError, LoadedBundle, load_bundle
from clm.slides.doc_report import report_id_for
from clm.slides.sync_wire import SEQUENCE_WIRE_SCHEMA

__all__ = ["ENV_VAR", "run_apply_seq", "run_report_seq", "sequence_core_selected"]

ENV_VAR = doc_pass.ENV_VAR
_ENGINE = "sequence"


def sequence_core_selected() -> bool:
    """:func:`clm.slides.doc_pass.selected`, a bad value as a usage error."""
    try:
        return doc_pass.selected()
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


def _echo_json(payload: dict) -> None:
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


_pair_payload = doc_pass.pair_payload


def _render(bundle: LoadedBundle, result: doc_pass.Pass) -> list[str]:
    name = bundle.de_path.name
    if result.refusal is not None:
        return [f"{name}: REFUSED — {result.refusal}"]
    if result.error is not None:
        return [f"{name}: ERROR — {result.error}"]
    if result.is_clean:
        return [f"{name}: clean"]
    questions = [r for r in result.rows if r.is_question]
    changes = [r for r in result.rows if not r.is_question]
    lines = [f"{name}: {len(questions)} question(s), {len(changes)} change(s)"]
    for r in questions:
        how = (
            "manual"
            if r.manual
            else f"answered: {r.answer}"
            if r.answer is not None
            else f"answers: {', '.join(r.answers)}"
        )
        side = f" ({r.side})" if r.side else ""
        lines.append(f"  ask {r.action} {r.key}{side} {r.detail}  [{how}]")
    for r in changes:
        side = f" -> {r.side}" if r.side else ""
        lines.append(f"  do  {r.action} {r.key}{side} {r.detail}")
    return lines


def _pairs(
    de_path: Path, en_path: Path | None
) -> tuple[list[tuple[Path, Path | None]], list[Path]]:
    from clm.cli.commands.slides.sync_v3 import _scope_pairs

    return _scope_pairs(de_path, en_path)


def run_report_seq(de_path: Path, en_path: Path | None, *, as_json: bool) -> int:
    """The read verb on the sequence core. Exit 0 clean / 1 work pending /
    2 error."""
    pairs, solos = _pairs(de_path, en_path)
    for solo in solos:
        click.echo(f"warning: skipping {solo.name} — no twin half found", err=True)
    results: list[tuple[LoadedBundle, doc_ledger.TopicLedger, doc_pass.Pass]] = []
    errors: list[str] = []
    for de, en in pairs:
        try:
            bundle = load_bundle(de, en)
        except DocLensError as exc:
            errors.append(str(exc))
            continue
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(bundle.de_path))
        deck = ledger.decks.get(doc_ledger.deck_key_for(bundle.de_path))
        results.append((bundle, ledger, doc_pass.run_pass(bundle, deck)))
    clean = not errors and all(r.is_clean for _, _, r in results)
    failed = any(r.refusal or r.error for _, _, r in results)
    code = EXIT_ERROR if (errors and not results) else EXIT_CLEAN if clean else EXIT_WORK_PENDING
    if as_json:
        payloads = [_pair_payload(b, lg, r) for b, lg, r in results]
        _echo_json(
            envelope(
                SEQUENCE_WIRE_SCHEMA,
                engine=_ENGINE,
                body={
                    "exit_code": code,
                    "is_clean": clean,
                    "needs_model": any(r.needs_model for _, _, r in results),
                    "needs_agent": any(r.needs_agent for _, _, r in results) or bool(errors),
                    "errors": errors,
                    "skipped_solos": [str(p) for p in solos],
                    "pairs": payloads,
                },
            )
        )
    else:
        for bundle, _ledger, result in results:
            click.echo("\n".join(_render(bundle, result)))
        for error in errors:
            click.echo(f"ERROR: {error}", err=True)
    if failed and code == EXIT_WORK_PENDING and not de_path.is_dir():
        return EXIT_ERROR
    return code


def run_apply_seq(
    de_path: Path,
    en_path: Path | None,
    *,
    decisions_spec: str | None,
    members: tuple[str, ...],
    dry_run: bool,
    as_json: bool,
) -> int:
    """The write verb on the sequence core. Exit 0 the deck is in sync /
    1 questions left open (nothing written) / 2 error."""
    if de_path.is_dir():
        raise click.UsageError("apply works on a single deck — run report over the directory")
    if members:
        raise click.UsageError(
            f"--member is not available with {ENV_VAR}=sequence: a pass settles the whole "
            "deck or nothing (unset the variable for the positional engine)"
        )
    try:
        bundle = load_bundle(de_path, en_path)
    except DocLensError as exc:
        raise click.UsageError(str(exc)) from exc
    ledger_path = doc_ledger.ledger_path_for(bundle.de_path)
    ledger = doc_ledger.load(ledger_path)
    deck_key = doc_ledger.deck_key_for(bundle.de_path)

    rows: list[doc_apply.Decision] = []
    if decisions_spec is not None:
        try:
            text = (
                sys.stdin.read()
                if decisions_spec == "-"
                else Path(decisions_spec).read_text(encoding="utf-8")
            )
        except OSError as exc:
            raise click.UsageError(f"cannot read the decision document: {exc}") from exc
        document, problems = doc_apply.load_decision_document(
            text, accepted=(SEQUENCE_WIRE_SCHEMA,)
        )
        if not problems:
            problems = _freshness(bundle, ledger, document)
        if problems:
            return _refuse(problems, as_json)
        rows = document.rows

    result = doc_pass.run_pass(bundle, ledger.decks.get(deck_key), rows)
    written: list[Path] = []
    recorded = False
    if result.refusal is None and result.error is None and not result.held and not dry_run:
        try:
            written = doc_pass.land(
                bundle, ledger, deck_key, result, commit=_head_commit(bundle.de_path)
            )
        except OSError as exc:
            result.error = f"writing failed: {exc}"
        else:
            recorded = doc_ledger.save(ledger, ledger_path)
    if result.refusal or result.error:
        code = EXIT_ERROR
    elif result.held:
        code = EXIT_WORK_PENDING
    else:
        code = EXIT_CLEAN
    for rejection in result.rejected:
        click.echo(
            f"rejected: {rejection.key}"
            + (f" ({rejection.action})" if rejection.action else "")
            + f": {rejection.reason}",
            err=True,
        )
    if as_json:
        payload = _pair_payload(bundle, ledger, result)
        payload.update(
            {
                "exit_code": code,
                "dry_run": dry_run,
                "wrote": bool(written),
                "written": [str(p) for p in written],
                "ledger_recorded": recorded,
                "rejected": [
                    {"key": r.key, "action": r.action, "reason": r.reason} for r in result.rejected
                ],
            }
        )
        _echo_json(envelope(SEQUENCE_WIRE_SCHEMA, engine=_ENGINE, body=payload))
        return code
    click.echo("\n".join(_render(bundle, result)))
    if result.held:
        click.echo("questions left open — nothing written (answer them, then apply again)")
    elif dry_run and result.rendered is not None:
        changed = ", ".join(sorted(f"{lang}:{part}" for lang, part in result.changed)) or "none"
        click.echo(f"dry run — nothing written (would change: {changed})")
    elif written:
        click.echo("wrote " + ", ".join(p.name for p in written))
    return code


def _freshness(
    bundle: LoadedBundle, ledger: doc_ledger.TopicLedger, document: doc_apply.DecisionDocument
) -> list[str]:
    """The ``report_id`` check: a document answers the report it was made
    from, and ``seq:`` / ``new:`` handles mean nothing against another."""
    if document.report_id is None:
        return [
            "decision document carries no top-level `report_id` — copy the `report_id` "
            "of the deck's `report --json` entry into the document beside `decisions`"
        ]
    current = report_id_for(bundle, ledger)
    if document.report_id == current:
        return []
    return [
        f"decision document was written against report_id {document.report_id!r}, but "
        f"this deck is now {current!r}: its files or its ledger section changed since. "
        "Nothing was written: re-run `clm slides sync report DECK --json`"
    ]


def _refuse(problems: list[str], as_json: bool) -> int:
    for problem in problems:
        click.echo(f"decision error: {problem}", err=True)
    if as_json:
        _echo_json(
            envelope(
                SEQUENCE_WIRE_SCHEMA,
                engine=_ENGINE,
                body={
                    "exit_code": EXIT_ERROR,
                    "error": "; ".join(problems),
                    "decision_errors": problems,
                    "wrote": False,
                    "items": [],
                },
            )
        )
    return EXIT_ERROR


def _head_commit(path: Path) -> str | None:
    from clm.cli.commands.slides.sync_v3 import _head_commit as head

    return head(path)
