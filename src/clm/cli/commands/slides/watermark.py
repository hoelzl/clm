"""``clm slides watermark`` — inspect and maintain the sync structural watermark.

Issue #363. ``clm slides sync`` records a per-language structural *watermark* — the
last-synced state of a split deck pair — in the shared ``clm-llm.sqlite``, keyed by
the absolute ``(de_path, en_path)`` of the pair (see :class:`SyncWatermarkCache`).
The watermark is normally invisible, but it can go **stale** (a deck edited and
committed on *both* halves without an intervening ``sync``, so the baseline falls
behind the working tree) or **orphaned** (its files renamed / renumbered away, so the
rows point at paths that no longer exist). Previously the only way to inspect or reset
it was hand-written SQL against the shared database.

This subcommand group exposes three maintenance operations over the same store the
``sync`` command writes:

- ``list``  — show every watermarked pair (row count, last sync, on-disk status).
- ``clear`` — delete the watermark for a deck / half / stem / directory; the next
  ``sync`` then re-baselines off git ``HEAD`` (the proven-clean cold-start path).
- ``prune`` — drop watermarks whose files no longer exist on disk (orphans).

``clear`` resolves the deck path through the same helpers ``sync`` uses
(:func:`_resolve_single_path` / :func:`_resolve_sync_pair`) and keys by the same
``str(path.resolve())`` form ``sync`` writes, so a cleared pair is exactly the pair a
subsequent ``sync`` of that deck would look up.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from attrs import define

from clm.cli.commands.slides.sync import (
    CACHE_DB_NAME,
    _resolve_single_path,
    _resolve_sync_pair,
)
from clm.infrastructure.llm.cache import SyncWatermarkCache, resolve_cache_dir
from clm.slides.pairing import find_split_slide_files_recursive, iter_split_pairs
from clm.slides.sync_apply import record_baseline
from clm.slides.sync_verify import VerifyResult, verify_pair


@define
class _PairWatermark:
    """A pair's watermark, rolled up from the per-cell rows of ``iter_entries``."""

    de_path: str
    en_path: str
    rows: int
    langs: dict[str, int]
    synced_at: str | None

    @property
    def de_exists(self) -> bool:
        return Path(self.de_path).exists()

    @property
    def en_exists(self) -> bool:
        return Path(self.en_path).exists()

    @property
    def orphan(self) -> bool:
        """True when either half no longer exists on disk (a dead watermark)."""
        return not (self.de_exists and self.en_exists)


def _group_watermarks(cache: SyncWatermarkCache) -> list[_PairWatermark]:
    """Roll the per-cell ``iter_entries`` rows up into one entry per pair.

    Rows arrive ordered by ``(de_path, en_path, lang, position)``; we aggregate the
    row count, per-language counts, and the latest ``synced_at`` per pair. Order of
    the returned list follows first appearance, i.e. sorted by pair path.
    """
    by_pair: dict[tuple[str, str], _PairWatermark] = {}
    for (
        de_path,
        en_path,
        lang,
        _pos,
        _sid,
        _role,
        _chash,
        _construct,
        synced_at,
    ) in cache.iter_entries():
        key = (de_path, en_path)
        entry = by_pair.get(key)
        if entry is None:
            entry = by_pair[key] = _PairWatermark(de_path, en_path, 0, {}, synced_at)
        entry.rows += 1
        entry.langs[lang] = entry.langs.get(lang, 0) + 1
        if synced_at is not None and (entry.synced_at is None or synced_at > entry.synced_at):
            entry.synced_at = synced_at
    return list(by_pair.values())


def _open_cache(cache_dir: Path | None) -> SyncWatermarkCache:
    cache_root = resolve_cache_dir(cli_override=cache_dir)
    return SyncWatermarkCache(cache_root / CACHE_DB_NAME)


def _label(path_str: str) -> str:
    """A deck label: relative to the cwd when possible, else the absolute path."""
    try:
        return str(Path(path_str).relative_to(Path.cwd()))
    except ValueError:
        return path_str


