"""Assemble a JupyterLite ``lite-dir/`` for a single ``(target, language, kind)``.

``jupyter lite build`` consumes a source directory (``--lite-dir``) with this
layout:

    lite-dir/
        jupyter_lite_config.json    # site config (required)
        files/                      # notebooks + data copied into the site
        pypi/                       # pre-staged wheels (pyodide kernel)
        environment.yml             # conda-forge env (xeus-python kernel)
        overrides.json              # optional UI overrides (Phase 3)

The functions here are pure: they take inputs, populate ``lite_dir`` on
disk, and return a deterministic manifest that callers can hash for cache
keying. Unit-testable without any ``jupyterlite-core`` dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


VALID_KERNELS = frozenset({"xeus-python", "pyodide"})
VALID_APP_ARCHIVES = frozenset({"offline", "cdn"})

# JupyterLite kernels use different names than desktop Jupyter.  Notebooks
# built by CLM carry the ipykernel kernelspec (``python3``); these mappings
# rewrite the metadata so the browser kernel starts correctly.
JUPYTERLITE_KERNELSPECS: dict[str, dict[str, str]] = {
    "pyodide": {
        "display_name": "Python (Pyodide)",
        "language": "python",
        "name": "python",
    },
    "xeus-python": {
        "display_name": "Python 3 (XPython)",
        "language": "python",
        "name": "xpython",
    },
}


def sha256_of_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def collect_notebook_tree(notebook_tree: Path) -> list[tuple[str, str]]:
    """Enumerate notebooks under ``notebook_tree`` with their content hashes.

    Returns a list of ``(relative_posix_path, sha256)`` sorted by path.
    Only files ending in ``.ipynb`` are included — supporting assets
    (images, data) travel with the notebooks via sibling inclusion in
    ``populate_files``.
    """
    if not notebook_tree.is_dir():
        raise FileNotFoundError(f"Notebook tree does not exist: {notebook_tree}")

    entries: list[tuple[str, str]] = []
    for path in sorted(notebook_tree.rglob("*.ipynb")):
        rel = path.relative_to(notebook_tree).as_posix()
        entries.append((rel, sha256_of_file(path)))
    return entries


def collect_notebook_trees(
    notebook_trees: dict[str, Path],
) -> dict[str, list[tuple[str, str]]]:
    """Enumerate notebooks across multiple kind-keyed trees.

    Returns ``{kind: [(relative_posix_path, sha256), ...]}`` with entries
    sorted by path within each kind and kinds sorted alphabetically.
    """
    result: dict[str, list[tuple[str, str]]] = {}
    for kind in sorted(notebook_trees):
        result[kind] = collect_notebook_tree(notebook_trees[kind])
    return result


def populate_files(lite_dir: Path, notebook_tree: Path) -> list[str]:
    """Copy a single notebook tree into ``lite_dir / 'files'``.

    Returns the list of relative POSIX paths that were copied.
    """
    return _copy_tree_into(lite_dir / "files", notebook_tree)


def populate_files_multi(lite_dir: Path, notebook_trees: dict[str, Path]) -> list[str]:
    """Copy multiple kind-keyed notebook trees into ``lite_dir / 'files'``.

    Each kind's notebooks land under ``files/<kind>/`` so students see
    a per-kind folder in the JupyterLab file browser. When there is only
    one kind, notebooks are placed directly under ``files/`` (no extra
    nesting) for a cleaner experience.

    Returns the list of relative POSIX paths that were copied.
    """
    files_dir = lite_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    if len(notebook_trees) == 1:
        ((_kind, tree),) = notebook_trees.items()
        return _copy_tree_into(files_dir, tree)

    copied: list[str] = []
    for kind in sorted(notebook_trees):
        kind_dir = files_dir / kind
        kind_dir.mkdir(parents=True, exist_ok=True)
        for rel in _copy_tree_into(kind_dir, notebook_trees[kind]):
            copied.append(f"{kind}/{rel}")
    return copied


def _copy_tree_into(dest_dir: Path, source_tree: Path) -> list[str]:
    """Mirror ``source_tree`` into ``dest_dir``, return relative POSIX paths."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src in sorted(source_tree.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(source_tree)
        dst = dest_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel.as_posix())
    return copied


