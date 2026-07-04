"""The engine facade for the sync verbs (#520; sole engine since Phase 4).

``clm.cli.commands.slides.sync`` hands each verb to a runner here. This
module drives only the document-model core (``doc_lenses`` / ``sync_diff`` /
``doc_ledger`` / ``doc_apply``); the structural verify gate on the write
paths (``sync_verify``) is loaded lazily inside the functions that need it.

Verbs (design §8):

* ``report`` — read-only, ledger-trusted; schema-3 envelope with the stable
  ``is_clean`` / ``needs_model`` / ``needs_agent`` booleans; framed items
  carry their decision vocabulary so an agent can answer in one document.
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
from clm.slides.doc_lenses import DocLensError, LoadedBundle, load_bundle
from clm.slides.doc_report import diff_bundle, diff_bundle_at_ref, pair_payload
from clm.slides.pairing import (
    find_split_slide_files_recursive,
    iter_split_pairs,
)
from clm.slides.sync_diff import DeckDiff

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


def _render_pair(bundle: LoadedBundle, diff: DeckDiff) -> str:
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
        answers = doc_apply.decision_vocabulary(item.action)
        suffix = f"  [answers: {', '.join(answers)}]" if answers else ""
        lines.append(
            f"  {item.outcome}/{item.action} {item.key} ({item.direction}) {item.detail}{suffix}"
        )
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
    results: list[tuple[LoadedBundle, DeckDiff, list[str]]] = []
    errors: list[str] = []
    pairs, solos = _scope_pairs(de_path, en_path)
    _warn_solos(solos)
    for de, en in pairs:
        try:
            bundle = _load(de, en)
        except DocLensError as exc:
            errors.append(str(exc))
            continue
        if since_ref is not None:
            diff, base_refusal = diff_bundle_at_ref(bundle, since_ref)
        else:
            diff, base_refusal = diff_bundle(bundle), []
        results.append((bundle, diff, base_refusal))
    clean = all(diff.is_clean for _, diff, _refusal in results) and not errors
    if as_json:
        payloads = []
        for bundle, diff, base_refusal in results:
            payload = pair_payload(bundle, diff)
            if since_ref is not None:
                payload["baseline"] = f"since:{since_ref}"
                if base_refusal:
                    payload["base_refusal"] = base_refusal
            payloads.append(payload)
        if not de_path.is_dir() and len(payloads) == 1 and not errors:
            _echo_json(payloads[0])
        else:
            _echo_json(
                {
                    "schema": 3,
                    "engine": "v3",
                    "is_clean": clean,
                    "needs_model": any(d.needs_model for _, d, _r in results),
                    "needs_agent": any(d.needs_agent for _, d, _r in results) or bool(errors),
                    "errors": errors,
                    "skipped_solos": [str(p) for p in solos],
                    "pairs": payloads,
                }
            )
    else:
        for bundle, diff, base_refusal in results:
            click.echo(_render_pair(bundle, diff))
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
) -> int:
    """The v3 write verb. Exit 0 all-applied / 1 residue / 2 error."""
    if de_path.is_dir():
        raise click.UsageError("apply works on a single deck — run report over the directory")
    try:
        bundle = _load(de_path, en_path)
    except DocLensError as exc:
        raise click.UsageError(str(exc)) from exc

    decisions: dict[str, doc_apply.Decision] = {}
    if decisions_spec is not None:
        try:
            text = (
                sys.stdin.read()
                if decisions_spec == "-"
                else Path(decisions_spec).read_text(encoding="utf-8")
            )
        except OSError as exc:
            raise click.UsageError(f"cannot read the decision document: {exc}") from exc
        decisions, decision_errors = doc_apply.load_decisions_text(text)
        if decision_errors:
            for error in decision_errors:
                click.echo(f"decision error: {error}", err=True)
            return 2

    diff = diff_bundle(bundle)
    if diff.refusal is not None:
        message = diff.refusal.render()
        if as_json:
            _echo_json({"schema": 3, "engine": "v3", "error": message, "items": []})
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
        only_members=set(members) if members else None,
        dry_run=dry_run,
        commit=_head_commit(bundle.de_path),
    )
    verify_violations: list[str] = []
    if outcome.error is None and not dry_run and outcome.ledger_changed:
        # The structural write-gate on the TRUST store (design §5): landed
        # file mutations stay (review them with git), but a pair that fails
        # the structural verify is never recorded as verified — same gate
        # `record` applies. Lazy import: sync_verify still loads v2 modules.
        from clm.slides.sync_verify import structural_gate

        verify_violations = [
            v.message
            for v in structural_gate(
                bundle.de_path.read_text(encoding="utf-8"),
                bundle.en_path.read_text(encoding="utf-8"),
                bundle.comment_token,
            )
        ]
        if not verify_violations:
            doc_ledger.save(ledger, ledger_path)

    if as_json:
        payload = outcome.to_payload()
        payload["ledger_recorded"] = outcome.ledger_changed and not verify_violations
        payload["verify_violations"] = verify_violations
        _echo_json(payload)
    else:
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
                "recorded into the ledger; fix the pair, then `sync record`",
                err=True,
            )
    if outcome.error is not None:
        return 2
    return 0 if outcome.all_applied and not verify_violations else 1


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
) -> int:
    """The v3 trust verb: bless/accept collapsed, gated on structural verify.

    Exit 0 all recorded / 1 some pairs refused / 2 error.
    """
    if provenance not in ("record", "agent") and not provenance.startswith("semantic:"):
        raise click.UsageError("--provenance must be 'record', 'agent', or 'semantic:<model>'")
    rows: list[dict] = []
    refused = 0
    errors = 0
    pairs, solos = _scope_pairs(de_path, en_path)
    _warn_solos(solos)
    for de, en in pairs:
        row = _record_one(de, en, members=members, provenance=provenance)
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
                "schema": 3,
                "engine": "v3",
                "recorded": sum(r.get("recorded", 0) for r in rows),
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
    # is never recorded as verified. Lazy import — sync_verify still imports
    # v2 modules, and this module must stay clean of them at import time.
    from clm.slides.sync_verify import structural_gate

    violations = structural_gate(
        bundle.de_path.read_text(encoding="utf-8"),
        bundle.en_path.read_text(encoding="utf-8"),
        bundle.comment_token,
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
    )
    doc_ledger.save(ledger, ledger_path)
    row["recorded"] = recorded
    row["ledger"] = str(ledger_path)
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
    click.echo(f"{name}: recorded {row['recorded']} member(s) -> {row['ledger']}")
    for old, new in row.get("key_migrations", {}).items():
        click.echo(f"  key migrated {old} -> {new}")
