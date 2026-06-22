"""Info command for displaying version-accurate CLM documentation.

This module provides a command to show agent-friendly markdown documentation
about CLM's current behavior, always reflecting the installed version.
"""

from importlib.resources import files
from typing import NamedTuple

import click

from clm.__version__ import __version__


class TopicInfo(NamedTuple):
    name: str
    description: str
    filename: str


TOPICS: dict[str, TopicInfo] = {
    "spec-files": TopicInfo(
        "spec-files",
        "Course specification XML format reference",
        "spec-files.md",
    ),
    "commands": TopicInfo(
        "commands",
        "CLI command reference",
        "commands.md",
    ),
    "migration": TopicInfo(
        "migration",
        "Breaking changes and migration guide",
        "migration.md",
    ),
    "jupyterlite": TopicInfo(
        "jupyterlite",
        "JupyterLite output format: opt-in gates and <jupyterlite> config",
        "jupyterlite.md",
    ),
    "calendar": TopicInfo(
        "calendar",
        "Cohort calendar: project course schedule onto real teaching dates",
        "calendar.md",
    ),
    "slide-format": TopicInfo(
        "slide-format",
        "Jupytext percent-format: cell markers, tags, slide_id, bilingual/split structure",
        "slide-format.md",
    ),
    "releases": TopicInfo(
        "releases",
        "Per-topic solution release: channels, ledger, sync, clm git",
        "releases.md",
    ),
    "sync-agents": TopicInfo(
        "sync-agents",
        "Agent workflow for `clm slides sync`: dry-run report tiers, realign residue, --verify",
        "sync-agents.md",
    ),
}


def load_topic_content(topic_slug: str) -> str:
    """Load and render a topic's markdown content.

    Args:
        topic_slug: Key into the TOPICS registry.

    Returns:
        Markdown string with {version} placeholders replaced.
    """
    topic = TOPICS[topic_slug]
    content = files("clm.cli.info_topics").joinpath(topic.filename).read_text(encoding="utf-8")
    return content.replace("{version}", __version__)


@click.command()
@click.argument("topic", required=False, default=None)
def info(topic: str | None) -> None:
    """Show version-accurate CLM documentation.

    Without a TOPIC argument, lists available topics.
    With a TOPIC, displays the full documentation for that topic.

    \b
    Examples:
        clm info                # List available topics
        clm info spec-files     # Spec file format reference
        clm info commands       # CLI command reference
        clm info migration      # Breaking changes and migration guide
        clm info calendar       # Cohort calendar reference
        clm info slide-format   # Slide file format reference
        clm info releases       # Per-topic solution release reference
        clm info sync-agents    # Agent workflow for clm slides sync
    """
    if topic is None:
        click.echo(f"CLM {__version__} — Available documentation topics:\n")
        for slug, ti in TOPICS.items():
            click.echo(f"  {slug:<14} {ti.description}")
        click.echo("\nUsage: clm info <topic>")
        return

    if topic not in TOPICS:
        available = ", ".join(TOPICS)
        raise click.BadParameter(
            f"Unknown topic '{topic}'. Available topics: {available}",
            param_hint="'TOPIC'",
        )

    click.echo(load_topic_content(topic))
