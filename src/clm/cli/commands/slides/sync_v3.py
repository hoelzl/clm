"""The engine facade for the sync verbs (#520; sole engine since Phase 4).

``clm.cli.commands.slides.sync`` hands each verb to a runner here. This
module drives only the document-model core (``doc_lenses`` / ``sync_diff`` /
``doc_ledger`` / ``doc_apply``); the structural verify gate on the write
paths (``sync_verify``) is loaded lazily inside the functions that need it.

Verbs (design §8):

* ``report`` — read-only, ledger-trusted; schema-5 envelope with the stable
  ``is_clean`` / ``needs_model`` / ``needs_agent`` booleans; framed items
  carry their decision vocabulary so an agent can answer in one document,
  and recovered translation rows carry base diffs (#773).
* ``apply``  — per-item: every mechanical row plus validated decisions; the
  ledger records each landed item; exit 0 all-applied / 1 residue / 2 error.
* ``record`` — bless/accept collapsed: gated on the structural verify, then
  the deck's current state is recorded wholesale (or per ``--member``),
  performing the §7.3 pos→id key migration at record time (logged).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.slides import doc_apply, doc_ledger
from clm.slides.base_recovery import (
    BASE_DIFF_ACTIONS,
    MemberBaseDiff,
    batch_observation,
    recover_base_diffs,
)
from clm.slides.doc_lenses import DocLensError, LoadedBundle, load_bundle
from clm.slides.doc_report import (
    cold_sweep_hint,
    diff_bundle,
    diff_bundle_at_ref,
    diff_bundle_with_ledger,
    pair_payload,
    report_id_for,
)
from clm.slides.pairing import (
    find_split_slide_files_recursive,
    iter_split_pairs,
)
from clm.slides.sync_diff import DeckDiff
from clm.slides.sync_wire import REQUIRE_REPORT_ID, WIRE_SCHEMA

__all__ = ["run_apply_v3", "run_record_v3", "run_report_v3"]


def _echo_json(payload: dict) -> None:
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _scope_pairs(
    de_path: Path, en_path: Path | None
) -> tuple[list[tuple[Path, Path | None]], list[Path]]:
    """DECK|DIR scope → the bundles to visit plus any unpaired solo halves.

    A solo half (its twin deleted or misnamed) is total divergence — it must
    never vanish silently from a sweep (the v2 engines warn per solo, and so
    do the v3 runners).
    """
    if de_path.is_dir():
        pairs, solos = iter_split_pairs(find_split_slide_files_recursive(de_path))
        return [(de, en) for de, en in pairs], list(solos)
    return [(de_path, en_path)], []


def _warn_solos(solos: list[Path]) -> None:
    for solo in solos:
        click.echo(f"warning: skipping {solo.name} — no twin half found", err=True)


def _load(de_path: Path, en_path: Path | None) -> LoadedBundle:
    return load_bundle(de_path, en_path)


def _render_pair(
    bundle: LoadedBundle,
    diff: DeckDiff,
    base_diffs: dict[str, MemberBaseDiff] | None = None,
    *,
    batch: bool = True,
) -> str:
    lines = [
        f"{bundle.de_path.name}: "
        + (
            f"clean ({diff.in_sync_count} member(s) in sync)"
            if diff.is_clean
            else f"{len(diff.items)} item(s), {diff.in_sync_count} in sync"
        )
    ]
    if diff.refusal is not None:
        lines.append("  " + diff.refusal.render().replace("\n", "\n  "))
    for item in diff.items:
        answers = doc_apply.item_answers(item)
        suffix = f"  [answers: {', '.join(answers)}]" if answers else ""
        lines.append(
            f"  {item.outcome}/{item.action} {item.key} ({item.direction}) {item.detail}{suffix}"
        )
        # #773: the recovered base renders inline, not behind a flag — reading
        # two full cells to spot a one-word change is the measured cost, and a
        # hidden diff would not collapse it. A side at base ("") prints
        # nothing; an unrecovered row prints exactly what it did before. The
        # action guard mirrors item_payloads: keys are shared across a
        # member's aspect rows (a mechanical mirror_tags beside the
        # verify_translation), and the recovery is a claim about the
        # recovered actions only — without it the hunks render duplicated
        # under rows they do not describe.
        recovered = base_diffs.get(item.key) if base_diffs else None
        if recovered is not None and item.action in BASE_DIFF_ACTIONS:
            for lang in ("de", "en"):
                hunks = recovered.side_diff(lang)
                if hunks:
                    lines.append(f"    {lang} vs base {recovered.base_ref[:12]}:")
                    lines.extend(f"      {hunk_line}" for hunk_line in hunks.splitlines())
    for obs in diff.observations:
        # Two kinds are worth a line in the human report, for opposite reasons:
        # ``group_order_divergence`` suppresses is_clean (issue #654), so without
        # a line an observation-only unclean report reads "0 item(s)" with no
        # visible cause; ``uniform_drift_side`` collapses a wall of per-item
        # translate_edit rows into the one reading that answers them together
        # (Q5) — printing it after the items is deliberate, it is a summary.
        if obs.kind in ("group_order_divergence", "uniform_drift_side"):
            lines.append(f"  observation/{obs.kind}: {obs.detail}")
    if base_diffs and batch:
        # The #773 batch summary — like uniform_drift_side, a summary prints
        # after what it summarizes.
        batch_obs = batch_observation(diff, base_diffs)
        if batch_obs is not None:
            lines.append(f"  observation/{batch_obs.kind}: {batch_obs.detail}")
    hint = cold_sweep_hint(diff)
    if hint is not None:
        lines.append(f"  hint: {hint}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def run_report_v3(
    de_path: Path,
    en_path: Path | None,
    *,
    as_json: bool,
    since_ref: str | None = None,
) -> int:
    """The v3 read verb. Exit 0 clean / 1 work pending / 2 error.

    ``since_ref`` switches the baseline from the committed ledger to the
    bundle at a git ref — the design-§12.3 forensic *view* ("what changed in
    this window"), never a trust change: the ledger is neither consulted nor
    written, and nothing else about the verb differs.
    """
    results: list[
        tuple[
            LoadedBundle,
            DeckDiff,
            list[str],
            doc_ledger.TopicLedger | None,
            dict[str, MemberBaseDiff],
        ]
    ] = []
    errors: list[str] = []
    pairs, solos = _scope_pairs(de_path, en_path)
    _warn_solos(solos)
    for de, en in pairs:
        try:
            bundle = _load(de, en)
        except DocLensError as exc:
            errors.append(str(exc))
            continue
        ledger: doc_ledger.TopicLedger | None = None
        if since_ref is not None:
            diff, base_refusal = diff_bundle_at_ref(bundle, since_ref)
            # The forensic view diffs against a NAMED commit — recovery must
            # not walk history for a base the caller already spelled out.
            base_diffs = recover_base_diffs(bundle, diff, candidates=[since_ref])
        else:
            (diff, ledger), base_refusal = diff_bundle_with_ledger(bundle), []
            base_diffs = recover_base_diffs(bundle, diff)
        results.append((bundle, diff, base_refusal, ledger, base_diffs))
    clean = all(diff.is_clean for _, diff, _refusal, _l, _bd in results) and not errors
    if as_json:
        payloads = []
        for bundle, diff, base_refusal, ledger, base_diffs in results:
            payload = pair_payload(
                bundle, diff, ledger=ledger, base_diffs=base_diffs, batch=since_ref is None
            )
            if since_ref is not None:
                payload["baseline"] = f"since:{since_ref}"
                if base_refusal:
                    payload["base_refusal"] = base_refusal
            payloads.append(payload)
        if not de_path.is_dir() and len(payloads) == 1 and not errors:
            payloads[0]["exit_code"] = 0 if clean else 1
            _echo_json(payloads[0])
        else:
            _echo_json(
                {
                    "schema": WIRE_SCHEMA,
                    "engine": "v3",
                    "exit_code": 0 if clean else 1,
                    "is_clean": clean,
                    "needs_model": any(d.needs_model for _, d, _r, _l, _bd in results),
                    "needs_agent": any(d.needs_agent for _, d, _r, _l, _bd in results)
                    or bool(errors),
                    "errors": errors,
                    "skipped_solos": [str(p) for p in solos],
                    "pairs": payloads,
                }
            )
    else:
        for bundle, diff, base_refusal, _ledger, base_diffs in results:
            click.echo(_render_pair(bundle, diff, base_diffs, batch=since_ref is None))
            if base_refusal:
                click.echo(
                    f"  note: the bundle at {since_ref} refuses to parse "
                    f"({', '.join(sorted(set(base_refusal)))}) — diffed against no base "
                    "(every member cold)",
                    err=True,
                )
        for error in errors:
            click.echo(f"ERROR: {error}", err=True)
    if errors and not results:
        return 2
    return 0 if clean else 1


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def run_apply_v3(
    de_path: Path,
    en_path: Path | None,
    *,
    decisions_spec: str | None,
    members: tuple[str, ...],
    dry_run: bool,
    as_json: bool,
    allow_diverged_companion: bool = False,
) -> int:
    """The v3 write verb. Exit 0 all-applied / 1 residue / 2 error."""
    if de_path.is_dir():
        raise click.UsageError("apply works on a single deck — run report over the directory")
    try:
        bundle = _load(de_path, en_path)
    except DocLensError as exc:
        raise click.UsageError(str(exc)) from exc

    decisions: dict[str, doc_apply.Decision] = {}
    decision_rows: list[doc_apply.Decision] = []
    if decisions_spec is not None:
        try:
            text = (
                sys.stdin.read()
                if decisions_spec == "-"
                else Path(decisions_spec).read_text(encoding="utf-8")
            )
        except OSError as exc:
            raise click.UsageError(f"cannot read the decision document: {exc}") from exc
        document, decision_errors = doc_apply.load_decision_document(text)
        if not decision_errors:
            decision_errors = _report_id_errors(bundle, document)
        if decision_errors:
            for error in decision_errors:
                click.echo(f"decision error: {error}", err=True)
            if as_json:
                # M14: the refusal paths must not differ in shape. A parse or
                # freshness refusal used to exit 2 with an EMPTY stdout while
                # the apply-refusal path emitted an envelope, so a --json
                # consumer saw "no output" and could not tell a crash from a
                # rejection.
                _echo_json(
                    {
                        "schema": WIRE_SCHEMA,
                        "engine": "v3",
                        "exit_code": 2,
                        "error": "; ".join(decision_errors),
                        "decision_errors": decision_errors,
                        "wrote": False,
                        "items": [],
                    }
                )
            return 2
        decisions = document.decisions
        decision_rows = document.rows

    diff = diff_bundle(bundle)
    if diff.refusal is not None:
        message = diff.refusal.render()
        if as_json:
            _echo_json(
                {
                    "schema": WIRE_SCHEMA,
                    "engine": "v3",
                    "exit_code": 2,
                    "error": message,
                    "wrote": False,
                    "items": [],
                }
            )
        else:
            click.echo(message, err=True)
        return 2
    assert bundle.outcome.deck is not None

    ledger_path = doc_ledger.ledger_path_for(bundle.de_path)
    ledger = doc_ledger.load(ledger_path)
    outcome = doc_apply.apply_deck(
        bundle,
        bundle.outcome.deck,
        diff,
        ledger,
        doc_ledger.deck_key_for(bundle.de_path),
        decisions=decisions,
        decision_rows=decision_rows,
        only_members=set(members) if members else None,
        dry_run=dry_run,
        commit=_head_commit(bundle.de_path),
    )
    verify_violations: list[str] = []
    if outcome.error is None and not dry_run and outcome.ledger_changed:
        # The structural write-gate on the TRUST store (design §5): landed
        # file mutations stay (review them with git), but a pair that fails
        # the structural verify is never recorded as verified — same gate
        # `record` applies, over the same companion-inlined projection
        # `verify` reads (D8). Lazy import: sync_verify still loads v2 modules.
        from clm.slides.sync_verify import gate_projected_pair

        verify_violations = [
            v.message
            for v in gate_projected_pair(
                bundle.de_path,
                bundle.en_path,
                bundle.comment_token,
                allow_diverged_companion=allow_diverged_companion,
            )
        ]
        if not verify_violations:
            doc_ledger.save(ledger, ledger_path)

    rejected = [r for r in outcome.results if r.status == "rejected"]
    exit_code = (
        2
        if outcome.error is not None
        else (0 if outcome.all_applied and not verify_violations else 1)
    )
    if as_json:
        # M14/C7: the rejection block goes to stderr BEFORE the payload. It
        # used to print after, so a consumer merging the two streams got JSON
        # with prose appended — unparseable exactly when something went wrong.
        _echo_rejections(rejected)
        payload = outcome.to_payload()
        payload["exit_code"] = exit_code
        payload["deck_key"] = doc_ledger.deck_key_for(bundle.de_path)
        payload["ledger"] = str(ledger_path)
        payload["ledger_recorded"] = outcome.ledger_changed and not verify_violations
        payload["verify_violations"] = verify_violations
        _echo_json(payload)
        return exit_code

    for result in outcome.results:
        click.echo(f"  {result.status:8s} {result.action} {result.key}  {result.reason}")
    if outcome.error:
        click.echo(f"ERROR: {outcome.error}", err=True)
    elif outcome.wrote:
        names = ", ".join(p.name for p in outcome.written_paths)
        click.echo(f"wrote {names}" + (" (dry run)" if dry_run else ""))
    elif dry_run:
        click.echo("dry run — nothing written")
    for violation in verify_violations:
        click.echo(f"verify: {violation}", err=True)
    if verify_violations:
        click.echo(
            "structural verify failed — applied changes were written but NOT "
            "recorded into the ledger; fix the pair, then `sync record`. If the "
            "divergence is in a voiceover companion and is intentional, "
            "`--allow-diverged-companion` records it anyway (logged)",
            err=True,
        )
    _echo_rejections(rejected)
    return exit_code


def _echo_rejections(rejected: list[doc_apply.ItemResult]) -> None:
    """The per-item rejection block — stderr in both modes.

    Agents parsing ``--json`` counts alone provably committed with rejections
    unnoticed, so this is never silent.
    """
    if not rejected:
        return
    click.echo(
        f"{len(rejected)} decision(s) rejected — each item's accepted answers "
        "come from report --json (its `answers` list); see `clm info sync-agents`:",
        err=True,
    )
    for result in rejected:
        click.echo(f"  {result.key} ({result.action}): {result.reason}", err=True)


def _report_id_errors(bundle: LoadedBundle, document: doc_apply.DecisionDocument) -> list[str]:
    """Refuse a decision document written against a report that has expired.

    Wholesale, before anything is written (Q2): a stale document's *other*
    answers are as suspect as the one that no longer matches, and the old
    per-handle rejection let the first apply's writes stand while telling the
    second one its decisions were stale (#649).

    A document with no token is accepted with a warning — schema 3 predates
    the field, and the drivers that emit those documents are still in flight.
    :data:`~clm.slides.sync_wire.REQUIRE_REPORT_ID` flips that in the release
    that drops schema 3.
    """
    if document.report_id is None:
        message = (
            "decision document carries no `report_id` — copy it from the report "
            "envelope so apply can refuse a document answering a report that no "
            "longer describes this deck"
        )
        if REQUIRE_REPORT_ID:
            return [message]
        click.echo(f"warning: {message} (accepted for now)", err=True)
        return []
    current = report_id_for(bundle)
    if document.report_id == current:
        return []
    return [
        f"decision document was written against report_id {document.report_id!r}, "
        f"but this deck is now {current!r} — the bundle or its ledger section "
        "changed since that report (an edit, a sibling apply, or the companion "
        "spelling of the same deck). Nothing was written: re-run "
        "`clm slides sync report DECK --json` and answer the fresh items"
    ]


def _head_commit(path: Path) -> str | None:
    """Best-effort git provenance for ledger records (never fails a write)."""
    try:
        from clm.core.git_info import get_git_info

        commit = get_git_info(path.parent).get("commit")
        return commit if isinstance(commit, str) else None
    except Exception:  # noqa: BLE001 - provenance must never fail the verb
        return None


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def run_record_v3(
    de_path: Path,
    en_path: Path | None,
    *,
    members: tuple[str, ...],
    provenance: str,
    as_json: bool,
    allow_diverged_companion: bool = False,
    provenance_explicit: bool = False,
) -> int:
    """The v3 trust verb: bless/accept collapsed, gated on structural verify.

    Exit 0 all recorded / 1 some pairs refused / 2 error.

    ``provenance_explicit`` says the user actually typed ``--provenance``. It
    cannot be inferred from the value: ``record`` is both the option's default
    and a value a human types to reset a stale ``semantic:<model>``
    attribution, and the ledger preserves an unchanged member's existing stamp
    unless the new one is deliberate (M13). Without this the reset was silently
    swallowed while the verb still reported the member as recorded.
    """
    if provenance not in ("record", "agent") and not provenance.startswith("semantic:"):
        raise click.UsageError("--provenance must be 'record', 'agent', or 'semantic:<model>'")
    rows: list[dict] = []
    refused = 0
    errors = 0
    pairs, solos = _scope_pairs(de_path, en_path)
    _warn_solos(solos)
    for de, en in pairs:
        row = _record_one(
            de,
            en,
            members=members,
            provenance=provenance,
            allow_diverged_companion=allow_diverged_companion,
            provenance_explicit=provenance_explicit,
        )
        rows.append(row)
        if row.get("error"):
            errors += 1
        elif row.get("refused"):
            refused += 1
        if not as_json:
            _render_record_row(row)
    if as_json:
        _echo_json(
            {
                "schema": WIRE_SCHEMA,
                "engine": "v3",
                "recorded": sum(r.get("recorded", 0) for r in rows),
                "unchanged": sum(
                    1 for r in rows if "recorded" in r and not r.get("ledger_changed", True)
                ),
                "refused": refused,
                "errors": errors,
                "pairs": rows,
            }
        )
    if errors:
        return 2
    return 1 if refused else 0


def _record_one(
    de_path: Path,
    en_path: Path | None,
    *,
    members: tuple[str, ...],
    provenance: str,
    allow_diverged_companion: bool = False,
    provenance_explicit: bool = False,
) -> dict:
    try:
        bundle = _load(de_path, en_path)
    except DocLensError as exc:
        return {"de_path": str(de_path), "error": str(exc)}
    row: dict = {"de_path": str(bundle.de_path), "en_path": str(bundle.en_path)}
    if bundle.outcome.refusal is not None:
        row["refused"] = True
        row["reasons"] = [f"[{r.code}] {r.detail}" for r in bundle.outcome.refusal.reasons]
        return row
    assert bundle.outcome.deck is not None

    # The structural verify gate (design §5/§8): a structurally corrupt pair
    # is never recorded as verified. Runs over the companion-inlined projection
    # `verify` reads, so a divergence hidden in a separated voiceover companion
    # cannot be blessed here while `verify` fails on it (D8 / finding Y2). Lazy
    # import — sync_verify still imports v2 modules, and this module must stay
    # clean of them at import time.
    from clm.slides.sync_verify import gate_projected_pair

    violations = gate_projected_pair(
        bundle.de_path,
        bundle.en_path,
        bundle.comment_token,
        allow_diverged_companion=allow_diverged_companion,
    )
    if violations:
        row["refused"] = True
        row["reasons"] = [v.message for v in violations]
        return row

    ledger_path = doc_ledger.ledger_path_for(bundle.de_path)
    ledger = doc_ledger.load(ledger_path)
    deck_key = doc_ledger.deck_key_for(bundle.de_path)
    recorded, migrations = doc_ledger.record_deck_snapshot(
        ledger,
        deck_key,
        bundle.outcome.deck,
        provenance=provenance,
        commit=_head_commit(bundle.de_path),
        member_keys=set(members) if members else None,
        deliberate_provenance=provenance_explicit,
    )
    changed = doc_ledger.save(ledger, ledger_path)
    row["recorded"] = recorded
    row["ledger"] = str(ledger_path)
    row["ledger_changed"] = changed
    if members:
        deck_ledger = ledger.decks.get(deck_key)
        known = deck_ledger.members.keys() if deck_ledger is not None else set()
        unknown = sorted(k for k in members if k not in known)
        if unknown:
            row["unknown_members"] = unknown
            click.echo(
                f"warning: {bundle.de_path.name}: no such member(s) in the current "
                f"deck: {', '.join(unknown)}",
                err=True,
            )
    if migrations:
        # The §7.3 key migration is an explicit, logged rename.
        row["key_migrations"] = dict(sorted(migrations.items()))
    return row


def _render_record_row(row: dict) -> None:
    name = Path(row["de_path"]).name
    if row.get("error"):
        click.echo(f"{name}: ERROR {row['error']}", err=True)
        return
    if row.get("refused"):
        click.echo(f"{name}: REFUSED")
        for reason in row.get("reasons", []):
            click.echo(f"  - {reason}")
        return
    suffix = "" if row.get("ledger_changed", True) else " (unchanged)"
    click.echo(f"{name}: recorded {row['recorded']} member(s) -> {row['ledger']}{suffix}")
    for old, new in row.get("key_migrations", {}).items():
        click.echo(f"  key migrated {old} -> {new}")
