"""``clm slides cpp-show`` — rewrite bare C++ display expressions to ``SHOW(expr);``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.slides.cpp_show import DeckRewrite, iter_cpp_slide_files, rewrite_deck_file


@click.command("cpp-show")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Report what would change without writing.")
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
@click.option("-v", "--verbose", is_flag=True, help="List every rewritten expression.")
def cpp_show_cmd(paths: tuple[Path, ...], dry_run: bool, as_json: bool, verbose: bool) -> None:
    """Rewrite bare display expressions of C++ decks to ``SHOW(expr);``.

    The xeus-cpp kernel prints nothing for a cell that is not terminated by
    ``;``, so a deck shows values through the ``SHOW`` macro of
    ``clm/display.hpp`` instead of a bare expression. For every ``*.cpp``
    slide file under PATHS, each bare expression or call the classifier
    reports becomes ``SHOW(<expr>);`` (comments kept in place) and the deck
    gains ``#include <clm/display.hpp>``. Cells tagged ``global`` are left
    alone and reported: ``SHOW`` at namespace scope is ill-formed. Running
    the command twice changes nothing.
    """
    report: list[dict[str, object]] = []
    total = 0
    files_changed = 0
    warnings = 0
    for path in iter_cpp_slide_files(paths):
        result: DeckRewrite = rewrite_deck_file(path, dry_run=dry_run)
        if not result.changed and not result.skipped_global and not result.unmatched:
            continue
        total += len(result.rewrites)
        files_changed += 1 if result.changed else 0
        warnings += len(result.skipped_global) + len(result.unmatched)
        report.append(
            {
                "file": str(path),
                "rewrites": [
                    {"line": r.line_number, "before": r.before, "after": r.after}
                    for r in result.rewrites
                ],
                "include_added": result.include_added,
                "skipped_global": [
                    {"line": line, "expr": expr} for line, expr in result.skipped_global
                ],
                "unmatched": [{"line": line, "expr": expr} for line, expr in result.unmatched],
            }
        )
        if as_json:
            continue
        verb = "would rewrite" if dry_run else "rewrote"
        click.echo(
            f"{path}: {verb} {len(result.rewrites)} display(s)"
            + (", include added" if result.include_added else "")
        )
        if verbose:
            for r in result.rewrites:
                click.echo(f"    L{r.line_number}: {r.before!r} -> {r.after!r}")
        for line, expr in result.skipped_global:
            click.echo(f"    WARNING L{line}: display in a `global` cell left as is: {expr!r}")
        for line, expr in result.unmatched:
            click.echo(f"    WARNING L{line}: could not locate verbatim, left as is: {expr!r}")
    if as_json:
        click.echo(json.dumps({"dry_run": dry_run, "files": report}, indent=2, ensure_ascii=False))
    else:
        verb = "would change" if dry_run else "changed"
        click.echo(f"{total} display(s) in {files_changed} file(s) {verb}; {warnings} warning(s)")
    if warnings:
        sys.exit(1)
