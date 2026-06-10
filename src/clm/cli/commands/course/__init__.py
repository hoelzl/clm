"""The ``clm course`` group — course/spec structure (issue #310).

Everything that operates on the course structure: deck resolution,
output targets, topic lookup, includes, and the readiness gate. One
module per subcommand: ``clm course <cmd>`` lives in
``commands/course/<cmd>.py`` (dashes become underscores).
"""

from __future__ import annotations

import click


@click.group("course")
def course_group() -> None:
    """Course structure: decks, targets, topics, includes, readiness gate."""


from clm.cli.commands.course.decks import spec_decks_cmd  # noqa: E402
from clm.cli.commands.course.gate import course_gate_cmd  # noqa: E402
from clm.cli.commands.course.orphans import spec_orphans_cmd  # noqa: E402
from clm.cli.commands.course.resolve_topic import resolve_topic_cmd  # noqa: E402
from clm.cli.commands.course.sync_includes import sync_includes_cmd  # noqa: E402
from clm.cli.commands.course.targets import list_targets  # noqa: E402

course_group.add_command(spec_decks_cmd, name="decks")
course_group.add_command(spec_orphans_cmd, name="orphans")
course_group.add_command(list_targets, name="targets")
course_group.add_command(course_gate_cmd, name="gate")
course_group.add_command(resolve_topic_cmd, name="resolve-topic")
course_group.add_command(sync_includes_cmd, name="sync-includes")
