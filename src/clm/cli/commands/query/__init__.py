"""The ``clm query`` group — read-only introspection for scripting (issue #350).

The group name states the contract: queries never modify anything (no
filesystem writes, no builds), and every member is safe to call from scripts
and CI. One module per subcommand: ``clm query <cmd>`` lives in
``commands/query/<cmd>.py`` (dashes become underscores).
"""

from __future__ import annotations

import click


@click.group("query")
def query_group() -> None:
    """Read-only queries for scripting: never modify anything."""


from clm.cli.commands.query.affected_specs import affected_specs_cmd  # noqa: E402

query_group.add_command(affected_specs_cmd, name="affected-specs")
