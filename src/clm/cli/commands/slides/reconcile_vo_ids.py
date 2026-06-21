"""``clm slides reconcile-vo-ids`` — symmetrize voiceover ids across a split pair.

Issue #403 fix #3. When a deck's two halves (``<deck>.de.<ext>`` / ``<deck>.en.<ext>``)
disagree on whether their paired voiceover / notes cells carry a ``slide_id`` — one half
id-less, the other id'd — this command makes them agree, *safely*. It pairs the halves'
narratives by the same occurrence-under-slide identity ``clm slides sync`` uses (NOT the
per-file slug ``assign-ids`` derives, which diverges DE/EN), then strips or stamps the
``slide_id`` so both halves share one convention.

Exit codes:

- ``0`` — reconciled (or already symmetric / would reconcile in ``--report-only``)
- ``2`` — usage error (no twin found, bad path)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.pairing import (
    derive_split_pair,
    derive_split_twin,
    find_split_slide_files_recursive,
    iter_split_pairs,
    split_lang_tag,
)
from clm.slides.reconcile_vo_ids import (
    TO_IDLESS,
    TO_IDS,
    ReconcileResult,
    reconcile_voiceover_ids,
)


@click.command("reconcile-vo-ids")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.argument(
    "en_path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--to",
    "direction",
    type=click.Choice([TO_IDLESS, TO_IDS]),
    default=TO_IDLESS,
    show_default=True,
    help=(
        "Which convention to resolve an asymmetric voiceover pair toward: "
        f"'{TO_IDLESS}' strips the id'd side (the engine's canonical, collision-proof "
        f"form), '{TO_IDS}' stamps the id'd side's existing id onto the id-less side."
    ),
)
@click.option(
    "--report-only",
    "--dry-run",
    "report_only",
    is_flag=True,
    help="Report what would change without modifying files.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def reconcile_vo_ids_cmd(
    path: Path,
    en_path: Path | None,
    direction: str,
    report_only: bool,
    as_json: bool,
) -> None:
    """Symmetrize voiceover/notes slide_ids between the two halves of a split deck.

    \b
    PATH is one half of a split pair (``<deck>.de.<ext>`` / ``<deck>.en.<ext>`` — the
    twin is found on disk), both halves passed explicitly, or a directory (every split
    pair under it is reconciled).

    \b
    Only *paired* narratives that disagree on id-ness are touched — a pair that already
    agrees (both id-less or both id'd) is left alone, and a narrative present on one half
    only is left to ``clm slides sync``. Unlike ``assign-ids`` (which slugs per file and
    so mints divergent ids on the two halves), this never derives an id from content:
    it strips, or copies the twin's existing id, so the halves can never diverge.
    """
    try:
        pairs = _resolve_pairs(path, en_path)
    except click.UsageError as exc:
        # Honor the --json contract for usage errors too: emit the {"error": …}
        # envelope (exit 2) instead of click's plain-text rendering; otherwise
        # re-raise so click prints the usual message.
        if as_json:
            _fail(exc.format_message(), as_json)
        raise

    results: list[tuple[Path, Path, ReconcileResult]] = []
    for de, en in pairs:
        results.append(
            (de, en, _reconcile_pair(de, en, direction=direction, write=not report_only))
        )

    if as_json:
        click.echo(
            json.dumps(_to_dict(results, direction=direction, report_only=report_only), indent=2)
        )
    else:
        _print_human(results, report_only=report_only)


def _resolve_pairs(path: Path, en_path: Path | None) -> list[tuple[Path, Path]]:
    """Resolve the CLI arguments to an ordered list of ``(de, en)`` pairs."""
    if path.is_dir():
        if en_path is not None:
            raise click.UsageError(
                f"{path} is a directory (batch mode); do not pass a second path."
            )
        pairs, solos = iter_split_pairs(find_split_slide_files_recursive(path))
        # A stranded half (no twin under the tree) is itself an asymmetry this command
        # exists to surface — warn and skip it, the same as `clm slides sync`, rather
        # than silently reporting only the healthy pairs.
        for solo in solos:
            other = "EN" if split_lang_tag(solo) == "de" else "DE"
            click.echo(
                f"warning: skipping {solo.name} — no {other} twin found under {path}.", err=True
            )
        if not pairs:
            raise click.UsageError(f"no split deck pairs found under {path}")
        return pairs

    if en_path is not None:
        # Resolve both halves so a mixed absolute/relative spelling of the same two
        # files compares equal (Path compares by string form, not by target).
        path, en_path = path.resolve(), en_path.resolve()
        pair = derive_split_pair(path) or derive_split_pair(en_path)
        if pair is None or {pair[0], pair[1]} != {path, en_path}:
            raise click.UsageError(
                f"{path.name} and {en_path.name} are not the two halves of one split deck."
            )
        return [pair]

    tag = split_lang_tag(path)
    if tag is None:
        raise click.UsageError(
            f"{path.name} has no .de/.en language tag; pass a split half "
            "(<deck>.de.<ext>), both halves, or a directory."
        )
    twin = derive_split_twin(path)
    if twin is None:
        other = "EN" if tag == "de" else "DE"
        raise click.UsageError(
            f"no {other} twin found next to {path.name}; pass both halves explicitly."
        )
    return [(path, twin) if tag == "de" else (twin, path)]


def _reconcile_pair(de: Path, en: Path, *, direction: str, write: bool) -> ReconcileResult:
    de_text = de.read_text(encoding="utf-8")
    en_text = en.read_text(encoding="utf-8")
    de_out, en_out, result = reconcile_voiceover_ids(
        de_text,
        en_text,
        comment_token_for_path(de),
        comment_token_for_path(en),
        direction=direction,
    )
    if write:
        if de_out != de_text:
            de.write_text(de_out, encoding="utf-8", newline="\n")
        if en_out != en_text:
            en.write_text(en_out, encoding="utf-8", newline="\n")
    return result


def _fail(message: str, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps({"error": message}, indent=2))
    else:
        click.echo(f"error: {message}", err=True)
    sys.exit(2)


def _print_human(results: list[tuple[Path, Path, ReconcileResult]], *, report_only: bool) -> None:
    prefix = "[report-only] " if report_only else ""
    verb = "would change" if report_only else "changed"
    total = 0
    for de, _en, result in results:
        if result.is_noop:
            note = "already symmetric" if result.already_symmetric else "no paired narratives"
            click.echo(f"{de.name}: {note}")
        else:
            total += len(result.changes)
            click.echo(f"{prefix}{de.name}: {verb} {len(result.changes)} voiceover id(s)")
            for c in result.changes:
                if c.action == "strip":
                    verbed = "would strip id from" if report_only else "stripped id from"
                else:
                    did = "would stamp" if report_only else "stamped"
                    verbed = f"{did} id={c.new_id!r} onto"
                click.echo(
                    f"    {c.lang} L{c.line_number} {c.role} under "
                    f"{c.owning_slide_id!r}#{c.occurrence}: {verbed} it"
                )
        # Reported for every pair (including a no-op one), so an unpaired narrative —
        # which `sync` must handle — is never hidden, matching the --json output.
        if result.unpaired:
            click.echo(
                f"    ({result.unpaired} narrative(s) present on one half only — "
                "left to `clm slides sync`)"
            )
    if len(results) > 1:
        click.echo(f"{prefix}{len(results)} pair(s), {total} voiceover id(s) {verb}")


def _to_dict(
    results: list[tuple[Path, Path, ReconcileResult]], *, direction: str, report_only: bool
) -> dict[str, object]:
    return {
        "direction": direction,
        "report_only": report_only,
        "pairs": [
            {
                "de": str(de),
                "en": str(en),
                "changes": [
                    {
                        "lang": c.lang,
                        "line_number": c.line_number,
                        "role": c.role,
                        "owning_slide_id": c.owning_slide_id,
                        "occurrence": c.occurrence,
                        "action": c.action,
                        "old_id": c.old_id,
                        "new_id": c.new_id,
                    }
                    for c in result.changes
                ],
                "unpaired": result.unpaired,
                "already_symmetric": result.already_symmetric,
            }
            for de, en, result in results
        ],
    }