def _resolve_pairs(deck: Path) -> list[tuple[str, str]]:
    """Resolve a ``clear`` target to one or more ``(de_key, en_key)`` watermark keys.

    A directory enumerates every split pair under it (batch); a file/stem is funnelled
    through the same single-path + pairing guards ``sync`` uses, so ``clear`` accepts
    exactly what ``sync`` accepts (a ``.de``/``.en`` half, or a bilingual ``<deck>.py``
    stem with both halves on disk). Keys are ``str(path.resolve())`` to match the form
    ``sync`` writes.
    """
    if deck.is_dir():
        pairs, _solos = iter_split_pairs(find_split_slide_files_recursive(deck))
        return [(str(de.resolve()), str(en.resolve())) for de, en in pairs]
    de_path, en_path = _resolve_single_path(deck, None)
    de_path, en_path = _resolve_sync_pair(de_path, en_path)
    return [(str(de_path.resolve()), str(en_path.resolve()))]


# ---------------------------------------------------------------------------
# clm slides watermark  (group)
# ---------------------------------------------------------------------------


@click.group("watermark")
def watermark_group() -> None:
    """Inspect and maintain the ``slides sync`` structural watermark."""


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@watermark_group.command("list")
@click.argument(
    "path",
    required=False,
    default=None,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Directory holding the structural watermark (default: --cache-dir > "
        "$CLM_CACHE_DIR > tool.clm.cache_dir in pyproject.toml > <cwd>/.clm-cache/)."
    ),
)
@click.option("--orphans", is_flag=True, default=False, help="Show only orphaned watermarks.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit a JSON report.")
def watermark_list_cmd(
    path: Path | None, cache_dir: Path | None, orphans: bool, as_json: bool
) -> None:
    """List watermarked deck pairs: row count, last sync, and on-disk status.

    With PATH (a directory), only pairs whose DE half resolves under it are shown.
    ``ORPHAN`` marks a pair whose files no longer exist (clear it with
    ``clm slides watermark prune``).
    """
    cache = _open_cache(cache_dir)
    try:
        entries = _group_watermarks(cache)
    finally:
        cache.close()

    if path is not None:
        root = path.resolve()
        entries = [e for e in entries if _is_under(Path(e.de_path), root)]
    if orphans:
        entries = [e for e in entries if e.orphan]
    entries.sort(key=lambda e: e.de_path)

    if as_json:
        click.echo(
            json.dumps(
                [
                    {
                        "de_path": e.de_path,
                        "en_path": e.en_path,
                        "rows": e.rows,
                        "langs": e.langs,
                        "synced_at": e.synced_at,
                        "orphan": e.orphan,
                    }
                    for e in entries
                ],
                indent=2,
            )
        )
        return

    if not entries:
        click.echo("no watermarks found." if path is None else f"no watermarks found under {path}.")
        return
    for e in entries:
        status = "ORPHAN" if e.orphan else "OK    "
        langs = ",".join(f"{lang}:{n}" for lang, n in sorted(e.langs.items()))
        click.echo(f"{status} {_label(e.de_path)}  [{e.rows} rows: {langs}]  synced {e.synced_at}")
    orphan_n = sum(1 for e in entries if e.orphan)
    click.echo("")
    click.echo(f"{len(entries)} pair(s), {orphan_n} orphan(ed).")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


