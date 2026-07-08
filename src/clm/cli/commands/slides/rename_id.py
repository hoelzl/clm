"""``clm slides rename-id`` — rename a ``slide_id`` across a split pair + ledger.

Issue #572. Renaming a ``slide_id`` by hand on a split DE/EN deck drops the
pair's per-topic sync ledger baseline to *cold* for that id — the v3 differ
keys trust by ``id:<slide_id>`` and only ``pos: → id:`` migrations are
recovered, so an ``id: → id:`` rename reads as a cold add. A later edit of the
renamed cell then frames ``verify_cold`` (only answer ``confirm``), which banks
the possibly-stale twin unnoticed.

This command does the rename atomically and design-consistently: it rewrites
the id on **both** halves (and every ``for_slide`` owner reference) and
**migrates** the ledger baseline key — preserving the recorded fingerprints, so
a simultaneous content edit surfaces as ``translate_edit`` on the next report,
never a silent cold-``confirm``. See :mod:`clm.slides.rename_id`.

Exit codes: ``0`` renamed (or would-rename in ``--report-only``); ``2`` usage
error (no such id, collision, no twin, bad path).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides import doc_ledger
from clm.slides.doc_ledger import deck_key_for, ledger_path_for
from clm.slides.pairing import derive_split_pair, order_split_pair, split_lang_tag
from clm.slides.rename_id import (
    RenameResult,
    is_valid_slide_id,
    migrate_ledger_key,
    rename_in_half,
    slide_ids_in,
)


@click.command("rename-id")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("old")
@click.argument("new")
@click.argument(
    "en_path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--report-only",
    "--dry-run",
    "report_only",
    is_flag=True,
    help="Report what would change without modifying files or the ledger.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def rename_id_cmd(
    path: Path,
    old: str,
    new: str,
    en_path: Path | None,
    report_only: bool,
    as_json: bool,
) -> None:
    """Rename a slide_id from OLD to NEW across both halves of a split deck.

    \b
    PATH is one half of a split pair (``<deck>.de.<ext>`` / ``<deck>.en.<ext>``
    — the twin is found on disk) or both halves passed explicitly. OLD is the
    current slide_id; NEW is the replacement.

    \b
    The rename is atomic across the pair AND the committed sync ledger: it
    rewrites the id (and every ``for_slide`` owner reference) on both halves and
    migrates the ledger baseline key, keeping the member's identity total. The
    baseline is migrated, never re-fingerprinted — so if you renamed *and*
    edited the same cell, the next ``clm slides sync report`` frames a
    ``translate_edit`` against the carried baseline instead of banking the stale
    twin under a cold ``verify_cold`` (issue #572). A manual rename that resets
    the deck to cold is exactly the footgun this command removes.
    """
    try:
        de, en = _resolve_pair(path, en_path)
        old_bare, new_bare = _validate(old, new)
        result = _rename_pair(de, en, old_bare, new_bare, write=not report_only)
    except click.UsageError as exc:
        # Honor the --json contract for usage errors too: emit {"error": …}
        # (exit 2) rather than click's plain text; else re-raise for click.
        if as_json:
            _fail(exc.format_message())
        raise

    if as_json:
        click.echo(json.dumps(_to_dict(de, en, result, report_only=report_only), indent=2))
    else:
        _print_human(de, en, result, report_only=report_only)
    sys.exit(0)


def _resolve_pair(path: Path, en_path: Path | None) -> tuple[Path, Path]:
    """Resolve the argument(s) to an ordered ``(de, en)`` split pair."""
    if en_path is not None:
        pair = order_split_pair(path.resolve(), en_path.resolve())
        if pair is None:
            raise click.UsageError(
                f"{path.name} and {en_path.name} are not the two halves of one split deck."
            )
        return pair
    tag = split_lang_tag(path)
    if tag is None:
        raise click.UsageError(
            f"{path.name} has no .de/.en language tag; pass a split half "
            "(<deck>.de.<ext>) or both halves explicitly."
        )
    pair = derive_split_pair(path)
    if pair is None:
        other = "EN" if tag == "de" else "DE"
        raise click.UsageError(
            f"no {other} twin found next to {path.name}; pass both halves explicitly."
        )
    return pair


def _validate(old: str, new: str) -> tuple[str, str]:
    """Bare-id, sanity-check the OLD/NEW arguments."""
    old_bare = old[1:] if old.startswith("!") else old
    new_bare = new[1:] if new.startswith("!") else new
    if not old_bare or not new_bare:
        raise click.UsageError("OLD and NEW must be non-empty slide_ids.")
    if old_bare == new_bare:
        raise click.UsageError(f'OLD and NEW are the same id ("{old_bare}") — nothing to rename.')
    if not is_valid_slide_id(new_bare):
        raise click.UsageError(
            f'"{new_bare}" is not a usable slide_id (no whitespace or double-quotes).'
        )
    return old_bare, new_bare


def _rename_pair(de: Path, en: Path, old: str, new: str, *, write: bool) -> RenameResult:
    """Rewrite both halves + migrate the ledger for one id rename."""
    de_text = de.read_text(encoding="utf-8")
    en_text = en.read_text(encoding="utf-8")
    de_token = comment_token_for_path(de)
    en_token = comment_token_for_path(en)

    present = slide_ids_in(de_text, de_token) | slide_ids_in(en_text, en_token)
    if old not in present:
        raise click.UsageError(
            f'no cell carries slide_id="{old}" in either half of {de.name} / {en.name}.'
        )
    if new in present:
        raise click.UsageError(
            f'slide_id "{new}" already exists in the pair — renaming to it would create a '
            "duplicate id. Choose an unused id."
        )

    de_out, de_sid, de_fs = rename_in_half(de_text, de_token, old, new)
    en_out, en_sid, en_fs = rename_in_half(en_text, en_token, old, new)

    # Ledger migration is keyed off the DE half's topic; a deck with no recorded
    # baseline (cold / never recorded) simply has nothing to migrate.
    ledger_path = ledger_path_for(de)
    ledger = doc_ledger.load(ledger_path)
    deck_ledger = ledger.decks.get(deck_key_for(de))
    ledger_migrated = deck_ledger is not None and migrate_ledger_key(deck_ledger, old, new)

    if write:
        if de_out != de_text:
            de.write_text(de_out, encoding="utf-8", newline="\n")
        if en_out != en_text:
            en.write_text(en_out, encoding="utf-8", newline="\n")
        if ledger_migrated:
            doc_ledger.save(ledger, ledger_path)

    return RenameResult(
        old=old,
        new=new,
        de_slide_id_hits=de_sid,
        en_slide_id_hits=en_sid,
        de_for_slide_hits=de_fs,
        en_for_slide_hits=en_fs,
        ledger_migrated=ledger_migrated,
    )


def _to_dict(de: Path, en: Path, result: RenameResult, *, report_only: bool) -> dict:
    return {
        "deck": deck_key_for(de),
        "de_path": str(de),
        "en_path": str(en),
        "old": result.old,
        "new": result.new,
        "slide_id_hits": {"de": result.de_slide_id_hits, "en": result.en_slide_id_hits},
        "for_slide_hits": {"de": result.de_for_slide_hits, "en": result.en_for_slide_hits},
        "ledger_migrated": result.ledger_migrated,
        "report_only": report_only,
    }


def _print_human(de: Path, en: Path, result: RenameResult, *, report_only: bool) -> None:
    verb = "would rename" if report_only else "renamed"
    click.echo(f'{deck_key_for(de)}: {verb} slide_id "{result.old}" → "{result.new}"')
    click.echo(
        f"  slide_id: DE {result.de_slide_id_hits}, EN {result.en_slide_id_hits}"
        f"  |  for_slide: DE {result.de_for_slide_hits}, EN {result.en_for_slide_hits}"
    )
    if result.ledger_migrated:
        click.echo(f"  ledger: baseline migrated{' (dry-run)' if report_only else ''}")
    else:
        click.echo("  ledger: no baseline for this id (cold / not recorded) — nothing to migrate")


def _fail(message: str) -> None:
    click.echo(json.dumps({"error": message}, indent=2))
    sys.exit(2)
