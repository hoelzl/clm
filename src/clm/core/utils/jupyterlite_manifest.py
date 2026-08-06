"""JupyterLite manifest inputs shared by the build operation and the worker.

The build's cache key for a JupyterLite site is computed host-side by
``BuildJupyterliteSiteOperation`` from (a) the notebook trees' content hashes
and (b) the pinned ``jupyterlite-core`` version; the worker's assembler
recomputes the same hashes when packing the site. Both sides read this module
(Phase 8 S1, #802) so the manifest inputs cannot drift.
"""

import hashlib
from pathlib import Path

# Pinned jupyterlite-core version for the isolated ``uvx`` tool environment
# (issue #516 / Wave 2a — the site build never runs in clm's own venv). This
# pin is the version truth twice over: it defines what ``uvx`` installs (see
# ``clm.workers.jupyterlite.builder``, which keeps the private kernel-addon
# sibling pins) AND the ``jupyterlite_core_version`` payload field that feeds
# the build cache key, so a bump here correctly invalidates cached sites.
JUPYTERLITE_CORE_VERSION = "0.7.6"


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
