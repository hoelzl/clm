"""``clm provision`` command group.

Prepares environments clm runs *against* (as opposed to clm's own venv). Today
it registers a course-runtime kernel environment for Direct-mode notebook
execution (Wave 2b); the group is intentionally open-ended so other
environment-provisioning commands (e.g. a future ``docker-image`` that folds in
the current ``clm docker`` build) can live alongside it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.group("provision")
def provision_group() -> None:
    """Provision environments clm runs against (kernels, tools)."""


@provision_group.command("kernel-env")
@click.option(
    "--python",
    "python_exe",
    required=True,
    type=click.Path(path_type=Path),
    help="The course venv that should host the Direct-mode notebook kernel: "
    "either the venv directory (clm picks the platform interpreter inside it) "
    "or a specific Python interpreter. Relative paths resolve against the "
    "project root. Must have 'ipykernel' installed.",
)
@click.option(
    "--no-validate",
    is_flag=True,
    default=False,
    help="Skip the interpreter/ipykernel validation (write the kernelspec anyway).",
)
def kernel_env(python_exe: Path, no_validate: bool) -> None:
    """Register a course-runtime kernel environment for Direct mode.

    Writes a ``python3`` kernelspec pointing at the given interpreter and prints
    the ways to activate it. clm then runs the notebook kernel in that
    environment (the course-runtime ML/data-science stack lives there, not in
    clm's own venv) while clm keeps driving nbconvert.

    This registers an interpreter you already have; it does not create the venv.
    """
    from clm.infrastructure.workers.kernel_env import (
        KERNEL_PYTHON_ENV_VAR,
        provision_course_kernel,
        resolve_kernel_interpreter,
    )

    # Accept a venv directory or a relative path, same as <kernel-python> /
    # clm.toml: normalise to an absolute interpreter before provisioning.
    resolved = Path(resolve_kernel_interpreter(str(python_exe)))

    try:
        root = provision_course_kernel(resolved, validate=not no_validate)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Registered course kernel for: {resolved}")
    click.echo(f"Kernelspec root: {root}")
    click.echo("")
    click.echo("Activate it for a build in any of these ways (most specific wins):")
    click.echo(f"  - Env var:     {KERNEL_PYTHON_ENV_VAR}={resolved}")
    click.echo(f"  - Course spec: <kernel-python>{resolved}</kernel-python>")
    click.echo(f'  - clm.toml:    [jupyter] kernel_python = "{resolved}"')
