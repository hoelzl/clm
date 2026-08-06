"""Programmatic build engine (Phase 8 A4, #802).

The callable equivalent of ``clm build``: construct a :class:`BuildConfig`,
``await run_build(config)``, and apply your own policy to the returned
:class:`~clm.core.build_data_classes.BuildSummary` — no Click involved.
MCP tools, the web studio, and tests build courses through this package;
``clm.cli.commands.build`` is a thin Click adapter over it.

Layering: ``clm.build`` sits above ``clm.workers`` and may import all three
constrained layers; it must never import ``clm.cli`` or the extension
packages (enforced by import-linter — see ``[tool.importlinter]``).
"""

from clm.build.config import (
    VALID_HTTP_REPLAY_MODES,
    BuildConfig,
    resolve_explain_rebuilds,
    resolve_fail_on_error,
    resolve_fail_on_missing_xref,
    resolve_http_replay_mode,
    resolve_http_replay_transport,
    resolve_log_level,
    resolve_write_provenance_manifest,
)
from clm.build.engine import (
    initialize_paths_and_course,
    process_course_with_backend,
    run_build,
)
from clm.build.errors import BuildOptionError, SpecValidationFailure
from clm.build.output_formatter import OutputFormatter
from clm.build.reporter import BuildReporter

__all__ = [
    "VALID_HTTP_REPLAY_MODES",
    "BuildConfig",
    "BuildOptionError",
    "BuildReporter",
    "OutputFormatter",
    "SpecValidationFailure",
    "initialize_paths_and_course",
    "process_course_with_backend",
    "resolve_explain_rebuilds",
    "resolve_fail_on_error",
    "resolve_fail_on_missing_xref",
    "resolve_http_replay_mode",
    "resolve_http_replay_transport",
    "resolve_log_level",
    "resolve_write_provenance_manifest",
    "run_build",
]
