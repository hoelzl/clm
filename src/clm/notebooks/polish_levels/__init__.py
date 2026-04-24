"""Polish levels for speaker-note cleanup.

Each level corresponds to a system-prompt file in this package directory and
controls how aggressively the LLM edits the input text.

``verbatim`` is a special no-LLM passthrough level — it has no prompt file.
Calling ``load_prompt(PolishLevel.verbatim)`` raises ``ValueError``.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class PolishLevel(StrEnum):
    """Named polish levels for speaker-note cleanup.

    Values match the level names so that ``PolishLevel("standard")`` works
    and ``str(PolishLevel.standard) == "standard"``.
    """

    verbatim = "verbatim"
    light = "light"
    standard = "standard"
    heavy = "heavy"
    rewrite = "rewrite"


_PACKAGE_DIR = Path(__file__).parent


def load_prompt(level: PolishLevel) -> str:
    """Load the system-prompt text for *level* from the package directory.

    Args:
        level: The desired polish level.

    Returns:
        The prompt text as a string (trailing newline preserved).

    Raises:
        ValueError: If *level* is ``PolishLevel.verbatim``, which has no
            prompt file because verbatim is handled as a no-LLM passthrough.
    """
    if level == PolishLevel.verbatim:
        raise ValueError(
            "PolishLevel.verbatim has no system prompt: verbatim mode is a "
            "no-LLM passthrough and must be handled before calling load_prompt()."
        )
    prompt_path = _PACKAGE_DIR / f"{level}.md"
    return prompt_path.read_text(encoding="utf-8")
