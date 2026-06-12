"""Resolution of ``<task>`` steps from the course spec (``clm run``).

A step is one clm command line without the leading ``clm`` (e.g.
``export calendar {spec} --channel jan``). Resolution turns the step text
into an argv token list in two stages:

1. Tokenize with POSIX ``shlex`` rules — quoting works as in a POSIX
   shell, and **paths inside steps must use forward slashes** (backslash
   is the shlex escape character). This keeps specs portable; clm accepts
   forward-slash paths on every platform.
2. Substitute ``{placeholder}`` tokens *inside each token*. Substituting
   after tokenization means substituted values (e.g. a Windows spec path
   containing backslashes or spaces) are never re-parsed by shlex.

Besides ``{spec}``, steps may reference the extra arguments passed after
the spec file (``clm run TASK SPEC [ARGS]...``, issue #342): ``{args}``
expands — as a standalone token only — to one argv token per extra
argument (never re-quoted or re-parsed), and ``{1}``, ``{2}``, … place
individual arguments.

Unknown placeholders are a hard error so typos surface before anything
runs. Literal braces are written ``{{`` / ``}}``.

This module is CLI-free on purpose: the spec validator
(:mod:`clm.slides.spec_validator`) uses it to check ``<tasks>`` blocks
without importing the Click command tree. The validator resolves steps
without runtime arguments (``args=None``), which accepts ``{args}``/``{n}``
references with stand-in values — the actual values only exist at
``clm run`` time.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from pathlib import Path

#: Placeholders available in task steps, with a short description each
#: (used in error messages and documentation). Positional placeholders
#: ``{1}``, ``{2}``, … are recognized structurally (see
#: :data:`_POSITIONAL_RE`) and not listed here.
KNOWN_PLACEHOLDERS: dict[str, str] = {
    "spec": "absolute path of the spec file passed to `clm run`",
    "args": "all extra arguments passed to `clm run TASK SPEC [ARGS]...` "
    "(standalone token only; one argv token per argument)",
}

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
#: ``{1}``, ``{2}``, … — 1-based references to single extra arguments.
_POSITIONAL_RE = re.compile(r"[1-9][0-9]*")
# Sentinels for the ``{{`` / ``}}`` escapes; chr(0) cannot appear in XML text.
_OPEN_SENTINEL = "\x00<"
_CLOSE_SENTINEL = "\x00>"


class TaskStepError(Exception):
    """A task step cannot be resolved (bad placeholder, unparseable text)."""


def substitute_placeholders(token: str, values: dict[str, str]) -> str:
    """Replace ``{name}`` placeholders in *token* with their values.

    Raises:
        TaskStepError: For a placeholder not present in *values*.
    """
    masked = token.replace("{{", _OPEN_SENTINEL).replace("}}", _CLOSE_SENTINEL)

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            if _POSITIONAL_RE.fullmatch(name):
                raise TaskStepError(
                    f"step references argument {{{name}}} but only "
                    f"{_positional_count(values)} extra argument(s) were given"
                )
            known = ", ".join(f"{{{p}}}" for p in sorted(values))
            raise TaskStepError(f"unknown placeholder {{{name}}} (known placeholders: {known})")
        return values[name]

    substituted = _PLACEHOLDER_RE.sub(_replace, masked)
    return substituted.replace(_OPEN_SENTINEL, "{").replace(_CLOSE_SENTINEL, "}")


def _positional_count(values: dict[str, str]) -> int:
    return sum(1 for name in values if _POSITIONAL_RE.fullmatch(name))


def step_argument_usage(step: str) -> tuple[bool, int]:
    """Report how one step's text uses the extra ``clm run`` arguments.

    Returns ``(uses_args, max_positional)``: whether ``{args}`` appears, and
    the highest ``{n}`` referenced (0 when none). Unparseable steps report
    ``(False, 0)`` — resolution surfaces the parse error with full context.
    """
    try:
        tokens = shlex.split(step, posix=True)
    except ValueError:
        return False, 0
    uses_args = False
    max_positional = 0
    for token in tokens:
        masked = token.replace("{{", _OPEN_SENTINEL).replace("}}", _CLOSE_SENTINEL)
        for name in _PLACEHOLDER_RE.findall(masked):
            if name == "args":
                uses_args = True
            elif _POSITIONAL_RE.fullmatch(name):
                max_positional = max(max_positional, int(name))
    return uses_args, max_positional


def resolve_step(
    step: str,
    *,
    spec_path: Path,
    args: Sequence[str] | None = None,
) -> list[str]:
    """Resolve one step's text into the argv tokens to pass after ``clm``.

    Args:
        step: The step text from the spec (without the leading ``clm``).
        spec_path: The spec file the task was loaded from; expands ``{spec}``.
        args: The extra arguments from ``clm run TASK SPEC [ARGS]...``
            (issue #342); expand ``{args}`` and ``{1}``, ``{2}``, ….
            ``None`` (the default) means *validation mode*: argument
            placeholders are accepted and replaced with stand-in values, so
            the spec validator can check steps without runtime arguments.

    Raises:
        TaskStepError: For empty steps, unbalanced quotes, unknown
            placeholders, an out-of-range ``{n}``, or ``{args}`` embedded
            in a larger token.
    """
    if not step.strip():
        raise TaskStepError("step is empty")
    try:
        tokens = shlex.split(step, posix=True)
    except ValueError as e:
        raise TaskStepError(f"cannot parse step: {e}") from None
    if not tokens:
        raise TaskStepError("step is empty")

    validation_mode = args is None
    arg_list = [] if args is None else list(args)

    values = {"spec": str(spec_path.resolve())}
    if validation_mode:
        # Stand-ins for every {n} actually referenced; values are only used
        # for the command-tree check, where argument positions never matter.
        for token in tokens:
            masked = token.replace("{{", _OPEN_SENTINEL).replace("}}", _CLOSE_SENTINEL)
            for name in _PLACEHOLDER_RE.findall(masked):
                if _POSITIONAL_RE.fullmatch(name):
                    values[name] = f"<{name}>"
    else:
        values.update({str(i): arg for i, arg in enumerate(arg_list, start=1)})

    resolved: list[str] = []
    for token in tokens:
        masked = token.replace("{{", _OPEN_SENTINEL).replace("}}", _CLOSE_SENTINEL)
        if masked == "{args}":
            # A bare {args} token expands to one argv token per argument —
            # values are never joined, re-quoted, or re-parsed.
            resolved.extend(["<args>"] if validation_mode else arg_list)
            continue
        if "{args}" in masked:
            raise TaskStepError(
                "{args} must be a standalone token (it expands to one argv "
                "token per argument); use {1}, {2}, … to place arguments "
                "inside a larger token"
            )
        resolved.append(substitute_placeholders(token, values))
    return resolved
