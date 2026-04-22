"""``clm jupyterlite`` command group.

Provides ``clm jupyterlite preview <target>`` for locally serving
a previously built JupyterLite site. Requires the ``[jupyterlite]``
extra only for the actual build — preview itself is pure stdlib.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.group("jupyterlite")
def jupyterlite_group():
    """JupyterLite site management commands."""


@jupyterlite_group.command()
@click.argument("spec_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--target",
    required=True,
    help="Target name (as defined in the course spec).",
)
@click.option(
    "--kind",
    default=None,
    help="Output kind (code-along, completed, speaker, partial). Auto-detected if only one.",
)
@click.option(
    "--language",
    default=None,
    help="Language code (e.g. 'en', 'de'). Auto-detected if only one.",
)
def preview(spec_file: Path, target: str, kind: str | None, language: str | None) -> None:
    """Serve a previously built JupyterLite site locally.

    SPEC_FILE is the course spec XML file. The command locates the most
    recently built JupyterLite site for the given target and starts a
    local server.
    """
    from clm.core.course import Course
    from clm.core.course_spec import CourseSpec

    course_spec = CourseSpec.from_file(spec_file)
    course = Course.from_spec(course_spec, spec_file.parent, output_root=None)

    matching_targets = [t for t in course.output_targets if t.name == target]
    if not matching_targets:
        available = [t.name for t in course.output_targets]
        raise click.ClickException(f"No target named {target!r}. Available: {available}")
    output_target = matching_targets[0]

    if "jupyterlite" not in output_target.formats:
        raise click.ClickException(
            f"Target {target!r} does not include 'jupyterlite' in its formats."
        )

    site_dirs = _find_site_dirs(output_target.output_root, kind=kind, language=language)
    if not site_dirs:
        raise click.ClickException(
            f"No built JupyterLite site found for target {target!r}. Run 'clm build' first."
        )
    if len(site_dirs) > 1:
        click.echo("Multiple sites found:")
        for i, sd in enumerate(site_dirs, 1):
            click.echo(f"  {i}. {sd}")
        raise click.ClickException("Specify --kind and/or --language to narrow down to one site.")

    site_dir = site_dirs[0]
    launch_py = site_dir.parent / "launch.py"
    if launch_py.is_file():
        click.echo(f"Launching site from {site_dir}")
        subprocess.run([sys.executable, str(launch_py)], check=True)
    else:
        launch_bat = site_dir.parent / "launch.bat"
        launch_sh = site_dir.parent / "launch.sh"
        if launch_bat.is_file() or launch_sh.is_file():
            click.echo(f"Site at {site_dir.parent} uses miniserve launcher.")
            click.echo("Run the appropriate launcher for your OS:")
            if launch_bat.is_file():
                click.echo(f"  Windows: {launch_bat}")
            if launch_sh.is_file():
                click.echo(f"  Linux:   {launch_sh}")
            launch_cmd = site_dir.parent / "launch.command"
            if launch_cmd.is_file():
                click.echo(f"  macOS:   {launch_cmd}")
        else:
            raise click.ClickException(
                f"No launcher found in {site_dir.parent}. Rebuild with launcher enabled."
            )


def _find_site_dirs(
    output_root: Path,
    *,
    kind: str | None,
    language: str | None,
) -> list[Path]:
    """Walk the output tree for JupyterLite ``_output/`` directories.

    The expected layout is::

        <output_root>/<course-dir>/<Slides>/JupyterLite/<Kind>/_output/

    Optionally filter by kind and language.
    """
    results: list[Path] = []
    jupyterlite_glob = output_root.glob("**/JupyterLite/*/_output")
    for site_output in jupyterlite_glob:
        index = site_output / "index.html"
        if not index.is_file():
            continue
        kind_dir = site_output.parent
        if kind and kind_dir.name.lower() != kind.lower().replace("-", ""):
            normalized_kind = kind.replace("-", "").lower()
            if kind_dir.name.lower() != normalized_kind:
                continue
        results.append(site_output)

    if language and results:
        filtered = [r for r in results if f"/{language}/" in r.as_posix().lower()]
        if filtered:
            results = filtered

    results.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return results
