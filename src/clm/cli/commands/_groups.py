"""New verb-group definitions for the Phase 0 CLI restructure.

The flat top-level layout (``clm normalize-slides``, ``clm
extract-voiceover``, ``clm resolve-topic``, ...) had grown to 11+
commands with two duplications (``validate-slides``/``validate-spec``
and ``extract-voiceover``/``inline-voiceover`` as siblings of the
``voiceover`` group). This module introduces the three new groups —
``slides``, ``topic``, ``authoring`` — under which existing commands
are re-registered with shorter names in ``clm.cli.main``. The
``voiceover`` group already exists in ``clm.cli.commands.voiceover``.

Each group here is a thin ``@click.group()`` with no shared state;
the registration of subcommands happens in ``main.py``.
"""

from __future__ import annotations

import click


@click.group("slides")
def slides_group() -> None:
    """Slide authoring: normalize, validate, search, language tools, etc."""


@click.group("topic")
def topic_group() -> None:
    """Topic resolution and inspection."""


@click.group("spec")
def spec_group() -> None:
    """Course-spec inspection: resolve the decks a spec pulls in."""


@click.group("course")
def course_group() -> None:
    """Course-wide orchestration: readiness gate, mechanical conversion passes."""


@click.group("authoring")
def authoring_group() -> None:
    """Authoring-rules introspection."""


@click.group("export")
def export_group() -> None:
    """Export course documents: outline, schedule, and LLM summary."""
