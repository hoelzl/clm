"""Architecture contracts (#801 Phase 7 → #802 Phase 8, terminal form).

The 2026-07-24 adversarial review's §4 verdict was *"The documented
four-layer architecture does not exist in the code… the boundaries are
fiction."* Phase 7 pinned the complete violation inventory as a
shrink-only ratchet in this file; Phase 8's A2/A6 and S1–S5 worked it
down from 50 edges over 40 files to **zero**, and S6 replaced the
ratchet with the real contract: **import-linter in CI and pre-commit**
(config in ``pyproject.toml`` ``[tool.importlinter]``; grimp sees
function-body and TYPE_CHECKING imports, so lazy imports cannot dodge
it).

What remains here:

- the **string-import guard** — ``importlib.import_module("clm...")`` /
  ``__import__`` would slip past grimp and the old AST ratchet alike
  (round-2 finding M4), so a constrained layer must never stringify a
  clm import;
- the **private-import guard** (#802 A9) — import-linter constrains which
  modules may depend on which, but not which *names* cross the boundary;
  the review found ~12 ``from clm.x import _private`` imports, and after
  A5/A9 removed them all this guard keeps the count at zero;
- the abstract :class:`Backend` surface pin and the worker-side Pydantic
  payload-schema pins (the process boundary every worker deserializes) —
  these change only deliberately, in the same commit as every
  implementation.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "clm"

#: The layers the import-linter contracts constrain as SOURCES (extensions
#: and the CLI sit at the top of the stack and are unconstrained).
_CONSTRAINED_LAYERS = ("core", "infrastructure", "workers")


class TestImportContractGuards:
    def test_no_string_based_imports_dodge_the_contracts(self):
        """``importlib.import_module("clm...")`` / ``__import__("clm...")``
        are invisible to import-linter's graph exactly as they were to the
        old AST ratchet (round-2 finding M4). There are none in the
        constrained layers today, and this guard keeps it that way — a
        forbidden dependency must never be smuggled in as a string.
        """
        offenders: list[str] = []
        for file in sorted(_SRC.rglob("*.py")):
            rel = file.relative_to(_SRC).as_posix()
            if rel.split("/", 1)[0] not in _CONSTRAINED_LAYERS:
                continue
            text = file.read_text(encoding="utf-8")
            for needle in ('import_module("clm.', "import_module('clm.", '__import__("clm.'):
                if needle in text:
                    offenders.append(f"{rel}: {needle}")
        assert not offenders, (
            "string-based clm imports in constrained layers dodge the "
            "import-linter contracts — use a real import statement:\n  " + "\n  ".join(offenders)
        )


def _iter_clm_from_imports():
    """Yield ``(rel_path, lineno, source_module, alias_name)`` for every
    ``from <clm module> import <name>`` statement under ``src/clm``.

    Relative imports are resolved against the importing file's package so
    ``from .app import _helper`` is seen exactly like its absolute form.
    """
    for file in sorted(_SRC.rglob("*.py")):
        rel = file.relative_to(_SRC)
        anchor = ("clm", *rel.parts[:-1])  # containing package, both for modules and __init__
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                source = node.module or ""
            else:
                base = anchor[: len(anchor) - (node.level - 1)]
                source = ".".join((*base, node.module) if node.module else base)
            if source != "clm" and not source.startswith("clm."):
                continue
            for alias in node.names:
                yield rel.as_posix(), node.lineno, source, alias.name


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _clm_module_exists(dotted: str) -> bool:
    path = _SRC.parent.joinpath(*dotted.split("."))
    return path.with_suffix(".py").exists() or (path / "__init__.py").exists()


class TestPrivateImportGuard:
    """No module reaches into another module's underscore-privates (#802 A9).

    import-linter constrains which modules may depend on which, but not
    which *names* cross the boundary. The review counted ~12
    ``from clm.x import _private`` imports (worst: ``mcp/tools.py`` → a CLI
    command's private helper); A5 and A9 replaced every one with a public
    seam. This guard keeps the count at zero, in both directions the
    convention allows:

    - a leading-underscore **symbol** is private to its defining module —
      importing one from anywhere is an offence (fix: give the seam a
      public name in the defining module);
    - a leading-underscore **module** is private to its defining package —
      importing it (or importing from it) is fine within that package's
      subtree (``clm.cli.commands._export_shared`` from
      ``clm.cli.commands.export.*``) and an offence outside it.

    Dunder names (``from clm.__version__ import __version__``) are the
    conventional public exception.
    """

    def test_no_cross_module_private_imports(self):
        offenders: list[str] = []
        for rel, lineno, source, name in _iter_clm_from_imports():
            importer = ("clm", *Path(rel).parts[:-1])
            source_parts = source.split(".")
            # Private-module rule: every underscore component of the source
            # path must own the importing file (subtree containment).
            for i, comp in enumerate(source_parts):
                if comp.startswith("_") and not _is_dunder(comp):
                    owner = tuple(source_parts[:i])
                    if importer[: len(owner)] != owner:
                        offenders.append(
                            f"{rel}:{lineno}: from {source} import {name} "
                            f"(private module {'.'.join(source_parts[: i + 1])} "
                            f"belongs to {'.'.join(owner)})"
                        )
                    break
            else:
                if name.startswith("_") and not _is_dunder(name):
                    if _clm_module_exists(f"{source}.{name}"):
                        # Importing a private submodule as a name: same
                        # subtree rule as a dotted private-module source.
                        if importer[: len(source_parts)] != tuple(source_parts):
                            offenders.append(
                                f"{rel}:{lineno}: from {source} import {name} "
                                f"(private module {source}.{name} belongs to {source})"
                            )
                    else:
                        offenders.append(f"{rel}:{lineno}: from {source} import {name}")
        assert not offenders, (
            "cross-module underscore-private imports (#802 A9 removed the "
            "last one — give the seam a public name in the defining module "
            "instead):\n  " + "\n  ".join(offenders)
        )


class TestBackendContract:
    """What a backend must implement — pinned before Phase 8 moves anything
    beneath the interface."""

    EXPECTED_ABSTRACT = frozenset(
        {
            "copy_dir_group_to_output",
            "copy_file_to_output",
            "delete_dependencies",
            "delete_file",
            "execute_operation",
            "wait_for_completion",
        }
    )

    def test_abstract_surface_is_exactly_the_contract(self):
        from clm.core.backend import Backend

        assert frozenset(Backend.__abstractmethods__) == self.EXPECTED_ABSTRACT, (
            "the Backend contract changed — update every implementation AND "
            "this pin in the same commit, deliberately"
        )

    def test_the_backend_ladder_shape_is_pinned(self):
        """The review's A11 ladder, pinned as it IS: ``SqliteBackend`` is the
        one concrete production backend; ``LocalOpsBackend`` is a *partial*
        base that leaves exactly the dispatch pair abstract (the e2e tests
        subclass it to fill them in). Phase 8's A11 step flattens this —
        which will fail this pin, deliberately."""
        from clm.core.backend import Backend
        from clm.infrastructure.backends.local_ops_backend import LocalOpsBackend
        from clm.infrastructure.backends.sqlite_backend import SqliteBackend

        assert issubclass(SqliteBackend, Backend)
        assert not SqliteBackend.__abstractmethods__, "the production backend must be concrete"
        assert issubclass(LocalOpsBackend, Backend)
        assert frozenset(LocalOpsBackend.__abstractmethods__) == frozenset(
            {"execute_operation", "wait_for_completion"}
        )


class TestWorkerPayloadContract:
    """The worker process boundary: every field a worker deserializes.

    A renamed or removed field here silently breaks workers built against
    the old schema (Docker images lag the host), so the schema changes only
    deliberately — update the pin in the same commit as the model.
    """

    BASE_FIELDS = frozenset(
        {"correlation_id", "data", "input_file", "input_file_name", "output_file"}
    )
    IMAGE_FIELDS = BASE_FIELDS | {"output_format", "worker_image_identity"}
    NOTEBOOK_FIELDS = BASE_FIELDS | {
        "author",
        "cross_references",
        "fallback_execute",
        "format",
        "http_replay_cassette_name",
        "http_replay_mode",
        "http_replay_trace_dir",
        "img_path_prefix",
        "inline_images",
        "kind",
        "language",
        "organization",
        "other_files",
        "prog_lang",
        "skip_errors",
        "skip_evaluation",
        "source_topic_dir",
        "svg_available_stems",
        "template_fingerprint",
        "worker_image_identity",
    }

    #: PlantUML/DrawIO extend the image payload with the output file NAME —
    #: renaming it strands lagging Docker workers, the exact scenario this
    #: class exists to make deliberate (round-2 finding: it was unpinned).
    DIAGRAM_FIELDS = IMAGE_FIELDS | {"output_file_name"}
    JUPYTERLITE_FIELDS = BASE_FIELDS | {
        "app_archive",
        "branding_logo",
        "branding_site_name",
        "branding_theme",
        "course_root",
        "environment_yml",
        "jupyterlite_core_version",
        "kernel",
        "kinds",
        "language",
        "launcher",
        "notebook_trees",
        "output_dir",
        "target_name",
        "wheels",
    }

    def test_payload_schemas_are_pinned(self):
        from clm.core.messaging.base_classes import ImagePayload, Payload
        from clm.core.messaging.drawio_classes import DrawioPayload
        from clm.core.messaging.jupyterlite_classes import JupyterLitePayload
        from clm.core.messaging.notebook_classes import NotebookPayload
        from clm.core.messaging.plantuml_classes import PlantUmlPayload

        assert frozenset(Payload.model_fields) == self.BASE_FIELDS
        assert frozenset(ImagePayload.model_fields) == self.IMAGE_FIELDS
        assert frozenset(NotebookPayload.model_fields) == self.NOTEBOOK_FIELDS
        assert frozenset(PlantUmlPayload.model_fields) == self.DIAGRAM_FIELDS
        assert frozenset(DrawioPayload.model_fields) == self.DIAGRAM_FIELDS
        assert frozenset(JupyterLitePayload.model_fields) == self.JUPYTERLITE_FIELDS
