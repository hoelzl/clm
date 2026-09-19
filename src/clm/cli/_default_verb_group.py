"""Click groups whose bare invocation runs a default (read-only) verb.

Agent toolkits are read-by-default: ``clm slides sync DECK`` and
``clm harvest DECK VIDEO`` both mean their ``report`` verb. Click groups
have no native default subcommand, so the two strategies live here (#959):

* :class:`DefaultVerbGroup` prepends the default verb in
  :meth:`parse_args` — for plain groups with no group-level options.
* :class:`DefaultVerbLazyGroup` resolves the default in
  :meth:`resolve_command` (after group options are parsed) — for lazy
  groups carrying group options: a ``parse_args`` prepend would fire on
  a group flag such as ``--no-cache`` before Click ever saw it as a
  group option.
"""

from __future__ import annotations

import click

from clm.cli._lazy_group import LazyGroup

__all__ = ["DefaultVerbGroup", "DefaultVerbLazyGroup"]


class DefaultVerbGroup(click.Group):
    """A plain group whose bare ``<group> ARGS…`` runs the default verb.

    When the first token is not a known verb (and not a help flag),
    prepend ``default_verb`` so a bare argument list is treated as the
    read-only default the agent-toolkit contract mandates.
    """

    default_verb = "report"

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] not in self.commands and args[0] not in ("--help", "-h"):
            args = [self.default_verb, *args]
        return super().parse_args(ctx, args)


class DefaultVerbLazyGroup(LazyGroup):
    """A lazy group whose bare ``<group> ARGS…`` runs the default verb.

    The fallback lives in :meth:`resolve_command` because the group
    carries its own options — see the module docstring.
    """

    default_verb = "report"

    def resolve_command(self, ctx: click.Context, args: list[str]):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if args and not args[0].startswith("-"):
                cmd = self.get_command(ctx, self.default_verb)
                if cmd is not None:
                    return self.default_verb, cmd, args
            raise
