"""Verb-group definitions for the CLI command tree.

The CLI groups commands by domain concept (issue #310): ``slides`` for
deck-level authoring tools, ``course`` for everything that operates on
the course/spec structure (deck resolution, topic lookup, includes,
readiness gate), and ``export`` for rendered course documents. The
``calendar``, ``voiceover``, and infrastructure groups live next to
their implementations.

Each group here is a thin ``@click.group()`` with no shared state;
the registration of subcommands happens in ``clm.cli.main``.
"""

from __future__ import annotations

import copy

import click


@click.group("slides")
def slides_group() -> None:
    """Slide authoring: normalize, sync, search, language tools, etc."""


@click.group("course")
def course_group() -> None:
    """Course structure: decks, targets, topics, includes, readiness gate."""


@click.group("export")
def export_group() -> None:
    """Export course documents: outline, schedule, and LLM summary."""


def hidden_alias(cmd: click.Command, name: str) -> click.Command:
    """A hidden second name for ``cmd``.

    The alias stays invocable but is not listed in ``--help``, so each
    command shows up exactly once. The shallow copy shares params and
    callback with the canonical command.
    """
    alias = copy.copy(cmd)
    alias.name = name
    alias.hidden = True
    return alias