@watermark_group.command("clear")
@click.argument("deck", type=click.Path(exists=True, dir_okay=True, path_type=Path))
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory holding the structural watermark (see `clm slides sync --cache-dir`).",
)
@click.option(
    "--dry-run", is_flag=True, default=False, help="Report what would be cleared; delete nothing."
)
@click.option(
    "--yes",
    "-y",
    "yes",
    is_flag=True,
    default=False,
    help="Confirm clearing every pair under a directory without the interactive prompt.",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit a JSON report.")
def watermark_clear_cmd(
    deck: Path, cache_dir: Path | None, dry_run: bool, yes: bool, as_json: bool
) -> None:
    """Delete the watermark for a deck so the next ``sync`` re-baselines off git HEAD.

    DECK is a split half (``<deck>.de.py`` / ``<deck>.en.py``), a bilingual stem
    (``<deck>.py`` with both halves on disk), or a **directory** (every pair under it).
    Use this when a deck's halves are already consistent but ``sync`` errors against a
    stale baseline — clearing forces a clean cold-start on the next run.
    """
    pairs = _resolve_pairs(deck)
    cache = _open_cache(cache_dir)
    try:
        if deck.is_dir() and not pairs:
            cache.close()
            _emit_clear(deck, [], dry_run=dry_run, as_json=as_json)
            return
        # A directory clear can wipe many pairs' baselines — gate the write like
        # `sync` gates a batch apply. A single explicitly-named deck is not gated.
        if deck.is_dir() and not dry_run and not yes:
            if as_json:
                raise click.UsageError(
                    f"clearing {len(pairs)} pair(s) under {deck} needs --yes (cannot prompt "
                    "with --json); add --yes, or preview with --dry-run."
                )
            click.confirm(
                f"About to clear the watermark for {len(pairs)} pair(s) under {deck}. Continue?",
                abort=True,
            )

        existing = {(e.de_path, e.en_path): e for e in _group_watermarks(cache)}
        results: list[tuple[str, str, int]] = []
        for de_key, en_key in pairs:
            entry = existing.get((de_key, en_key))
            if dry_run:
                removed = entry.rows if entry is not None else 0
            elif entry is None:
                removed = 0
            else:
                removed = cache.clear_pair(de_key, en_key)
            results.append((de_key, en_key, removed))
    finally:
        cache.close()

    _emit_clear(deck, results, dry_run=dry_run, as_json=as_json)


def _emit_clear(
    deck: Path,
    results: list[tuple[str, str, int]],
    *,
    dry_run: bool,
    as_json: bool,
) -> None:
    verb = "would clear" if dry_run else "cleared"
    if as_json:
        click.echo(
            json.dumps(
                {
                    "deck": str(deck),
                    "dry_run": dry_run,
                    "pairs": [
                        {"de_path": de, "en_path": en, "rows": rows} for de, en, rows in results
                    ],
                    "total_rows": sum(rows for _de, _en, rows in results),
                },
                indent=2,
            )
        )
        return
    if not results:
        click.echo(f"no split-format deck pairs found under {deck}.")
        return
    for de_key, _en_key, rows in results:
        if rows == 0 and not dry_run:
            click.echo(f"no watermark for {_label(de_key)} (nothing to clear).")
        else:
            click.echo(f"{verb} {rows} row(s) for {_label(de_key)}.")
    total = sum(rows for _de, _en, rows in results)
    click.echo("")
    click.echo(f"{verb}: {total} row(s) across {len(results)} pair(s).")
    if not dry_run and total:
        click.echo("The next `clm slides sync` for these decks will re-baseline off git HEAD.")


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


@watermark_group.command("prune")
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory holding the structural watermark (see `clm slides sync --cache-dir`).",
)
@click.option("--dry-run", is_flag=True, default=False, help="Report orphans; delete nothing.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit a JSON report.")
def watermark_prune_cmd(cache_dir: Path | None, dry_run: bool, as_json: bool) -> None:
    """Drop watermarks whose files no longer exist on disk (orphans from rename/renumber).

    A watermark keyed by an absolute path that is gone from disk can never be matched
    by a ``sync`` again, but it lingers and clutters ``list``. Pruning removes those
    dead rows; live pairs are untouched.
    """
    cache = _open_cache(cache_dir)
    try:
        orphans = [e for e in _group_watermarks(cache) if e.orphan]
        orphans.sort(key=lambda e: e.de_path)
        results: list[tuple[str, str, int]] = []
        for e in orphans:
            removed = e.rows if dry_run else cache.clear_pair(e.de_path, e.en_path)
            results.append((e.de_path, e.en_path, removed))
    finally:
        cache.close()

    verb = "would prune" if dry_run else "pruned"
    if as_json:
        click.echo(
            json.dumps(
                {
                    "dry_run": dry_run,
                    "pairs": [
                        {"de_path": de, "en_path": en, "rows": rows} for de, en, rows in results
                    ],
                    "total_rows": sum(rows for _de, _en, rows in results),
                },
                indent=2,
            )
        )
        return
    if not results:
        click.echo("no orphaned watermarks.")
        return
    for de_key, _en_key, rows in results:
        click.echo(f"{verb} {rows} row(s) for {_label(de_key)} (orphaned).")
    total = sum(rows for _de, _en, rows in results)
    click.echo("")
    click.echo(f"{verb}: {total} row(s) across {len(results)} orphaned pair(s).")


# ---------------------------------------------------------------------------
# clm slides sync baseline  (the demoted-watermark verb group, epic #440)
#
# Under the agent toolkit the read surface (`report` / `verify`) baselines off git
# HEAD; the watermark is the demoted, opt-in accelerator for `apply`. The `baseline`
# group inspects and maintains it, co-located with `sync` and renamed to reflect that
# role: `show` (was `list`), `clear`, `prune`, plus `bless` — the commit-free baseline
# recorder that replaces `--rebaseline` (#430). The legacy `clm slides watermark`
# group stays as a back-compat alias for the same store.
# ---------------------------------------------------------------------------


