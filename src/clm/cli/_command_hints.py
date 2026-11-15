"""Cross-group "Did you mean" hints for unknown commands (issue #1241).

Click's own suggestion on ``No such command 'X'`` only considers the
*sibling* commands of the group that failed. After the CLI restructure
(#278, #310, #158) every command lives in exactly one group, so the
common miss is the right name in the wrong group — ``clm course outline``
or ``clm outline`` for ``clm export outline``. :class:`CommandHintGroup`
(the root ``clm`` group) catches the unknown-command error raised by any
group in the tree and appends the other places a command of that name
lives.

The suggestions come from the static :data:`COMMAND_PATHS` index in
:mod:`clm.cli._command_index`, never from importing the command modules:
the lazy-import contract of :mod:`clm.cli._lazy_group` holds even on the
error path. ``tests/cli/test_command_hints.py`` walks the real tree and
fails when the index is stale; regenerate it with
``python scripts/update_cli_command_index.py``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from difflib import get_close_matches

import click

from clm.cli._command_index import COMMAND_PATHS
from clm.cli._lazy_group import LazyGroup

__all__ = ["CommandHintGroup", "suggest_command_paths", "walk_command_tree"]

#: Click < 8.4 raises a plain UsageError; recover the name from its message.
_NO_SUCH_COMMAND_RE = re.compile(r"^No such command (['\"])(?P<name>.+)\1\.")

#: Cap on how many cross-group suggestions a single error lists.
_MAX_SUGGESTIONS = 5


def suggest_command_paths(
    name: str,
    group_path: Sequence[str] = (),
    paths: Iterable[tuple[str, ...]] = COMMAND_PATHS,
    *,
    close_matches: bool = True,
) -> list[tuple[str, ...]]:
    """Return command paths (below the root) that ``name`` probably meant.

    Exact matches on the last path element come first; only when there
    are none, close matches (edit distance via :mod:`difflib`) are tried.
    Commands that are direct children of ``group_path`` (the group in
    which ``name`` was not found) are skipped — Click already suggests
    those siblings itself. ``close_matches=False`` returns exact matches
    only.
    """
    group = tuple(group_path)
    candidates = [p for p in paths if p[:-1] != group]
    exact = [p for p in candidates if p[-1] == name]
    if exact:
        return sorted(exact, key=lambda p: (len(p), p))[:_MAX_SUGGESTIONS]
    if not close_matches:
        return []
    leaf_names = sorted({p[-1] for p in candidates})
    close = get_close_matches(name, leaf_names, n=3, cutoff=0.8)
    result = [p for leaf in close for p in sorted(candidates) if p[-1] == leaf]
    return result[:_MAX_SUGGESTIONS]


def _unknown_command_name(error: click.UsageError) -> str | None:
    name = getattr(error, "command_name", None)  # click.NoSuchCommand (8.4+)
    if isinstance(name, str):
        return name
    match = _NO_SUCH_COMMAND_RE.match(error.message)
    return match.group("name") if match else None


def _format_hint(root_name: str, paths: Sequence[tuple[str, ...]]) -> str:
    rendered = [" ".join((root_name, *p)) for p in paths]
    if len(rendered) == 1:
        return f"Did you mean: {rendered[0]}"
    return "Did you mean one of:\n" + "\n".join(f"  {r}" for r in rendered)


def add_cross_group_hint(error: click.UsageError) -> None:
    """Append cross-group suggestions to an unknown-command ``error``.

    No-op for any other usage error, or when nothing matches.
    """
    if getattr(error, "_clm_cross_group_hint", False):
        return
    name = _unknown_command_name(error)
    if name is None or error.ctx is None:
        return
    root = error.ctx.find_root()
    group_path: list[str] = []
    ctx: click.Context | None = error.ctx
    while ctx is not None and ctx is not root:
        group_path.insert(0, ctx.info_name or "")
        ctx = ctx.parent
    # When Click already proposes a near-miss sibling ("Did you mean
    # 'outline'?"), add only exact cross-group matches: a second set of
    # fuzzy guesses would just compete with the sibling's.
    sibling_hint = bool(getattr(error, "possibilities", None))
    paths = suggest_command_paths(name, group_path, close_matches=not sibling_hint)
    error._clm_cross_group_hint = True  # type: ignore[attr-defined]
    if not paths:
        return
    error.message = f"{error.message}\n{_format_hint(root.info_name or 'clm', paths)}"


class CommandHintGroup(LazyGroup):
    """The root ``clm`` group: lazy, plus cross-group command hints.

    Unknown-command errors from nested groups propagate through the root
    group's :meth:`invoke`, so catching them here covers the whole tree
    without touching each group's class.
    """

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except click.UsageError as error:
            add_cross_group_hint(error)
            raise


def walk_command_tree(group: click.Group) -> list[tuple[str, ...]]:
    """Return the path of every visible command below ``group``.

    Imports every command module — only for the index generator and its
    staleness test, never on a normal CLI path. Hidden commands and
    optional commands whose extras are missing are skipped.
    """
    found: list[tuple[str, ...]] = []

    def visit(cmd: click.Group, prefix: tuple[str, ...], ctx: click.Context) -> None:
        for name in cmd.list_commands(ctx):
            sub = cmd.get_command(ctx, name)
            if sub is None or sub.hidden:
                continue
            path = (*prefix, name)
            found.append(path)
            if isinstance(sub, click.Group):
                visit(sub, path, click.Context(sub, info_name=name, parent=ctx))

    visit(group, (), click.Context(group, info_name="clm"))
    return sorted(found)
