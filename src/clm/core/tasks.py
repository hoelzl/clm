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

Unknown placeholders are a hard error so typos surface before anything
runs. Literal braces are written ``{{`` / ``}}``.

This module is CLI-free on purpose: the spec validator
(:mod:`clm.slides.spec_validator`) uses it to check ``<tasks>`` blocks
without importing the Click command tree.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

#: Placeholders available in task steps, with a short description each
#: (used in error messages and documentation).
KNOWN_PLACEHOLDERS: dict[str, str] = {
    "spec": "absolute path of the spec file passed to `clm run`",
}

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
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
            known = ", ".join(f"{{{p}}}" for p in sorted(values))
            raise TaskStepError(f"unknown placeholder {{{name}}} (known placeholders: {known})")
        return values[name]

    substituted = _PLACEHOLDER_RE.sub(_replace, masked)
    return substituted.replace(_OPEN_SENTINEL, "{").replace(_CLOSE_SENTINEL, "}")


def resolve_step(step: str, *, spec_path: Path) -> list[str]:
    """Resolve one step's text into the argv tokens to pass after ``clm``.

    Args:
        step: The step text from the spec (without the leading ``clm``).
        spec_path: The spec file the task was loaded from; expands ``{spec}``.

    Raises:
        TaskStepError: For empty steps, unbalanced quotes, or unknown
            placeholders.
    """
    if not step.strip():
        raise TaskStepError("step is empty")
    try:
        tokens = shlex.split(step, posix=True)
    except ValueError as e:
        raise TaskStepError(f"cannot parse step: {e}") from None
    if not tokens:
        raise TaskStepError("step is empty")

    values = {"spec": str(spec_path.resolve())}
    return [substitute_placeholders(token, values) for token in tokens]