@click.group("baseline")
def baseline_group() -> None:
    """Inspect and maintain the sync baseline (the demoted watermark accelerator).

    \b
    show    list baselined pairs (row count, last sync, on-disk status)
    bless   record the current working tree as the baseline (no commit needed)
    clear   drop a pair's baseline so the next sync re-derives off git HEAD
    prune   drop orphan baselines whose files no longer exist
    """


# `show` reuses the `watermark list` implementation verbatim (same store, same shape);
# `clear` / `prune` are shared as-is.
baseline_group.add_command(watermark_list_cmd, name="show")
baseline_group.add_command(watermark_clear_cmd, name="clear")
baseline_group.add_command(watermark_prune_cmd, name="prune")


@baseline_group.command("bless")
@click.argument("deck", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument(
    "en_path",
    required=False,
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory holding the baseline (see `clm slides sync --cache-dir`).",
)
@click.option(
    "--ledger",
    is_flag=True,
    default=False,
    help=(
        "Also record the per-slide consistency ledger (<topic>/.clm/sync-ledger.json, "
        "#448): confirm each localized slide in-sync at the current halves so a later "
        "`report`/`apply --ledger` skips it (no re-litigation) until it drifts."
    ),
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit a JSON report.")
def baseline_bless_cmd(
    deck: Path, en_path: Path | None, cache_dir: Path | None, ledger: bool, as_json: bool
) -> None:
    """Record the current working tree as the sync baseline — no commit needed.

    Replaces ``--rebaseline`` and closes #430: after you reconcile a split pair by hand
    (or with ``apply --no-cache``), ``bless`` snapshots the working tree as the new
    baseline so the next ``report`` / ``apply`` sees it as in sync — **without** the
    throwaway commit ``--rebaseline``'s git-HEAD-no-op gate used to force.

    \b
    ⚠ ``bless`` checks ONLY structure — it does **not** check that the translation is
    correct, nor that the working tree agrees with git ``HEAD``. It records **whatever is
    in the working tree** as the authoritative "in sync" reference, so an unreviewed or
    wrong EN half present at bless time becomes the baseline the next ``report`` reads as
    clean (a weaker gate than the ``--rebaseline`` it replaces, which required a git-HEAD
    no-op). The gate is ``verify``: a structurally corrupt pair (mismatched ids, drifted
    shared cells, header parity break) is **refused**. Review with ``git diff`` and
    confirm the two halves correspond **before** blessing — ``bless`` trusts your
    assertion that they are semantically in sync. DECK is a split half
    (``<deck>.de.py`` / ``<deck>.en.py``) or bilingual stem; single pair only.
    """
    de_path, en_resolved = _resolve_single_path(deck, en_path)
    de_path, en_resolved = _resolve_sync_pair(de_path, en_resolved)
    de_path, en_resolved = de_path.resolve(), en_resolved.resolve()

    verdict = verify_pair(de_path, en_resolved)
    if not verdict.ok:
        _emit_bless_refused(de_path, verdict, as_json=as_json)
        sys.exit(2)

    cache = _open_cache(cache_dir)
    try:
        had = cache.has_pair(str(de_path), str(en_resolved))
        record_baseline(cache, de_path, en_resolved)
    finally:
        cache.close()

    ledger_recorded: int | None = None
    if ledger:
        from clm.slides import sync_ledger

        # bless already passed `verify` above; record_pair re-gates structurally as a
        # safety net and records each localized slide at the current halves.
        result = sync_ledger.record_pair(
            de_path, en_resolved, confirmed_by="bless", confirmed_oracle="structural"
        )
        ledger_recorded = 0 if result.refused else result.recorded

    _emit_bless_done(
        de_path,
        replaced=had,
        warnings=len(verdict.warnings),
        ledger_recorded=ledger_recorded,
        as_json=as_json,
    )


def _emit_bless_refused(de_path: Path, verdict: VerifyResult, *, as_json: bool) -> None:
    errors = [v for v in verdict.violations if v.severity == "error"]
    if as_json:
        click.echo(
            json.dumps(
                {
                    "deck": str(de_path),
                    "mode": "bless",
                    "exit_code": 2,
                    "blessed": False,
                    "reason": "structural verification failed",
                    "errors": [
                        {"kind": v.kind, "message": v.message, "slide_id": v.slide_id}
                        for v in errors
                    ],
                },
                indent=2,
            )
        )
        return
    click.echo(
        f"refusing to bless {de_path.name}: the pair is not a structurally valid split "
        f"({len(errors)} error(s)) — a genuine divergence would be masked. Resolve it "
        "(`clm slides sync report` / `verify`) first:"
    )
    for v in errors:
        click.echo(f"    error [{v.kind}]: {v.message}")


def _emit_bless_done(
    de_path: Path,
    *,
    replaced: bool,
    warnings: int,
    ledger_recorded: int | None = None,
    as_json: bool,
) -> None:
    if as_json:
        payload: dict[str, object] = {
            "deck": str(de_path),
            "mode": "bless",
            "exit_code": 0,
            "blessed": True,
            "replaced": replaced,
            "warnings": warnings,
        }
        if ledger_recorded is not None:
            payload["ledger_recorded"] = ledger_recorded
        click.echo(json.dumps(payload, indent=2))
        return
    verb = "re-recorded" if replaced else "recorded"
    tail = f" ({warnings} verify warning(s) allowed)" if warnings else ""
    led = (
        f" — ledger: {ledger_recorded} slide(s) confirmed in-sync"
        if ledger_recorded is not None
        else ""
    )
    click.echo(f"blessed {de_path.name}: {verb} the working tree as the sync baseline{tail}.{led}")


@baseline_group.command("seed")
@click.argument("deck", type=click.Path(exists=True, path_type=Path))
@click.argument(
    "en_path",
    required=False,
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory holding the baseline/watermark (see `clm slides sync --cache-dir`).",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit a JSON report.")
def baseline_seed_cmd(
    deck: Path, en_path: Path | None, cache_dir: Path | None, as_json: bool
) -> None:
    """Seed the consistency ledger from the existing watermark (#448 — ``--seed``).

    Bootstraps the per-slide ledger for a deck (or every pair under a directory) that
    already has a structural watermark but no ledger: each localized slide inherits the
    watermark's recorded half-hashes and ``synced_commit``, stamped
    ``confirmed_oracle=assume`` (inherited trust, **not** a fresh check). So a legacy
    deck does not cold-start every slide on its first ``--ledger`` run.

    \b
    Safe against a **stale** watermark: the seeded entry carries the watermark hashes,
    so a slide that has drifted since then no longer matches the current halves and
    re-checks on the next ``report``/``apply --ledger`` — never a silent mis-sync.
    **Fill-gaps only** — an existing real confirmation (``bless`` / ``apply``) is never
    downgraded to ``assume``. Gated on a structural ``verify`` of the current pair.

    DECK is a split half (``<deck>.de.py`` / ``<deck>.en.py``), a bilingual stem, or a
    directory (every split pair under it).
    """
    from clm.slides import sync_ledger

    if deck.is_dir():
        if en_path is not None:
            raise click.UsageError(
                f"{deck} is a directory (batch seed), which takes a single directory "
                "argument; do not pass a second path."
            )
        pairs = [(Path(de), Path(en)) for de, en in _resolve_pairs(deck)]
    else:
        de_p, en_p = _resolve_single_path(deck, en_path)
        de_p, en_p = _resolve_sync_pair(de_p, en_p)
        pairs = [(de_p.resolve(), en_p.resolve())]

    cache = _open_cache(cache_dir)
    results: list[tuple[Path, sync_ledger.RecordResult]] = []
    try:
        for de, en in pairs:
            results.append((de, sync_ledger.seed_from_watermark(de, en, cache)))
    finally:
        cache.close()

    seeded = sum(r.recorded for _de, r in results)
    refused = [(de, r) for de, r in results if r.refused]
    exit_code = 2 if refused else 0
    if as_json:
        click.echo(
            json.dumps(
                {
                    "mode": "seed",
                    "exit_code": exit_code,
                    "seeded": seeded,
                    "pairs": [
                        {
                            "deck": str(de),
                            "seeded": r.recorded,
                            "refused": r.refused,
                            "reasons": r.reasons,
                        }
                        for de, r in results
                    ],
                },
                indent=2,
            )
        )
        sys.exit(exit_code)
    for de, r in results:
        if r.refused:
            click.echo(
                f"refused {de.name}: not a structurally valid split — {'; '.join(r.reasons)}"
            )
        elif r.recorded:
            click.echo(
                f"seeded {de.name}: {r.recorded} slide(s) from the watermark (oracle=assume)."
            )
        else:
            click.echo(
                f"{de.name}: nothing to seed (no watermark, or all slides already in the ledger)."
            )
    if len(results) > 1:
        click.echo(f"\nseeded {seeded} slide(s) across {len(results)} pair(s).")
    sys.exit(exit_code)
