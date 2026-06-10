"""``clm slides tidy`` — relocate authoring sidecars to (or from) subdirectories.

Wraps :func:`clm.slides.tidy.plan_tidy` / :func:`~clm.slides.tidy.apply_tidy`.
Moves voiceover companions into ``voiceover/`` and cassettes into ``cassettes/``
(``--layout subdir``, default), or flattens them back (``--layout sibling``);
deletes transient cassette staging markers. Operates on a single file, a topic
directory, or a whole course tree (recursive).

Exit codes:

- ``0`` — done (or, under ``--dry-run``, the plan was printed)
- ``2`` — one or more sidecars exist in *both* layouts; those moves were skipped
  so nothing was clobbered. Reconcile the duplicates and re-run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.slides.tidy import TidyPlan, apply_tidy, plan_tidy


@click.command("tidy")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--layout",
    type=click.Choice(["subdir", "sibling"]),
    default="subdir",
    show_default=True,
    help="Target layout. 'subdir' moves sidecars into voiceover/ and cassettes/ "
    "folders; 'sibling' flattens them back next to the slides.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show the planned moves/deletes without touching any file.",
)
@click.option(
    "--voiceover/--no-voiceover",
    "do_voiceover",
    default=True,
    help="Include voiceover companions (default: yes).",
)
@click.option(
    "--cassettes/--no-cassettes",
    "do_cassettes",
    default=True,
    help="Include cassettes and prune transient staging markers (default: yes).",
)
@click.option(
    "--no-git",
    "no_git",
    is_flag=True,
    help="Use plain file moves instead of 'git mv' for tracked files.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def tidy_cmd(
    path: Path,
    layout: str,
    dry_run: bool,
    do_voiceover: bool,
    do_cassettes: bool,
    no_git: bool,
    as_json: bool,
) -> None:
    """Relocate authoring sidecars under PATH to (or from) subdirectories.

    \b
    PATH may be a single slide/sidecar file, a topic directory, or a whole
    course tree (walked recursively).

    \b
    Examples:
        clm slides tidy slides/module_550/topic_070 --dry-run
        clm slides tidy slides/module_550/topic_070            # -> subdir layout
        clm slides tidy slides --layout sibling                # flatten a whole tree
        clm slides tidy slides/module_550 --no-cassettes       # voiceover only
    """
    plan = plan_tidy(path, layout=layout, do_voiceover=do_voiceover, do_cassettes=do_cassettes)
    if not dry_run:
        apply_tidy(plan, use_git=not no_git)

    if as_json:
        click.echo(json.dumps(_to_dict(plan, layout=layout, dry_run=dry_run), indent=2))
    else:
        _print_human(plan, layout=layout, dry_run=dry_run)

    if plan.conflicts:
        sys.exit(2)


def _print_human(plan: TidyPlan, *, layout: str, dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    if plan.is_noop and not plan.conflicts:
        click.echo(f"{prefix}already tidy ({layout} layout) — nothing to do.")
        return
    move_verb = "would move" if dry_run else "moved"
    del_verb = "would delete" if dry_run else "deleted"
    for mv in plan.moves:
        click.echo(f"{prefix}{move_verb} {mv.src} -> {mv.dst}")
    for f in plan.deletes:
        click.echo(f"{prefix}{del_verb} {f} (transient staging marker)")
    for d in plan.removed_dirs:
        click.echo(f"{prefix}removed empty {d}")
    for src, dst in plan.conflicts:
        click.echo(
            f"{prefix}CONFLICT: {dst} already exists; left {src} in place "
            f"(reconcile the duplicate, then re-run)",
            err=True,
        )
    n_moved = len(plan.moves)
    n_deleted = len(plan.deletes)
    summary = "would change" if dry_run else "changed"
    click.echo(
        f"{prefix}{summary}: {n_moved} move(s), {n_deleted} deletion(s), "
        f"{len(plan.conflicts)} conflict(s)."
    )


def _to_dict(plan: TidyPlan, *, layout: str, dry_run: bool) -> dict[str, object]:
    return {
        "layout": layout,
        "dry_run": dry_run,
        "moves": [{"src": str(m.src), "dst": str(m.dst), "kind": m.kind} for m in plan.moves],
        "deletes": [str(f) for f in plan.deletes],
        "conflicts": [{"src": str(s), "dst": str(d)} for s, d in plan.conflicts],
        "removed_dirs": [str(d) for d in plan.removed_dirs],
    }