def patch_notebook_kernelspec(notebook_path: Path, kernel: str) -> None:
    """Rewrite the kernelspec in a single ``.ipynb`` for JupyterLite.

    JupyterLite's pyodide kernel registers as ``python`` while ipykernel
    uses ``python3``; xeus-python uses ``xpython``.  Without this patch
    JupyterLite cannot find a matching kernel and notebooks hang.
    """
    if kernel not in JUPYTERLITE_KERNELSPECS:
        raise ValueError(f"Unknown JupyterLite kernel: {kernel!r}")

    nb = json.loads(notebook_path.read_text(encoding="utf-8"))
    nb.setdefault("metadata", {})["kernelspec"] = JUPYTERLITE_KERNELSPECS[kernel]
    notebook_path.write_text(
        json.dumps(nb, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def patch_notebooks_in_dir(directory: Path, kernel: str) -> int:
    """Patch every ``.ipynb`` under *directory* for the given kernel.

    Returns the number of notebooks patched.
    """
    count = 0
    for nb_path in sorted(directory.rglob("*.ipynb")):
        patch_notebook_kernelspec(nb_path, kernel)
        count += 1
    if count:
        logger.debug("Patched kernelspec in %d notebook(s) to %s", count, kernel)
    return count


def populate_wheels(lite_dir: Path, wheels: list[Path]) -> list[tuple[str, str]]:
    """Copy wheel files into ``lite_dir / 'pypi'`` (pyodide kernel).

    Returns ``(wheel_name, sha256)`` tuples sorted by wheel name.
    Raises ``FileNotFoundError`` if any wheel path is missing.
    """
    if not wheels:
        return []

    pypi_dir = lite_dir / "pypi"
    pypi_dir.mkdir(parents=True, exist_ok=True)

    staged: list[tuple[str, str]] = []
    for wheel in wheels:
        if not wheel.is_file():
            raise FileNotFoundError(f"Wheel not found: {wheel}")
        dst = pypi_dir / wheel.name
        shutil.copy2(wheel, dst)
        staged.append((wheel.name, sha256_of_file(wheel)))

    staged.sort(key=lambda pair: pair[0])
    return staged


def populate_environment(lite_dir: Path, environment_yml: Path | None) -> str | None:
    """Copy ``environment.yml`` to the lite-dir root (xeus-python kernel).

    Returns the SHA-256 of the copied file, or ``None`` if no environment
    was provided.
    """
    if environment_yml is None:
        return None
    if not environment_yml.is_file():
        raise FileNotFoundError(f"environment.yml not found: {environment_yml}")
    dst = lite_dir / "environment.yml"
    shutil.copy2(environment_yml, dst)
    return sha256_of_file(environment_yml)


def write_jupyter_lite_config(
    lite_dir: Path,
    *,
    kernel: str,
    wheel_names: list[str],
    app_archive: str,
) -> dict:
    """Write ``jupyter_lite_config.json`` and return the dict that was written.

    The structure mirrors the subset of keys documented for
    ``jupyterlite-core`` 0.7 that we use:

    - ``LiteBuildConfig.apps`` — which apps to ship (``lab``, ``retro``,
      ``notebooks``). We default to ``["lab"]`` for a minimal footprint.
    - ``PipliteAddon.piplite_urls`` — URLs the piplite kernel addon
      consults when ``%pip install``ing. For opt-in offline sites we
      pre-stage wheels into ``pypi/`` and list their local URLs here so
      runtime ``import`` works without a network roundtrip.
    - ``LiteBuildConfig.no_unused_shared_packages`` — trims the site by
      removing shared packages not used by the enabled apps.
    """
    if kernel not in VALID_KERNELS:
        raise ValueError(f"Unknown JupyterLite kernel: {kernel!r}")
    if app_archive not in VALID_APP_ARCHIVES:
        raise ValueError(f"Unknown JupyterLite app-archive: {app_archive!r}")

    piplite_urls = [f"./pypi/{name}" for name in wheel_names]

    # Note: the kernel-specific addon disabling (``jupyterlite-xeus`` vs
    # ``jupyterlite-pyodide-kernel``) is passed on the ``jupyter lite
    # build`` command line via ``--disable-addons``; see
    # ``builder._run_jupyter_lite_build``. Config-level disabling via
    # ``LiteBuildConfig.disable_addons`` does not reliably prevent
    # ``post_build`` hooks from firing in 0.7.x.
    config: dict = {
        "LiteBuildConfig": {
            "apps": ["lab"],
            "no_unused_shared_packages": True,
        },
    }
    if piplite_urls:
        config["PipliteAddon"] = {"piplite_urls": piplite_urls}

    config_path = lite_dir / "jupyter_lite_config.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    return config


def write_jupyter_lite_json(lite_dir: Path, *, kernel: str) -> dict:
    """Write ``jupyter-lite.json`` — the **runtime** site configuration.

    This is distinct from ``jupyter_lite_config.json`` (the *build*
    configuration). ``jupyter-lite.json`` is shipped as-is inside the
    built site and merged into the runtime ``jupyter-config-data``.

    We use it to disable the kernel extension that is *not* active for
    this site. Both ``@jupyterlite/pyodide-kernel-extension`` and
    ``@jupyterlite/xeus-extension`` are installed as npm dependencies
    and their JavaScript bundles are always shipped, but loading the
    unused extension causes it to probe for endpoints that don't exist
    (e.g. ``/xeus/kernels.json`` when only pyodide is active), which
    produces console errors and can delay kernel startup.
    """
    if kernel not in VALID_KERNELS:
        raise ValueError(f"Unknown JupyterLite kernel: {kernel!r}")

    disabled: list[str] = []
    if kernel == "pyodide":
        disabled.append("@jupyterlite/xeus-extension")
    elif kernel == "xeus-python":
        disabled.append("@jupyterlite/pyodide-kernel-extension")

    config: dict = {
        "jupyter-lite-schema-version": 0,
        "jupyter-config-data": {
            "disabledExtensions": disabled,
        },
    }

    path = lite_dir / "jupyter-lite.json"
    path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    return config


def write_overrides(
    lite_dir: Path,
    *,
    branding_theme: str = "",
    branding_logo: str = "",
    branding_site_name: str = "",
) -> dict | None:
    """Write ``overrides.json`` for JupyterLab UI customization.

    Returns the overrides dict that was written, or ``None`` if all
    branding fields are empty (no file written).
    """
    if not any([branding_theme, branding_logo, branding_site_name]):
        return None

    overrides: dict = {}
    if branding_theme:
        overrides["@jupyterlab/apputils-extension:themes"] = {
            "theme": f"JupyterLab {branding_theme.title()}"
        }
    if branding_site_name:
        overrides["@jupyterlab/application-extension:logo"] = {"title": branding_site_name}
    if branding_logo:
        overrides["@jupyterlab/application-extension:logo"] = {
            **overrides.get("@jupyterlab/application-extension:logo", {}),
            "icon": branding_logo,
        }

    overrides_path = lite_dir / "overrides.json"
    overrides_path.write_text(json.dumps(overrides, indent=2, sort_keys=True), encoding="utf-8")
    return overrides


def assemble_lite_dir(
    lite_dir: Path,
    *,
    notebook_trees: dict[str, Path],
    kernel: str,
    wheels: list[Path],
    environment_yml: Path | None,
    app_archive: str,
    branding_theme: str = "",
    branding_logo: str = "",
    branding_site_name: str = "",
) -> dict:
    """Populate ``lite_dir`` with everything ``jupyter lite build`` needs.

    ``notebook_trees`` maps kind labels (e.g. ``"Code-Along"``,
    ``"Completed"``) to directories of pre-built ``.ipynb`` files. When
    there is only one kind the notebooks are placed directly under
    ``files/``; with multiple kinds each gets its own subfolder so
    students see them separately in the JupyterLab file browser.

    Returns a manifest dict suitable for hashing to form a cache key.
    """
    lite_dir.mkdir(parents=True, exist_ok=True)

    notebook_entries = collect_notebook_trees(notebook_trees)
    populated_files = populate_files_multi(lite_dir, notebook_trees)
    patch_notebooks_in_dir(lite_dir / "files", kernel)
    wheel_entries = populate_wheels(lite_dir, wheels)
    env_hash = populate_environment(lite_dir, environment_yml)
    site_config = write_jupyter_lite_json(lite_dir, kernel=kernel)
    config = write_jupyter_lite_config(
        lite_dir,
        kernel=kernel,
        wheel_names=[name for name, _ in wheel_entries],
        app_archive=app_archive,
    )
    overrides = write_overrides(
        lite_dir,
        branding_theme=branding_theme,
        branding_logo=branding_logo,
        branding_site_name=branding_site_name,
    )

    manifest: dict = {
        "kernel": kernel,
        "app_archive": app_archive,
        "notebooks": notebook_entries,
        "wheels": wheel_entries,
        "environment_sha256": env_hash,
        "files_count": len(populated_files),
        "site_config": site_config,
        "config": config,
        "overrides": overrides,
    }
    return manifest


def hash_manifest(manifest: dict, *, jupyterlite_core_version: str) -> str:
    """Compute a stable cache key from a manifest plus the builder version.

    The manifest is JSON-serialized with sorted keys so the digest is
    insensitive to dict ordering. ``jupyterlite-core``'s version is
    included because a new release can change the build output even
    when inputs are byte-identical.
    """
    blob = json.dumps(
        {"manifest": manifest, "jupyterlite_core": jupyterlite_core_version},
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
