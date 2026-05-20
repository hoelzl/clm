"""Interactive apply/skip/edit walker for ``clm slides sync``.

Phase 7 v2 of the slide-format-redesign. Given the
:class:`~clm.slides.sync.SyncResult` produced by the read-only v1
walker, this module replays the ``"update"`` proposals one at a time
and prompts the user for an action per proposal.

The walker preserves byte-identical surrounding context: cell headers,
preamble, and trailing blank lines stay verbatim — only the body of the
target cell is rewritten. This is the same invariant Phase 5's
``split`` / ``unify`` round-trip relies on, so the walker reuses
:mod:`clm.slides.raw_cells`.

Side effects:

- Writes the target file in place after each accept/edit.
- Writes a :class:`~clm.infrastructure.llm.cache.SyncSnapshotCache`
  row after each accept/edit, capturing the post-write ``(de_hash,
  en_hash)`` pair as the new last-known-synced state for that
  ``(de_path, en_path, slide_id, role)`` slot.

Output and prompts go through :mod:`click` so the test suite can drive
the walker with :class:`click.testing.CliRunner` ``input=`` and
``monkeypatch`` :func:`click.edit`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click

from clm.slides.sync_writeback import (
    FileState,
    record_snapshot,
    target_path_for_outcome,
)

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncSnapshotCache
    from clm.slides.sync import PairOutcome, SyncResult


__all__ = [
    "WalkerAction",
    "WalkerOptions",
    "run_interactive_walker",
]


# Action keys returned by the prompt. Kept as bare strings so test
# helpers can stub the prompt with a list of values trivially.
APPLY = "apply"
SKIP = "skip"
EDIT = "edit"
QUIT = "quit"


@dataclass
class WalkerAction:
    """One step the walker took on a proposal — used for telemetry."""

    slide_id: str
    role: str
    direction: str
    action: str  # APPLY / SKIP / EDIT / QUIT
    target_path: Path
    error: str = ""


@dataclass
class WalkerOptions:
    """Knobs for one walker pass.

    ``prompt_fn`` and ``edit_fn`` are injected so the tests can drive
    the walker without invoking a real editor. Defaults dispatch to
    :func:`click.prompt` and :func:`click.edit`.
    """

    snapshot_cache: SyncSnapshotCache | None = None
    prompt_fn: Callable[[str], str] | None = None
    edit_fn: Callable[[str], str | None] | None = None


def run_interactive_walker(
    result: SyncResult,
    options: WalkerOptions | None = None,
) -> list[WalkerAction]:
    """Walk ``result.outcomes`` that need an update and prompt per item.

    Mutates ``result`` to bump the accept counters
    (``pairs_accepted`` / ``pairs_skipped`` / ``pairs_edited`` /
    ``pairs_quit``) and writes the target file in place on accept/edit.

    Returns the list of :class:`WalkerAction` records — useful for the
    JSON report and for tests asserting that the right path was taken
    per proposal.
    """
    options = options or WalkerOptions()
    prompt_fn = options.prompt_fn or _default_prompt
    edit_fn = options.edit_fn or _default_edit

    actions: list[WalkerAction] = []
    quitting = False

    # Cache the parsed file contents per path so multiple accepts on
    # the same file don't re-split / re-reconstruct on every iteration.
    # Each cell is keyed by its 1-based header line number, which
    # matches Cell.line_number from slide_parser.parse_cells.
    file_state: dict[Path, FileState] = {}

    # Filter out outcomes that the trivial-apply pass already wrote.
    # Their files are already on disk; re-prompting would surprise the
    # user with diffs that no longer reflect the file.
    pending = [o for o in result.outcomes if o.verdict == "update" and not o.applied_trivially]

    for outcome in pending:
        target_path = target_path_for_outcome(outcome, result)

        if quitting:
            # User asked to stop earlier; every remaining proposal counts
            # as a quit for telemetry but doesn't prompt or write.
            actions.append(
                WalkerAction(
                    slide_id=outcome.slide_id,
                    role=outcome.role,
                    direction=outcome.direction,
                    action=QUIT,
                    target_path=target_path,
                )
            )
            result.pairs_quit += 1
            continue

        _print_header(outcome, target_path)
        if outcome.diff:
            click.echo(outcome.diff)
            click.echo()

        action = _prompt_action(prompt_fn)

        if action == QUIT:
            actions.append(
                WalkerAction(
                    slide_id=outcome.slide_id,
                    role=outcome.role,
                    direction=outcome.direction,
                    action=QUIT,
                    target_path=target_path,
                )
            )
            result.pairs_quit += 1
            quitting = True
            continue

        if action == SKIP:
            actions.append(
                WalkerAction(
                    slide_id=outcome.slide_id,
                    role=outcome.role,
                    direction=outcome.direction,
                    action=SKIP,
                    target_path=target_path,
                )
            )
            result.pairs_skipped += 1
            click.echo(f"skipped {outcome.slide_id}/{outcome.role}")
            continue

        if action == EDIT:
            proposal = outcome.proposal
            seed = proposal.proposed_text if proposal is not None else ""
            edited = _safe_edit(seed, edit_fn)
            if edited is None:
                actions.append(
                    WalkerAction(
                        slide_id=outcome.slide_id,
                        role=outcome.role,
                        direction=outcome.direction,
                        action=SKIP,
                        target_path=target_path,
                        error="editor did not produce content; treated as skip",
                    )
                )
                result.pairs_skipped += 1
                click.echo(
                    f"editor did not return new content; skipped {outcome.slide_id}/{outcome.role}"
                )
                continue
            new_text = edited
            telemetry_action = EDIT
            counter_attr = "pairs_edited"
        else:
            assert action == APPLY
            proposal = outcome.proposal
            new_text = proposal.proposed_text if proposal is not None else ""
            telemetry_action = APPLY
            counter_attr = "pairs_accepted"

        try:
            state = file_state.setdefault(target_path, FileState.load(target_path))
            state.replace_body(outcome, new_text)
            state.flush()
            record_snapshot(
                options.snapshot_cache,
                result=result,
                outcome=outcome,
                new_target_text=new_text,
            )
        except Exception as exc:  # noqa: BLE001 — surface as a walker error
            actions.append(
                WalkerAction(
                    slide_id=outcome.slide_id,
                    role=outcome.role,
                    direction=outcome.direction,
                    action=SKIP,
                    target_path=target_path,
                    error=f"write failed: {exc}",
                )
            )
            result.pairs_skipped += 1
            click.echo(
                f"error: failed to write {target_path}: {exc}; "
                f"skipped {outcome.slide_id}/{outcome.role}",
                err=True,
            )
            continue

        setattr(result, counter_attr, getattr(result, counter_attr) + 1)
        actions.append(
            WalkerAction(
                slide_id=outcome.slide_id,
                role=outcome.role,
                direction=outcome.direction,
                action=telemetry_action,
                target_path=target_path,
            )
        )
        click.echo(f"{telemetry_action} {outcome.slide_id}/{outcome.role} → {target_path}")

    return actions


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _print_header(outcome: PairOutcome, target_path: Path) -> None:
    cached_tag = " (cached)" if outcome.cached else ""
    click.echo(
        f"propose {outcome.slide_id}/{outcome.role} "
        f"({outcome.direction}) de:{outcome.de_line} "
        f"en:{outcome.en_line}{cached_tag}" + (f" — {outcome.reason}" if outcome.reason else "")
    )
    click.echo(f"  target: {target_path}")


def _default_prompt(prompt: str) -> str:
    answer: str = click.prompt(prompt, default="s", show_default=True)
    return answer.strip().lower()


def _default_edit(seed: str) -> str | None:
    return click.edit(seed, extension=".md")


def _prompt_action(prompt_fn: Callable[[str], str]) -> str:
    """Loop until the user picks a known action."""
    while True:
        raw = prompt_fn("[a]pply / [s]kip / [e]dit / [q]uit")
        first = (raw or "").strip().lower()[:1]
        if first == "a":
            return APPLY
        if first == "s" or first == "":
            return SKIP
        if first == "e":
            return EDIT
        if first == "q":
            return QUIT
        click.echo("unknown choice; type a / s / e / q")


def _safe_edit(seed: str, edit_fn: Callable[[str], str | None]) -> str | None:
    try:
        result = edit_fn(seed)
    except click.UsageError as exc:
        click.echo(f"editor unavailable: {exc}", err=True)
        return None
    if result is None:
        return None
    # ``click.edit`` returns the file contents verbatim; trim a single
    # trailing newline that most editors add so we don't blow up cell
    # spacing on every accept.
    if result.endswith("\n"):
        result = result[:-1]
    return result
