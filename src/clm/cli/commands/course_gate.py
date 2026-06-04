"""``clm course gate`` — run the mechanical conversion passes and report readiness.

Course-conversion tooling gap #3. Delegates to
:func:`clm.slides.course_gate.run_course_gate`; see that module for the
mechanical-vs-needs-author split.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.slides.course_gate import (
    DEFAULT_GATE_OPERATIONS,
    render_report,
    run_course_gate,
)


def _slides_dir_for_target(target: Path, data_dir: Path | None) -> Path:
    """Resolve the ``slides/`` directory for a spec or a slides path."""
    if data_dir is not None:
        return data_dir / "slides"
    if target.is_file() and target.suffix.lower() == ".xml":
        from clm.core.course_paths import resolve_course_paths

        course_root, _ = resolve_course_paths(target, None)
        return course_root / "slides"
    # A slides path: walk up to the 'slides' ancestor (or the dir itself).
    resolved = target.resolve()
    if resolved.name == "slides":
        return resolved
    for parent in resolved.parents:
        if parent.name == "slides":
            return parent
    raise click.ClickException(
        "Could not locate the slides/ directory from the path. Pass --data-dir explicitly."
    )


@click.command("gate")
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Write the mechanical fixes and re-validate. Without it, dry-run: report "
    "what would change without touching disk.",
)
@click.option(
    "--operations",
    default=None,
    help="Comma-separated mechanical passes to run "
    f"(default: {','.join(DEFAULT_GATE_OPERATIONS)}). "
    "Valid: tag_migration, workshop_tags, interleaving, slide_ids.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Course data directory (contains slides/). Default: inferred from the target.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def course_gate_cmd(
    target: Path,
    apply_changes: bool,
    operations: str | None,
    data_dir: Path | None,
    as_json: bool,
) -> None:
    """Run the mechanical conversion passes over a course and report readiness.

    TARGET is a course spec ``.xml`` (uses its shipping set) or a slides
    directory. The gate runs tag migration, DE/EN interleaving, and
    content-derived ``slide_id`` minting, then splits the remaining work into
    what those passes cleared mechanically versus what still needs an author
    (untranslatable ids, diverged DE/EN code, missing translations).

    Exits non-zero when author work — or, after ``--apply``, a residual error —
    remains, so it can gate a conversion in CI.

    \b
    Examples:
        clm course gate course-specs/python.xml             # dry-run report
        clm course gate course-specs/python.xml --apply     # fix + re-validate
        clm course gate slides/module_100/ --apply
        clm course gate course-specs/python.xml --json
    """
    slides_dir = _slides_dir_for_target(target, data_dir)
    op_list = [o.strip() for o in operations.split(",") if o.strip()] if operations else None

    try:
        report = run_course_gate(
            target,
            slides_dir,
            operations=op_list,
            apply=apply_changes,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from None

    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2))
    else:
        for line in render_report(report):
            click.echo(line)

    if not report.is_clean:
        raise SystemExit(1)
