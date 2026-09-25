"""``clm slides rename-id`` — rename a ``slide_id`` across a split pair + ledger.

Issue #572. Renaming a ``slide_id`` by hand on a split DE/EN deck drops the
pair's per-topic sync ledger baseline to *cold* for that id — the v3 differ
keys trust by ``id:<slide_id>`` and only ``pos: → id:`` migrations are
recovered, so an ``id: → id:`` rename reads as a cold add. A later edit of the
renamed cell then frames ``verify_cold`` (only answer ``confirm``), which banks
the possibly-stale twin unnoticed.

This command does the rename atomically and design-consistently: it rewrites
the id on **both** halves *and* in their separated voiceover companions
(``for_slide`` owner references, ``vo_anchor`` tokens and a companion cell's
own ``slide_id`` — #990) and **migrates** the ledger baseline key — preserving the recorded fingerprints, so
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

from clm.core.slide_text.pairing import derive_split_pair, order_split_pair, split_lang_tag
from clm.core.utils.prog_lang_utils import comment_token_for_path
from clm.core.voiceover_companions import companion_locations
from clm.slides import doc_ledger
from clm.slides.doc_ledger import deck_key_for, ledger_path_for
from clm.slides.doc_lenses import parse_bundle
from clm.slides.rename_id import (
    CompanionRename,
    RenameResult,
    is_valid_slide_id,
    ledger_knows,
    migrate_ledger_key,
    migrate_reference_fingerprints,
    referenced_ids_in,
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
    The rename is atomic across the pair, its separated voiceover companions
    (``voiceover/voiceover_*.{de,en}.*`` or the sibling layout) AND the committed
    sync ledger: it rewrites the id, every ``for_slide`` owner reference and
    every ``vo_anchor="id:OLD#n"`` token in all four files and migrates the
    ledger baseline key, keeping the member's identity total. An id that lives
    only in the companions (a narration cell's own slide_id) renames the same way. The
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
    """Rewrite both halves (+ their companions) and migrate the ledger for one rename.

    The separated voiceover companions are part of the deck (#990): the sync
    engine inlines them in memory, `validate` checks their ``for_slide``
    against the deck's ids, and the build drops narration whose owner no
    longer resolves — so they are read, rewritten and written together with
    the halves, and their ids count for the presence / collision checks.
    """
    files: dict[str, Path] = {"de": de, "en": en}
    for side, half in (("de", de), ("en", en)):
        locations = companion_locations(half)
        if len(locations) > 1:
            # resolve_companion would silently pick the voiceover/ copy and the
            # sibling would keep the old references (review finding) — the
            # same ambiguity `clm validate` reports; reconcile it first.
            names = ", ".join(str(p) for p in locations)
            raise click.UsageError(
                f"{half.name} has a voiceover companion in both layouts ({names}); "
                "delete or merge one copy (see `clm validate`) before renaming."
            )
        if locations:
            files[f"{side}_companion"] = locations[0]
    texts = {name: path.read_text(encoding="utf-8") for name, path in files.items()}
    tokens = {name: comment_token_for_path(path) for name, path in files.items()}
    with_companions = len(files) > 2

    present: set[str] = set()
    referenced: set[str] = set()
    for name, text in texts.items():
        present |= slide_ids_in(text, tokens[name])
        referenced |= referenced_ids_in(text, tokens[name])
    # Repoint mode (#990): the deck already carries NEW (a hand rename) and
    # OLD survives only as dangling for_slide / vo_anchor references — the
    # state `sync report` frames as broken_owner. Then the rename re-points
    # the references and migrates whatever the ledger still keys on OLD,
    # instead of refusing with "no cell carries OLD".
    repoint = old not in present and old in referenced and new in present
    if not repoint:
        if old not in present:
            raise click.UsageError(
                f'no cell carries slide_id="{old}" in either half of {de.name} / {en.name}'
                + (" (or their voiceover companions)." if with_companions else ".")
            )
        if new in present:
            raise click.UsageError(
                f'slide_id "{new}" already exists in the pair'
                + (" or its voiceover companions" if with_companions else "")
                + " — renaming to it would create a duplicate id. Choose an unused id."
            )

    outputs: dict[str, tuple[str, int, int, int]] = {
        name: rename_in_half(text, tokens[name], old, new) for name, text in texts.items()
    }
    de_out, de_sid, de_fs, de_va = outputs["de"]
    en_out, en_sid, en_fs, en_va = outputs["en"]
    companions: dict[str, CompanionRename | None] = {"de": None, "en": None}
    for side in ("de", "en"):
        name = f"{side}_companion"
        if name in outputs:
            _, c_sid, c_fs, c_va = outputs[name]
            companions[side] = CompanionRename(str(files[name]), c_sid, c_fs, c_va)

    # Ledger migration is keyed off the DE half's topic; a deck with no recorded
    # baseline (cold / never recorded) simply has nothing to migrate.
    ledger_path = ledger_path_for(de)
    ledger = doc_ledger.load(ledger_path)
    deck_ledger = ledger.decks.get(deck_key_for(de))
    ledger_migrated = deck_ledger is not None and migrate_ledger_key(
        deck_ledger,
        old,
        new,
        # A repoint onto an id the ledger already records must not clobber
        # that baseline with OLD's — only the references move.
        references_only=repoint and ledger_knows(deck_ledger, new),
    )
    if deck_ledger is not None:
        # The recorded fingerprints cover the for_slide / vo_anchor bytes the
        # rename rewrote (#990): carry them across the rewrite for cells that
        # sit on their baseline, so a pure rename stays clean. Both parses see
        # the same bundle the sync verbs read (halves + companions).
        before = parse_bundle(
            texts["de"],
            texts["en"],
            texts.get("de_companion"),
            texts.get("en_companion"),
            comment_token=tokens["de"],
        ).deck
        after = parse_bundle(
            outputs["de"][0],
            outputs["en"][0],
            outputs["de_companion"][0] if "de_companion" in outputs else None,
            outputs["en_companion"][0] if "en_companion" in outputs else None,
            comment_token=tokens["de"],
        ).deck
        if before is not None and after is not None:
            if migrate_reference_fingerprints(deck_ledger, before, after):
                ledger_migrated = True

    if write:
        for name, path in files.items():
            out = outputs[name][0]
            if out != texts[name]:
                path.write_text(out, encoding="utf-8", newline="\n")
        if ledger_migrated:
            doc_ledger.save(ledger, ledger_path)

    de_c, en_c = companions["de"], companions["en"]
    return RenameResult(
        old=old,
        new=new,
        # Per-side totals span the half AND its companion — the numbers an
        # agent reads to confirm nothing was left behind.
        de_slide_id_hits=de_sid + (de_c.slide_id_hits if de_c else 0),
        en_slide_id_hits=en_sid + (en_c.slide_id_hits if en_c else 0),
        de_for_slide_hits=de_fs + (de_c.for_slide_hits if de_c else 0),
        en_for_slide_hits=en_fs + (en_c.for_slide_hits if en_c else 0),
        ledger_migrated=ledger_migrated,
        de_vo_anchor_hits=de_va + (de_c.vo_anchor_hits if de_c else 0),
        en_vo_anchor_hits=en_va + (en_c.vo_anchor_hits if en_c else 0),
        repointed=repoint,
        de_companion=de_c,
        en_companion=en_c,
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
        "vo_anchor_hits": {"de": result.de_vo_anchor_hits, "en": result.en_vo_anchor_hits},
        "companions": {
            "de": _companion_dict(result.de_companion),
            "en": _companion_dict(result.en_companion),
        },
        "ledger_migrated": result.ledger_migrated,
        "repointed": result.repointed,
        "report_only": report_only,
    }


def _companion_dict(companion: CompanionRename | None) -> dict | None:
    if companion is None:
        return None
    return {
        "path": companion.path,
        "slide_id_hits": companion.slide_id_hits,
        "for_slide_hits": companion.for_slide_hits,
        "vo_anchor_hits": companion.vo_anchor_hits,
    }


def _print_human(de: Path, en: Path, result: RenameResult, *, report_only: bool) -> None:
    verb = "would rename" if report_only else "renamed"
    if result.repointed:
        verb = "would re-point" if report_only else "re-pointed"
    click.echo(f'{deck_key_for(de)}: {verb} slide_id "{result.old}" → "{result.new}"')
    if result.repointed:
        click.echo(
            f'  repoint: the deck already carries slide_id="{result.new}" — only the '
            f'dangling references to "{result.old}" were rewritten'
        )
    click.echo(
        f"  slide_id: DE {result.de_slide_id_hits}, EN {result.en_slide_id_hits}"
        f"  |  for_slide: DE {result.de_for_slide_hits}, EN {result.en_for_slide_hits}"
        f"  |  vo_anchor: DE {result.de_vo_anchor_hits}, EN {result.en_vo_anchor_hits}"
    )
    for side, companion in (("DE", result.de_companion), ("EN", result.en_companion)):
        if companion is None:
            continue
        click.echo(
            f"  {side} companion {Path(companion.path).name}: slide_id {companion.slide_id_hits},"
            f" for_slide {companion.for_slide_hits}, vo_anchor {companion.vo_anchor_hits}"
        )
    if result.ledger_migrated:
        click.echo(f"  ledger: baseline migrated{' (dry-run)' if report_only else ''}")
    else:
        click.echo("  ledger: no baseline for this id (cold / not recorded) — nothing to migrate")


def _fail(message: str) -> None:
    click.echo(json.dumps({"error": message}, indent=2))
    sys.exit(2)
