"""Course-runtime kernel environment for Direct-mode notebook execution.

Wave 2b (issue #516 follow-up) lets the Direct notebook worker launch its
Python kernel from a **separate interpreter** — a course venv holding the
course-runtime stack (``[ml]`` etc.) — while clm's own venv keeps driving
nbconvert. This isolates course dependencies from clm's environment, mirroring
what the Docker notebook image already does.

The mechanism is pure Jupyter plumbing and needs no change to the execution
hot path:

- ``TrackingExecutePreprocessor`` is created with **no ``kernel_name``**, so
  nbconvert resolves the kernel from the notebook's ``metadata.kernelspec.name``
  — the literal ``python3`` for Python decks
  (``clm.workers.notebook.utils.prog_lang_utils.kernelspec_for``).
- ``jupyter_client`` resolves ``python3`` by scanning kernelspec directories,
  and the dirs named by ``JUPYTER_PATH`` are searched **before** the user /
  system data dirs.

So provisioning a ``kernels/python3/kernel.json`` whose ``argv[0]`` is the
course interpreter and prepending its root to the worker's ``JUPYTER_PATH``
makes the kernel subprocess run in the course venv. Only the ``python3``
kernelspec is written; C++/C#/Java/TS kernels are external toolchains and are
left untouched.

This module owns two things: :func:`resolve_notebook_kernel_python` (the
env > course-spec > ``clm.toml`` precedence) and :func:`provision_course_kernel`
(writes the kernelspec, returns the ``JUPYTER_PATH`` root).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path

import platformdirs

logger = logging.getLogger(__name__)

#: Env var that overrides every other tier (operator escape hatch for one
#: invocation — debugging, CI). Documented canonical name.
KERNEL_PYTHON_ENV_VAR = "CLM_NOTEBOOK_KERNEL_PYTHON"


def resolve_notebook_kernel_python(spec_value: str = "") -> str:
    """Resolve the interpreter for the Python notebook kernel (Direct mode).

    Precedence, most specific first (first non-empty wins):

    1. ``CLM_NOTEBOOK_KERNEL_PYTHON`` env var — operator escape hatch.
    2. ``spec_value`` — the course spec's ``<kernel-python>`` element.
    3. ``clm.toml [jupyter].kernel_python`` — the project-level default.
    4. ``""`` — use clm's own environment (today's behaviour).

    Args:
        spec_value: The course-spec ``<kernel-python>`` value (tier 2). Callers
            that have no spec in scope pass ``""``.

    Returns:
        The resolved interpreter path, or ``""`` to mean "use clm's env".
    """
    env_value = os.environ.get(KERNEL_PYTHON_ENV_VAR, "").strip()
    if env_value:
        return env_value
    if spec_value and spec_value.strip():
        return spec_value.strip()

    # Project (clm.toml) tier. Read lazily so importing this module never
    # forces config resolution.
    from clm.infrastructure.config import get_config

    return (get_config().jupyter.kernel_python or "").strip()


def _venv_interpreter(venv_dir: Path) -> Path:
    """Return the Python interpreter inside a virtualenv *directory*.

    A venv stores its interpreter at ``Scripts/python.exe`` on Windows and
    ``bin/python`` on POSIX — so a single committed ``<kernel-python>`` value
    can point at the venv *directory* and resolve correctly on every platform,
    instead of hard-coding one OS's interpreter layout.

    Prefers whichever interpreter actually exists (so a POSIX venv checked out
    on the same box as a Windows one still resolves), falling back to the
    platform-native path when neither is present yet — so provisioning's
    "not found" error names a concrete, expected location.
    """
    windows = venv_dir / "Scripts" / "python.exe"
    posix = venv_dir / "bin" / "python"
    ordered = (windows, posix) if os.name == "nt" else (posix, windows)
    for candidate in ordered:
        if candidate.is_file():
            return candidate
    return ordered[0]


def resolve_kernel_interpreter(value: str, *, project_root: Path | None = None) -> str:
    """Turn a raw kernel-python value into an absolute interpreter path.

    Complements :func:`resolve_notebook_kernel_python` (which only picks the
    winning *tier*): this normalises whatever that tier yielded into a concrete,
    absolute interpreter, so the value threaded to the worker is platform- and
    cwd-independent. The rules, in order:

    - ``""`` (or whitespace) → ``""`` — use clm's own environment.
    - ``~`` is expanded.
    - A **relative** path is anchored to ``project_root`` (default:
      :func:`find_project_root`, which locates the ``pyproject.toml`` /
      ``clm.toml`` / ``.git`` dir) — NOT the process cwd — so a value committed
      to a shared repo resolves the same no matter where ``clm build`` runs.
    - A **directory** is treated as a virtualenv and resolved to the interpreter
      inside it (``Scripts/python.exe`` on Windows, ``bin/python`` on POSIX),
      so one committed value works cross-platform.
    - Anything else is returned as an interpreter path verbatim (existence is
      validated later, in :func:`provision_course_kernel`).

    Args:
        value: The raw kernel-python value (a venv directory or an interpreter,
            absolute or project-relative). Typically the result of
            :func:`resolve_notebook_kernel_python`.
        project_root: Anchor for relative values. Defaults to
            :func:`find_project_root`.

    Returns:
        An absolute interpreter path, or ``""`` for "use clm's env".
    """
    if not value or not value.strip():
        return ""

    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        if project_root is None:
            from clm.infrastructure.utils.path_utils import find_project_root

            project_root = find_project_root()
        path = project_root / path

    if path.is_dir():
        path = _venv_interpreter(path)

    return str(path)


def _kernel_envs_root() -> Path:
    """Return the base dir under which per-interpreter kernelspecs are written.

    Uses ``platformdirs.user_data_dir("clm")`` — a persistent, per-user,
    cross-platform location (distinct from config/logs/cache). Each interpreter
    gets its own subdir so multiple course venvs coexist.
    """
    return Path(platformdirs.user_data_dir("clm", appauthor=False)) / "kernel-envs"


def _validate_ipykernel(python_exe: Path) -> None:
    """Fail early (with an actionable message) if ``ipykernel`` is missing.

    The kernel launcher (``python -m ipykernel_launcher``) lives in the course
    venv, so ``ipykernel`` must be installed there.
    """
    try:
        result = subprocess.run(
            [str(python_exe), "-c", "import ipykernel"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(
            f"Could not run the interpreter {python_exe!r} to validate it: {e}"
        ) from e
    if result.returncode != 0:
        raise RuntimeError(
            f"The interpreter {python_exe!r} cannot import 'ipykernel'. Install "
            f'it into that environment (e.g. `"{python_exe}" -m pip install '
            f"ipykernel`) so it can host the notebook kernel, then retry."
        )


def provision_course_kernel(python_exe: Path, *, validate: bool = True) -> Path:
    """Write a ``python3`` kernelspec for ``python_exe`` and return its root.

    The returned directory is the one to place on ``JUPYTER_PATH`` (it *contains*
    ``kernels/python3/kernel.json``, which is the layout jupyter_client expects).
    The location is derived from a hash of the resolved interpreter path so
    re-provisioning the same interpreter is idempotent and distinct interpreters
    never collide.

    Args:
        python_exe: Interpreter that should host the Python notebook kernel.
        validate: When True (default), verify the interpreter exists and can
            import ``ipykernel`` before writing the kernelspec.

    Returns:
        The ``JUPYTER_PATH`` root directory containing ``kernels/python3/``.

    Raises:
        RuntimeError: The interpreter does not exist, or (when ``validate``)
            cannot import ``ipykernel``.
    """
    resolved = python_exe.expanduser()
    if validate:
        if not resolved.is_file():
            raise RuntimeError(
                f"kernel-python interpreter not found: {resolved!r}. Point "
                f"CLM_NOTEBOOK_KERNEL_PYTHON / <kernel-python> / clm.toml "
                f"[jupyter].kernel_python at an existing Python executable."
            )
        _validate_ipykernel(resolved)

    key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    root = _kernel_envs_root() / key
    kernel_dir = root / "kernels" / "python3"
    kernel_dir.mkdir(parents=True, exist_ok=True)

    kernel_json = {
        "argv": [
            str(resolved),
            "-m",
            "ipykernel_launcher",
            "-f",
            "{connection_file}",
        ],
        "display_name": "Python 3 (clm course venv)",
        "language": "python",
    }
    (kernel_dir / "kernel.json").write_text(
        json.dumps(kernel_json, indent=2),
        encoding="utf-8",
    )
    logger.debug("Provisioned course kernelspec for %s at %s", resolved, kernel_dir)
    return root


def jupyter_path_with_kernel(root: Path, existing: str | None) -> str:
    """Prepend ``root`` to an existing ``JUPYTER_PATH`` value.

    Prepending (not appending) is load-bearing: jupyter_client returns the first
    ``python3`` kernelspec it finds across the search path, so our course kernel
    must sit ahead of clm's own.
    """
    root_str = str(root)
    if existing:
        return root_str + os.pathsep + existing
    return root_str


__all__ = [
    "KERNEL_PYTHON_ENV_VAR",
    "resolve_notebook_kernel_python",
    "resolve_kernel_interpreter",
    "provision_course_kernel",
    "jupyter_path_with_kernel",
]
