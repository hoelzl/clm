"""Phase 7 item 2 (#801): layer-boundary contracts, as executable ratchets.

The 2026-07-24 adversarial review's §4 verdict: *"The documented four-layer
architecture does not exist in the code… the boundaries are fiction"* (A1–A6).
Since every documented boundary is currently violated, the Phase-7 form of a
"contract" is a **ratchet**: the complete, file-level inventory of today's
violations is pinned below, and the test fails in BOTH directions — a new
forbidden import fails immediately, and a fixed file must be removed from the
inventory, so the list only shrinks. Phase 8 (#802) works this list down to
empty, at which point the ratchet collapses into the real contract
(import-linter in CI, per Phase 8 item 4).

Also pinned, as the boundaries Phase 8 must not silently change while moving
code beneath them: the abstract :class:`Backend` surface and the worker-side
Pydantic payload schemas (the process boundary every worker deserializes).

The scanner is deliberately total: it sees module-level AND lazy (function-
body) imports, because the review's A2 inventory showed the cycle hiding in
both.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "clm"
_LAYERS = {"core", "infrastructure", "cli", "workers"}


def _extension_packages() -> frozenset[str]:
    return frozenset(
        p.name
        for p in _SRC.iterdir()
        if p.is_dir() and p.name not in _LAYERS and not p.name.startswith("_")
    )


def _forbidden_targets() -> dict[str, frozenset[str]]:
    """The documented layering, as forbidden import edges.

    ``architecture.md`` stacks core → infrastructure → workers → extensions
    → cli: each layer may depend only on the layers *below* it. So ``core``
    depends on nothing (A1/A2/A3), ``infrastructure`` may not reach up into
    workers, extensions or the CLI (A2), and ``workers`` may not reach into
    extensions or the CLI. Extension packages and the CLI are unconstrained
    (they sit at the top). The extension set is derived, not hardcoded, so a
    new top-level package is automatically protected without editing this
    test. (The review's own inventory covered only the A1/A2/A3 edges; the
    round-2 review of this gate found live violations on the two missing
    edge classes, now part of the ratchet.)
    """
    extensions = _extension_packages()
    return {
        "core": frozenset({"infrastructure", "cli", "workers"} | extensions),
        "infrastructure": frozenset({"cli", "workers"} | extensions),
        "workers": frozenset({"cli"} | extensions),
    }


def _clm_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names if alias.name.startswith("clm.")}
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("clm."):
            found.add(node.module)
    return found


def _current_violations() -> set[str]:
    """Every ``"<edge>: <file>"`` where a layer imports a forbidden target."""
    forbidden = _forbidden_targets()
    violations: set[str] = set()
    for file in sorted(_SRC.rglob("*.py")):
        rel = file.relative_to(_SRC).as_posix()
        source_pkg = rel.split("/", 1)[0] if "/" in rel else None
        if source_pkg not in forbidden:
            continue
        for target in _clm_imports(file):
            parts = target.split(".")
            target_pkg = parts[1] if len(parts) > 1 else ""
            if target_pkg in forbidden[source_pkg]:
                violations.add(f"{source_pkg} -> {target_pkg}: {rel}")
    return violations


#: The complete violation inventory at the time the ratchet was pinned
#: (2026-08-06: 50 edges over 40 files — the review's A1/A2/A3 findings plus
#: the infrastructure→workers and workers→extensions residents its inventory
#: missed). Phase 8 removes entries as it moves code; nothing may be added.
#: Ratcheted down so far: A2 (#802) moved build_data_classes +
#: error_categorizer into infrastructure, clearing every infrastructure→cli
#: edge and core→cli's only resident → 42 edges over 33 files. A6 (#802)
#: moved the path domain vocabulary into clm.core.utils.path_utils, clearing
#: every core file whose only infrastructure import was path_utils → 31
#: edges over 23 files. S1 of the A1/A3 design
#: (docs/claude/design/phase8-a1-a3-core-decoupling.md) descended the leaf
#: vocabulary (prog-lang tables + comment tokens, tags, workshop scope,
#: sidecar layout, deck markers, companion paths, replay trace, jupyterlite
#: manifest, diagram-tool locators, C++ analysis/emission) → 19 edges over
#: 17 files. S2 descended the contract seam (Operation hierarchy, Backend
#: ABC, the messaging/payload package, File, copy-data, build_data_classes,
#: build_profiling) into clm.core → 5 edges over 4 files. S3 relocated the
#: cassette staging maintenance off Course into
#: infrastructure.http_replay_mitm.cassette_staging (sweeping is the entry
#: points' job now) → 4 edges over 3 files. S4 inverted the worker-image
#: identity reads through the clm.core.worker_identity registry
#: (infrastructure records + provides the singleton fallback) → 1 edge.
#: S5 descended the slide-text model (slide_parser, raw_cells,
#: anchor_primitives, pairing, the payload-time voiceover merge) into
#: clm.core.slide_text → **EMPTY**. The documented architecture now exists
#: in the import graph; S6 (#802) adds the import-linter contract to CI,
#: after which this inventory test becomes belt-and-braces.
KNOWN_LAYER_VIOLATIONS = frozenset()


class TestLayerBoundaryRatchet:
    def test_no_new_violations_and_no_stale_entries(self):
        current = _current_violations()
        new = current - KNOWN_LAYER_VIOLATIONS
        fixed = KNOWN_LAYER_VIOLATIONS - current
        assert not new, (
            "NEW layer-boundary violation(s) — the documented architecture "
            "forbids these imports (review §4 / #801); import the other "
            "direction or move the code:\n  " + "\n  ".join(sorted(new))
        )
        assert not fixed, (
            "layer violation(s) fixed — ratchet down by removing them from "
            "KNOWN_LAYER_VIOLATIONS (the list only shrinks):\n  " + "\n  ".join(sorted(fixed))
        )

    def test_the_scanner_sees_lazy_imports(self, tmp_path):
        """The ratchet is only as good as the scanner: A2's inventory hid in
        function bodies, so a module-level-only scan would ratchet a fiction.
        Exercises the REAL ``_clm_imports`` (review round 2: an inline
        reimplementation would pass while the scanner itself regressed)."""
        import textwrap

        probe = tmp_path / "probe.py"
        probe.write_text(
            textwrap.dedent(
                """
                def lazy():
                    from clm.cli.build_data_classes import BuildWarning
                    return BuildWarning
                """
            ),
            encoding="utf-8",
        )
        assert "clm.cli.build_data_classes" in _clm_imports(probe)
        # The companion "live inventory carries a lazy resident" pin retired
        # with S5: the inventory is empty, so the probe above is the whole
        # guarantee — a module-level-only scanner regression would now show
        # up as a missed NEW violation, and this test keeps _clm_imports
        # honest about function-body imports.

    def test_no_string_based_imports_dodge_the_ratchet(self):
        """The scanner is AST-based, so ``importlib.import_module("clm...")``
        or ``__import__("clm...")`` would slip past it (round-2 finding M4).
        There are none in the constrained layers today, and this guard keeps
        it that way — a Phase 8 mover must not "fix" a ratchet entry by
        stringifying the import."""
        offenders: list[str] = []
        for file in sorted(_SRC.rglob("*.py")):
            rel = file.relative_to(_SRC).as_posix()
            if rel.split("/", 1)[0] not in _forbidden_targets():
                continue
            text = file.read_text(encoding="utf-8")
            for needle in ('import_module("clm.', "import_module('clm.", '__import__("clm.'):
                if needle in text:
                    offenders.append(f"{rel}: {needle}")
        assert not offenders, (
            "string-based clm imports in constrained layers dodge the AST "
            "ratchet — use a real import statement:\n  " + "\n  ".join(offenders)
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
