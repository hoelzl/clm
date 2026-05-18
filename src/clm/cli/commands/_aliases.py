"""Deprecation-alias helper for the Phase 0 CLI restructure.

The slide-format-redesign rollout (CLM 1.6+) moves several flat commands
under verb-grouped subcommands — `clm normalize-slides` →
`clm slides normalize`, `clm extract-voiceover` → `clm voiceover extract`,
etc. Old names keep working for two minor releases so downstream
PythonCourses skills, hook commands, and trainer muscle memory have time
to migrate.

This helper wraps an existing ``click.Command`` as a deprecated top-level
alias that emits a one-line ``DeprecationWarning``-style notice on stderr
naming the new invocation, then forwards to the same underlying callback
without otherwise altering behavior. The alias inherits the original
command's parameters by reference, so adding a new option to the
canonical command shows up on the alias automatically.
"""

from __future__ import annotations

import functools

import click


def deprecated_alias(target: click.Command, *, new_invocation: str) -> click.Command:
    """Return a deprecated alias of *target* that forwards to its callback.

    Args:
        target: The canonical command. Its callback is reused unchanged.
        new_invocation: The new command path users should switch to,
            without the leading ``clm`` (e.g. ``"slides normalize"`` or
            ``"voiceover extract"``).

    The returned command:

    - Has the same name as *target* — caller is responsible for
      registering it where the old name should live.
    - Carries the same parameters as *target* (shared list reference),
      so flag additions propagate automatically.
    - Sets ``deprecated=True`` so Click renders the help text with
      "(deprecated)" and prints its standard warning.
    - Wraps the callback to also emit a one-line stderr notice naming
      the new invocation, so users see the migration path even when
      they ignore Click's bare "deprecated" tag.
    """
    if target.callback is None:
        # The helper is intended for plain commands, not pass-through
        # groups. Callers that need a deprecated group should compose
        # the deprecation message differently.
        raise TypeError(
            f"deprecated_alias requires target.callback to be set; "
            f"got command {target.name!r} with no callback."
        )

    original_callback = target.callback
    old_name = target.name

    from typing import Any

    @functools.wraps(original_callback)
    def wrapped_callback(*args: Any, **kwargs: Any) -> Any:
        click.echo(
            f"DeprecationWarning: `clm {old_name}` is deprecated. "
            f"Use `clm {new_invocation}` instead. "
            f"The alias will be removed in CLM 1.7.",
            err=True,
        )
        return original_callback(*args, **kwargs)

    return click.Command(
        name=old_name,
        callback=wrapped_callback,
        params=list(target.params),
        help=target.help,
        epilog=target.epilog,
        short_help=target.short_help,
        options_metavar=target.options_metavar,
        add_help_option=target.add_help_option,
        no_args_is_help=target.no_args_is_help,
        hidden=target.hidden,
        deprecated=True,
    )
