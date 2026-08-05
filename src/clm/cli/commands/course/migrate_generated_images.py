"""``clm course migrate-generated-images`` — the #664 layout migration.

Moves committed DrawIO/PlantUML renders from the shared ``<topic>/img/``
namespace into the build-owned ``<topic>/img-generated/`` sibling, so the
invariant *"nothing in ``img/`` is ever written by the build; everything in
``img-generated/`` only ever is"* becomes structural instead of inferred.

Deliberately spec-free: the generated set is derived the same way
``ImageFile.img_path`` derives it — a diagram source at
``<topic>/{drawio,pu}/<stem>.<ext>`` renders to ``<topic>/img*/<sanitized
stem>.{png,svg}`` — so the command covers the whole slides tree, including
topics no spec currently references. Slide references (``img/x.png``) are
output-relative and never rewritten; a correct migration produces a
byte-identical output tree.

Idempotent: an already-migrated render (legacy path gone) is a no-op; a
duplicate (both locations, identical bytes) drops the legacy copy; diverging
bytes are a conflict the command reports and refuses to touch — the build has
been rendering to the legacy location while it existed, so the
``img-generated/`` copy is the suspect one and a human must look.
"""

from __future__ import annotations

from pathlib import Path

import click

from clm.core.utils.path_utils import (
    DIAGRAM_SOURCE_EXTENSIONS,
    GENERATED_IMG_DIR,
    is_ignored_dir_for_course,
)
from clm.core.utils.text_utils import sanitize_path

#: Both formats a repo may have committed over time — a course that switched
#: ``image_format`` can carry a stale render in the other format beside the
#: live one, and both are build-owned.
_RENDER_EXTENSIONS = ("png", "svg")


@click.command("migrate-generated-images")
@click.argument(
    "root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report what would move without touching any file.",
)
def migrate_generated_images_cmd(root: Path, dry_run: bool) -> None:
    """Move committed diagram renders from img/ into img-generated/ (#664).

    ROOT is a course root, a slides directory, or any subtree of one;
    every topic below it is migrated. Idempotent — re-running after a
    partial or completed migration is safe. Exits 1 when a conflict
    (diverging bytes in both locations) needs a human decision.
    """
    # Resolve first: with the default ROOT of ``.`` a shallow relative source
    # path can have too few parents for the topic-dir derivation (a raw
    # IndexError, review finding M1), and the containment check below needs
    # absolute paths on both sides.
    root = root.resolve()
    moved: list[Path] = []
    deduped: list[Path] = []
    conflicts: list[Path] = []
    seen_legacy: set[Path] = set()

    for source in sorted(root.rglob("*")):
        if not source.is_file() or source.suffix not in DIAGRAM_SOURCE_EXTENSIONS:
            continue
        if is_ignored_dir_for_course(source.parent):
            continue
        # ``.claude/`` holds agent state, including LINKED GIT WORKTREES whose
        # ``slides/`` copies belong to other sessions' checkouts — moving
        # files inside them corrupts those checkouts (found the hard way on a
        # course repo carrying ``.claude/worktrees/``). ``is_ignored_dir_for_
        # course`` does not know the directory because the course scan starts
        # below it; this command scans the repo ROOT, so it must.
        if ".claude" in source.parts:
            continue
        topic_dir = source.parents[1]
        # A source directly under ROOT derives ROOT's *parent* as its topic
        # dir; the contract is "every topic below ROOT", so never reach
        # outside it (review finding M2).
        if topic_dir != root and root not in topic_dir.parents:
            continue
        for ext in _RENDER_EXTENSIONS:
            # The exact computation ImageFile.legacy_img_path/generated_img_path
            # perform, so the moved names match what the build looks for.
            legacy = sanitize_path((topic_dir / "img" / source.stem).with_suffix(f".{ext}"))
            # Two sources can share one stem (a ``.pu`` and a ``.drawio``
            # sibling): the render is one file, and counting it once per
            # source made ``--dry-run`` over-report what the real run
            # (whose first move empties the path) would do.
            if legacy in seen_legacy:
                continue
            seen_legacy.add(legacy)
            target = sanitize_path(
                (topic_dir / GENERATED_IMG_DIR / source.stem).with_suffix(f".{ext}")
            )
            if not legacy.exists():
                continue
            if target.exists():
                if target.read_bytes() == legacy.read_bytes():
                    deduped.append(legacy)
                    if not dry_run:
                        legacy.unlink()
                else:
                    conflicts.append(legacy)
                continue
            moved.append(legacy)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                legacy.rename(target)

    prefix = "would move" if dry_run else "moved"
    for path in moved:
        click.echo(f"{prefix}: {path} -> {path.parents[1] / GENERATED_IMG_DIR / path.name}")
    for path in deduped:
        click.echo(
            f"{'would drop' if dry_run else 'dropped'} duplicate legacy copy: {path} "
            f"(byte-identical render already in {GENERATED_IMG_DIR}/)"
        )
    for path in conflicts:
        click.echo(
            f"CONFLICT: {path} and its {GENERATED_IMG_DIR}/ twin differ — the build "
            "rendered to the legacy location while it existed, so the "
            f"{GENERATED_IMG_DIR}/ copy is suspect; resolve by hand and re-run",
            err=True,
        )
    click.echo(
        f"{len(moved)} {prefix}, {len(deduped)} duplicate(s) dropped, "
        f"{len(conflicts)} conflict(s)" + (" [dry run]" if dry_run else "")
    )
    if conflicts:
        raise SystemExit(1)
