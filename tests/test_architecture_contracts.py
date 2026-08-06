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
- the abstract :class:`Backend` surface pin and the worker-side Pydantic
  payload-schema pins (the process boundary every worker deserializes) —
  these change only deliberately, in the same commit as every
  implementation.
"""

from __future__ import annotations

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
