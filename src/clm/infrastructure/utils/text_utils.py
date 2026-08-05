"""Text sanitation utilities shared across layers.

ANSI escape handling lives in infrastructure (not the CLI) because worker
error output is scrubbed before categorization and database storage — the
CLI's display formatting merely reuses the same scrubber.
"""

import re

# Regex pattern for ANSI escape sequences
# Matches: ESC[ followed by any number of parameters and a final letter
# Also matches ESC followed by other common sequences
ANSI_ESCAPE_PATTERN = re.compile(
    r"""
    \x1b  # ESC character
    (?:
        \[  # CSI sequences: ESC[
        [0-9;]*  # Parameters (numbers and semicolons)
        [a-zA-Z]  # Final character
        |
        \]  # OSC sequences: ESC]
        [^\x07]*  # Content until BEL
        \x07  # BEL character
        |
        [@-Z\\-_]  # Other escape sequences (ESC followed by single char)
    )
    """,
    re.VERBOSE,
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text.

    Args:
        text: Text that may contain ANSI escape codes

    Returns:
        Text with all ANSI escape sequences removed
    """
    if not text:
        return text
    return ANSI_ESCAPE_PATTERN.sub("", text)
