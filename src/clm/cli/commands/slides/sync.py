"""``clm slides sync`` — the agent toolkit for syncing split DE/EN deck pairs.

The document-model engine (#520): every verb reads the pair's ≤4 files
(deck halves + voiceover companions) into one canonical
:class:`~clm.slides.bilingual_doc.BilingualDeck`, diffs its members 3-way
against the committed per-topic ledger (``<topic>/.clm/sync-ledger.json`` —
the only trust store), and reports/applies per member. Mechanical items
(verbatim shared-cell propagation, mirrors, transitions) are executed
deterministically; framed items (translations, conflicts, cold members) are
emitted with their decision vocabulary so an agent can answer them in one
JSON document (``apply --decisions``). No verb ever calls a model.

Verbs:

- ``report`` — read-only member table; exit 0 clean / 1 work pending / 2 error.
- ``apply``  — write the mechanical rows + validated decisions, per item.
- ``verify`` — structural gate (no model, no ledger); exit 0 sound / 2 corrupt.
- ``record`` — bank the deck's verified state in the committed ledger.

Bare ``clm slides sync DECK`` is ``report DECK``. Review writes with
``git diff``; confirm soundness with ``verify``; bank trust with ``record``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click
from attrs import define

from clm.slides.pairing import (
    derive_split_pair_from_stem,
    derive_split_twin,
    find_split_slide_files_recursive,
    iter_split_pairs,
    order_split_pair,
    split_lang_tag,
)
from clm.slides.sync_verify import VerifyResult, verify_pair
from clm.slides.voiceover_tools import COMPANION_SUBDIR, resolve_companion


def _resolve_sync_pair(de_path: Path, en_path: Path) -> tuple[Path, Path]:
    """Validate the two positional paths are the DE/EN halves of one split deck,
    auto-correcting a swapped order, and return them as ``(de, en)``.

    Guards the #162 footgun: a swapped, same-file, same-language, or cross-deck
    pair would otherwise sync silently — producing a divergent or no-op result
    on an error-free pass. Raises :class:`click.UsageError` on an invalid pair.
    The check is deliberately prefix-agnostic — ``sync`` reconciles whatever two
    halves it is given, independent of the build's topic-routing prefix;
    existence on disk is already enforced by ``click.Path(exists=True)``.
    """
    if de_path == en_path:
        raise click.UsageError(
            f"DE_PATH and EN_PATH are the same file ({de_path}); pass the two "
            "halves of a split deck — <deck>.de.<ext> and <deck>.en.<ext>."
        )
    de_tag, en_tag = split_lang_tag(de_path), split_lang_tag(en_path)
    if de_tag is None or en_tag is None:
        bad = de_path if de_tag is None else en_path
        raise click.UsageError(
            f"{bad} is not a split-format slide half. `clm slides sync` expects "
            "two paths named <deck>.de.<ext> and <deck>.en.<ext> "
            "(run `clm slides split <deck>.py` to produce them)."
        )
    if de_tag == en_tag:
        raise click.UsageError(
            f"both paths are the same language (.{de_tag}); pass one .de half and one .en half."
        )
    ordered = order_split_pair(de_path, en_path)
    if ordered is None:
        raise click.UsageError(
            f"{de_path.name} and {en_path.name} belong to different decks; "
            "pass the two halves of ONE deck (same name before the .de/.en tag)."
        )
    if ordered != (de_path, en_path):
        click.echo(
            f"note: arguments look swapped — treating {ordered[0].name} as the "
            f"DE half and {ordered[1].name} as the EN half.",
            err=True,
        )
    return ordered


def _deck_for_companion(companion_path: Path) -> Path | None:
    """Map a ``voiceover_*`` companion argument back to its slide-deck half (#501).

    ``clm slides sync voiceover_x.de.py`` should reconcile the *deck* the companion
    belongs to. The companion may sit beside the deck or one directory up (a
    relocated ``voiceover/`` companion), and its stem prefix (``slides_`` / ``topic_``
    / ``project_`` / none) is not recoverable from the companion name alone — so we
    generate every candidate deck path and keep the one whose own
    :func:`resolve_companion` round-trips back to this file. ``None`` when no deck
    claims the companion.
    """
    name = companion_path.name
    prefix = "voiceover_"
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix) :]  # "<stem>.<lang>.<ext>"
    deck_dirs = [companion_path.parent]
    if companion_path.parent.name == COMPANION_SUBDIR:
        deck_dirs.append(companion_path.parent.parent)
    resolved = companion_path.resolve()
    for deck_dir in deck_dirs:
        for deck_prefix in ("slides_", "topic_", "project_", ""):
            candidate = deck_dir / f"{deck_prefix}{rest}"
            if not candidate.exists():
                continue
            comp = resolve_companion(candidate)
            if comp is not None and comp.resolve() == resolved:
                return candidate
    return None


def _resolve_single_path(de_path: Path, en_path: Path | None) -> tuple[Path, Path]:
    """Single-path contract: when EN_PATH is omitted, derive the second half from
    DE_PATH so the author can run ``clm slides sync <deck>.de.<ext>``.

    DE_PATH may be **one half** (``<deck>.de.<ext>`` / ``<deck>.en.<ext>``) — the twin
    is derived from disk — or a **bilingual deck stem** (``<deck>.py``, no
    ``.de``/``.en`` tag) whose two halves both exist. Derivation is prefix-agnostic
    (so ``apis.de.py`` works) and the resolved pair is still funnelled through
    :func:`_resolve_sync_pair` for the #162 pairing guard. Raises
    :class:`click.UsageError` when the twin / halves are not found on disk — a
    missing twin is almost always a typo or an un-split deck, so we error clearly
    rather than invent a full translated half.
    """
    if en_path is not None:
        return de_path, en_path
    tag = split_lang_tag(de_path)
    if tag is not None:
        twin = derive_split_twin(de_path)
        if twin is None:
            if de_path.name.startswith("voiceover_"):
                # Issue #501: pointing sync at a companion reconciles its *deck* pair.
                deck = _deck_for_companion(de_path)
                if deck is not None:
                    return _resolve_single_path(deck, None)
                raise click.UsageError(
                    f"{de_path.name} is a voiceover companion, but its slide deck could "
                    f"not be found; expected the deck half (<deck>.de.<ext> / "
                    f"<deck>.en.<ext>) beside it or one directory up from a "
                    f"`{COMPANION_SUBDIR}/` companion. Pass the deck half instead."
                )
            other = "EN" if tag == "de" else "DE"
            raise click.UsageError(
                f"no {other} twin found next to {de_path.name}; expected its sibling "
                f"split half on disk. Pass both halves explicitly, or run "
                f"`clm slides split` to produce the pair."
            )
        # Return already (de, en)-ordered so the pairing guard's swap note does not
        # fire on a single derived path — the author supplied one path; nothing was
        # "swapped". (derive_split_twin gives the OTHER half, so order by our tag.)
        return (de_path, twin) if tag == "de" else (twin, de_path)
    # No language tag → treat DE_PATH as a bilingual deck stem and derive both halves.
    pair = derive_split_pair_from_stem(de_path)
    if pair is None:
        ext = de_path.suffix
        stem = de_path.name[: -len(ext)] if ext else de_path.name
        raise click.UsageError(
            f"{de_path.name} is neither a split half (<deck>.de.<ext> / <deck>.en.<ext>) "
            f"nor a deck stem with both halves present (expected {stem}.de{ext} and "
            f"{stem}.en{ext} on disk). Pass the two halves explicitly."
        )
    return pair


def _resolve_verb_scope(de_path: Path, en_path: Path | None) -> tuple[Path, Path | None]:
    """The verb-layer scope: a directory passes through; a file resolves its pair.

    Funnels a single-file argument through the single-path contract (twin /
    stem / #501 companion derivation) and the #162 pairing guard, so every
    verb accepts the same argument shapes.
    """
    if de_path.is_dir():
        if en_path is not None:
            raise click.UsageError(
                f"{de_path} is a directory (batch mode), which takes a single "
                "directory argument; do not pass a second path."
            )
        return de_path, None
    de_resolved, en_resolved = _resolve_single_path(de_path, en_path)
    return _resolve_sync_pair(de_resolved, en_resolved)


# ---------------------------------------------------------------------------
# --since DATE|REF  (Issue #446): resolve a timeframe to a concrete commit.
#
# CLI sugar: a date/relative time resolves to the commit that was HEAD at that
# instant; the concrete SHA flows into the report's forensic ref-baseline view
# (design §12.3 — a VIEW, never a trust source). Raw subprocess (no run_git
# dry-run/auth shim) — timeframe resolution belongs at the CLI layer.
# ---------------------------------------------------------------------------


@define(frozen=True)
class SinceResolution:
    """A ``--since DATE|REF`` value resolved to a concrete commit (#446)."""

    requested: str
    resolved_sha: str
    kind: str  # "ref" | "date"
    committed: str | None


def _git_capture(cwd: Path, args: list[str]) -> tuple[int, str]:
    """Run ``git <args>`` in ``cwd``; return ``(returncode, stdout.strip())``.

    ``(-1, "")`` when git is unavailable (utf-8, ``check=False``).
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (FileNotFoundError, OSError):
        return (-1, "")
    return (completed.returncode, completed.stdout.strip())


def _git_committed_iso(cwd: Path, sha: str) -> str | None:
    """The ISO committer date of ``sha`` (``git show -s --format=%cI``), or ``None``."""
    rc, out = _git_capture(cwd, ["show", "-s", "--format=%cI", sha])
    return out if (rc == 0 and out) else None


def _resolve_since(value: str, cwd: Path) -> SinceResolution:
    """Resolve ``--since VALUE`` to a concrete commit (issue #446).

    Try-ref-first: if VALUE peels to a commit (``git rev-parse --verify
    VALUE^{commit}``) it is used verbatim — ``--since HEAD~1`` diffs against
    ``HEAD~1`` and the literal ``HEAD`` resolves as a ref. Otherwise VALUE is a git
    approxidate/date (``"2 days ago"``, ``2026-06-21``) and resolves to the last commit
    at/before that instant (``git rev-list -1 --before=VALUE HEAD``) — the commit that
    was HEAD then, so the diff captures everything edited *since*.

    Raises :class:`click.UsageError` on empty input, a non-repo ``cwd``, or an
    unresolvable/empty date. NOTE: ``rev-list --before`` filters by *committer* date,
    so on rebased/cherry-picked (non-monotonic) history the chosen commit may not be the
    strict HEAD-at-that-instant — fine for linear authoring history.
    """
    value = value.strip()
    if not value:
        raise click.UsageError(
            '--since needs a date or git ref (e.g. "2 days ago", 2026-06-21, HEAD~1).'
        )
    rc, _root = _git_capture(cwd, ["rev-parse", "--show-toplevel"])
    if rc != 0:
        raise click.UsageError(
            f"--since needs git history, but {cwd} is not inside a git work tree."
        )
    rc, sha = _git_capture(cwd, ["rev-parse", "--verify", "--quiet", f"{value}^{{commit}}"])
    if rc == 0 and sha:
        return SinceResolution(
            requested=value, resolved_sha=sha, kind="ref", committed=_git_committed_iso(cwd, sha)
        )
    rc, sha = _git_capture(cwd, ["rev-list", "-1", f"--before={value}", "HEAD"])
    if rc != 0:
        raise click.UsageError(f"--since {value!r}: could not query git history (is HEAD valid?).")
    if not sha:
        raise click.UsageError(
            f"--since {value!r}: no commit at or before that time (the repo's history may "
            "not reach that far back, or the date is malformed)."
        )
    return SinceResolution(
        requested=value, resolved_sha=sha, kind="date", committed=_git_committed_iso(cwd, sha)
    )


def _since_cwd(de_path: Path) -> Path:
    """The directory to run ``--since`` git queries from: the deck's own directory."""
    base = de_path if de_path.is_dir() else de_path.parent
    return base.resolve()


def _resolve_since_ref(since_spec: str | None, de_path: Path) -> str | None:
    """Resolve ``--since`` to a concrete SHA (``None`` when absent), echoing it.

    The resolved commit goes to stderr (stdout stays clean for ``--json``); it
    also surfaces as the report's ``since:<sha>`` baseline label.
    """
    if since_spec is None:
        return None
    resolution = _resolve_since(since_spec, _since_cwd(de_path))
    when = f" (committed {resolution.committed})" if resolution.committed else ""
    click.echo(
        f"resolved --since {resolution.requested!r} to {resolution.resolved_sha[:12]}{when}.",
        err=True,
    )
    return resolution.resolved_sha


# ---------------------------------------------------------------------------
# verify — the structural gate (kept verbatim across the engine cutover)
# ---------------------------------------------------------------------------


def _run_verify(de_path: Path, en_path: Path | None, *, as_json: bool) -> None:
    """Structurally verify a pair or a directory tree, then ``sys.exit``.

    Single pair: resolve the twin / pairing exactly as the sync modes do, then
    verify. Directory: sweep every split pair under the tree (a half with no twin
    is skipped with a warning). Exit is the worst per-pair code (0 valid < 2
    corrupt); warnings never fail the gate. Always ``sys.exit``s.
    """
    root: Path | None
    if de_path.is_dir():
        if en_path is not None:
            raise click.UsageError(
                f"{de_path} is a directory (batch verify), which takes a single "
                "directory argument; do not pass a second path."
            )
        pairs, solos = iter_split_pairs(find_split_slide_files_recursive(de_path))
        for solo in solos:
            tag = split_lang_tag(solo)
            other = "EN" if tag == "de" else "DE"
            click.echo(
                f"warning: skipping {solo.name} — no {other} twin found under {de_path}.",
                err=True,
            )
        results = [verify_pair(de_p.resolve(), en_p.resolve()) for de_p, en_p in pairs]
        root = de_path
    else:
        de_resolved, en_resolved = _resolve_single_path(de_path, en_path)
        de_resolved, en_resolved = _resolve_sync_pair(de_resolved, en_resolved)
        de_resolved, en_resolved = de_resolved.resolve(), en_resolved.resolve()
        results = [verify_pair(de_resolved, en_resolved)]
        root = None

    exit_code = 2 if any(not r.ok for r in results) else 0
    if as_json:
        click.echo(json.dumps(_verify_to_dict(results, root, exit_code), indent=2))
    else:
        _print_verify_human(results, root)
    sys.exit(exit_code)


def _verify_to_dict(
    results: list[VerifyResult], root: Path | None, exit_code: int
) -> dict[str, object]:
    payload: dict[str, object] = {"mode": "verify", "exit_code": exit_code}
    if root is not None:
        payload["root"] = str(root)
    payload["pairs"] = [
        {
            "de_path": str(r.de_path),
            "en_path": str(r.en_path),
            "ok": r.ok,
            "git_baseline": r.git_baseline,
            "violations": [
                {
                    "severity": v.severity,
                    "kind": v.kind,
                    "message": v.message,
                    "slide_id": v.slide_id,
                }
                for v in r.violations
            ],
        }
        for r in results
    ]
    return payload


def _print_verify_human(results: list[VerifyResult], root: Path | None) -> None:
    if not results:
        click.echo(
            f"no split-format deck pairs found under {root}."
            if root is not None
            else "nothing to verify."
        )
        return
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        bits = []
        if r.errors:
            bits.append(f"{len(r.errors)} error{'s' if len(r.errors) != 1 else ''}")
        if r.warnings:
            bits.append(f"{len(r.warnings)} warning{'s' if len(r.warnings) != 1 else ''}")
        if not r.git_baseline:
            bits.append("no-drop check skipped (untracked)")
        summary = f" ({', '.join(bits)})" if bits else " (structurally valid)"
        click.echo(f"{mark} {r.de_path.name}{summary}")
        for v in r.violations:
            click.echo(f"    {v.severity} [{v.kind}]: {v.message}")
    if len(results) > 1:
        valid = sum(1 for r in results if r.ok)
        total_warn = sum(len(r.warnings) for r in results)
        tail = f", {total_warn} warning(s)" if total_warn else ""
        click.echo(
            f"\nverified {len(results)} pair(s): {valid} valid, "
            f"{len(results) - valid} with errors{tail}."
        )


# ---------------------------------------------------------------------------
# The verb group
# ---------------------------------------------------------------------------


class _DefaultVerbGroup(click.Group):
    """A ``sync`` group whose bare ``clm slides sync DECK`` runs ``report``.

    Click groups have no native default subcommand. When the first token is not a
    known verb (and not a help flag), prepend ``report`` so a bare deck path is
    treated as ``report DECK`` — the read-only default the redesign mandates.
    """

    _DEFAULT_VERB = "report"

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] not in self.commands and args[0] not in ("--help", "-h"):
            args = [self._DEFAULT_VERB, *args]
        return super().parse_args(ctx, args)


@click.group("sync", cls=_DefaultVerbGroup)
def slides_sync_group() -> None:
    """Agent toolkit for syncing split DE/EN deck pairs.

    \b
    Bare `clm slides sync DECK` == `clm slides sync report DECK` (read-only).
    Verbs:
      report     per-member sync state vs the committed ledger (read-only, no model)
      apply      write the mechanical items + validated decisions, per item
      verify     structural integrity check (no model, no ledger)
      record     bank the deck's verified state in the committed ledger

    \b
    The engine (#520): one canonical bilingual deck parsed from the pair's
    <=4 files, a generic 3-way member diff, and the committed per-topic
    ledger (<topic>/.clm/sync-ledger.json) as the only trust store. Framed
    items carry their decision vocabulary; answer them with a JSON decision
    document via `apply --decisions` (see `clm info sync-agents`).
    """


#: Shared deck arguments so every verb takes ``DECK [EN_PATH]`` consistently.
_DECK_ARG = click.argument(
    "de_path",
    metavar="DECK",
    type=click.Path(exists=True, dir_okay=True, path_type=Path),
)
_EN_ARG = click.argument(
    "en_path",
    required=False,
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)


@slides_sync_group.command("report")
@_DECK_ARG
@_EN_ARG
@click.option("--json", "as_json", is_flag=True, help="Emit the report as JSON.")
@click.option(
    "--since",
    "since_spec",
    default=None,
    metavar="DATE|REF",
    help=(
        "Forensic view: diff against the bundle at a git ref instead of the ledger. "
        'A ref is used verbatim; a date/relative time ("2 days ago", 2026-06-21) '
        "resolves to the last commit at/before it. A VIEW only — the ledger is "
        "neither consulted nor written."
    ),
)
def sync_report_cmd(
    de_path: Path,
    en_path: Path | None,
    as_json: bool,
    since_spec: str | None,
) -> None:
    """Report the pair's per-member sync state (writes nothing, no model).

    The primary agent verb: it states *what reconciliation is necessary* as
    member-keyed items — mechanical actions ``apply`` can execute, and framed
    actions carrying their decision-answer vocabulary — with the stable
    ``is_clean`` / ``needs_model`` / ``needs_agent`` booleans. The baseline is
    the committed per-topic ledger; a member with no ledger entry is **cold**
    (frame ``verify_cold``), never silently trusted. ``--since DATE|REF``
    switches to the forensic git-window view. Works over a directory.
    """
    from clm.cli.commands.slides.sync_v3 import run_report_v3

    de_path, en_path = _resolve_verb_scope(de_path, en_path)
    since_ref = _resolve_since_ref(since_spec, de_path)
    sys.exit(run_report_v3(de_path, en_path, as_json=as_json, since_ref=since_ref))


@slides_sync_group.command("verify")
@_DECK_ARG
@_EN_ARG
@click.option("--json", "as_json", is_flag=True, help="Emit the verify result as JSON.")
def sync_verify_cmd(de_path: Path, en_path: Path | None, as_json: bool) -> None:
    """Structural integrity check (no model, no ledger, writes nothing).

    Confirms the pair is a valid split — byte-identical shared cells, header parity,
    clean alignment, ``de_id == en_id`` symmetry, no duplicate ids — and warns on an
    id'd cell dropped vs git ``HEAD``. Exit ``0`` = sound (warnings allowed), ``2`` =
    corrupt. Answers "did this edit corrupt the pair?", not "is it in sync?".
    """
    _run_verify(de_path, en_path, as_json=as_json)


@slides_sync_group.command("record")
@_DECK_ARG
@_EN_ARG
@click.option(
    "--member",
    "members",
    multiple=True,
    metavar="KEY",
    help="Record only these member handles (default: the whole deck, swept).",
)
@click.option(
    "--provenance",
    default="record",
    show_default=True,
    metavar="WHO",
    help="Trust provenance to stamp: 'record', 'agent', or 'semantic:<model>'.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the record result as JSON.")
def sync_record_cmd(
    de_path: Path,
    en_path: Path | None,
    members: tuple[str, ...],
    provenance: str,
    as_json: bool,
) -> None:
    """Record the deck's current verified state into the committed ledger.

    The bless/accept confirmation paths collapsed into one verb (design §8):
    after you have verified — or reconciled — a deck, ``record`` banks its
    members' current fingerprints in ``<topic>/.clm/sync-ledger.json`` so
    ``report`` trusts them until they drift. Gated on the structural verify
    (a corrupt pair is refused, nothing written). A full record sweeps stale
    entries and performs the §7.3 pos→id key migration (logged); ``--member``
    upserts just the named handles. Works over a directory.
    """
    from clm.cli.commands.slides.sync_v3 import run_record_v3

    de_path, en_path = _resolve_verb_scope(de_path, en_path)
    sys.exit(
        run_record_v3(
            de_path,
            en_path,
            members=members,
            provenance=provenance,
            as_json=as_json,
        )
    )


@slides_sync_group.command("apply")
@_DECK_ARG
@_EN_ARG
@click.option(
    "--decisions",
    "decisions_spec",
    default=None,
    metavar="FILE|-",
    help=(
        "A JSON decision document answering framed report items per member "
        "handle ('-' reads stdin). Invalid answers are rejected per item; "
        "valid ones land."
    ),
)
@click.option(
    "--member",
    "members",
    multiple=True,
    metavar="KEY",
    help="Apply only the item(s) with these member handles.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Execute and validate everything, write nothing.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the apply result as JSON.")
def sync_apply_cmd(
    de_path: Path,
    en_path: Path | None,
    decisions_spec: str | None,
    members: tuple[str, ...],
    dry_run: bool,
    as_json: bool,
) -> None:
    """Apply the reconciliation per item — writes files, never calls a model.

    Executes every **mechanical** report row (verbatim shared-cell
    propagation, mirrors, tag/order/layout transitions) plus any **framed**
    rows answered in a ``--decisions`` document, validating each answer
    through the accept-gates before it lands. Items are independent: an
    invalid answer is rejected with a reason while valid ones land, and the
    ledger records each landed item (gated on the structural verify).
    Unanswered framed items are residue — reported, exit ``1`` — pointing you
    at ``report`` for their decision vocabulary.

    \b
    Needs no API key. Review writes with `git diff`; confirm soundness with
    `clm slides sync verify`. Exit 0 all-applied / 1 residue / 2 error.
    """
    from clm.cli.commands.slides.sync_v3 import run_apply_v3

    if not de_path.is_dir():
        de_path, en_path = _resolve_verb_scope(de_path, en_path)
    sys.exit(
        run_apply_v3(
            de_path,
            en_path,
            decisions_spec=decisions_spec,
            members=members,
            dry_run=dry_run,
            as_json=as_json,
        )
    )
