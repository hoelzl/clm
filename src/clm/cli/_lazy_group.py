"""A :class:`click.Group` that imports subcommands on first use.

Importing ``clm.cli.main`` used to import every command module eagerly,
which pulled in the whole core/infrastructure stack (course model, SQLite
backend, pydantic message classes, rich, ...) on **every** CLI invocation
— roughly half a second of startup before Click even dispatched. With
``LazyGroup`` only the invoked command's module is imported; the full
import sweep now happens only for ``--help``-style listings, which need
every command's short help anyway.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Mapping

import click

#: Either a ``"module.path:attribute"`` spec or a zero-arg factory that
#: returns the command (used when loading needs extra wiring, e.g. the
#: voiceover group attaching its extract/inline subcommands).
LazySpec = str | Callable[[], click.Command]


class LazyGroup(click.Group):
    """Click group resolving subcommands from import specs on demand.

    Args:
        lazy_subcommands: Maps the *registered* command name to a
            ``"module:attr"`` string or a factory callable. The registered
            name wins over the loaded command's own name (Click aliases
            such as ``translate``/``bootstrap`` map two names to one spec).
        optional_subcommands: Names whose import is allowed to fail
            (commands gated behind optional extras). A failed import makes
            the command behave as if it were never registered: hidden from
            help listings and reported as an unknown command when invoked.
    """

    def __init__(
        self,
        *args,
        lazy_subcommands: Mapping[str, LazySpec] | None = None,
        optional_subcommands: Iterable[str] = (),
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._lazy_subcommands: dict[str, LazySpec] = dict(lazy_subcommands or {})
        self._optional_subcommands = set(optional_subcommands)

    def list_commands(self, ctx: click.Context) -> list[str]:
        # Names only — no imports. Click's help formatter and shell
        # completion call get_command() per name and skip ``None`` results,
        # so optional commands whose extras are missing drop out of
        # listings exactly as they did under eager registration.
        return sorted(set(super().list_commands(ctx)) | set(self._lazy_subcommands))

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        spec = self._lazy_subcommands.get(cmd_name)
        if spec is None:
            return None
        try:
            if callable(spec):
                cmd = spec()
            else:
                module_name, _, attr = spec.partition(":")
                cmd = getattr(importlib.import_module(module_name), attr)
        except ImportError:
            if cmd_name in self._optional_subcommands:
                return None
            raise
        # Cache under the registered name so repeated lookups (help
        # formatting, completion, re-invocation in tests) import once.
        self.add_command(cmd, name=cmd_name)
        return cmd
