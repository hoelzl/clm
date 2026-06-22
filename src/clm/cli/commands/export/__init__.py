"""The ``clm export`` group — rendered course documents.

One module per subcommand: ``clm export <cmd>`` lives in
``commands/export/<cmd>.py``. Shared option vocabulary sits in
``commands/_export_shared.py`` (also used by ``clm calendar generate``).
"""

from __future__ import annotations

import click

from clm.cli.commands.shared import hidden_alias


@click.group("export")
def export_group() -> None:
    """Export course documents: outline, schedule, context, and LLM summary."""


from clm.cli.commands.export.agent_guide import agent_guide  # noqa: E402
from clm.cli.commands.export.context import context  # noqa: E402
from clm.cli.commands.export.outline import outline  # noqa: E402
from clm.cli.commands.export.schedule import schedule  # noqa: E402
from clm.cli.commands.export.summary import summary  # noqa: E402

export_group.add_command(outline, name="outline")
export_group.add_command(schedule, name="schedule")
export_group.add_command(summary, name="summary")
export_group.add_command(hidden_alias(summary, "summarize"))  # noun-vs-verb
export_group.add_command(context, name="context")
export_group.add_command(agent_guide, name="agent-guide")
