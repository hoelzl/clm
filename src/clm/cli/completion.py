"""Shell completion support for the ``clm`` CLI.

Click 8.x ships native completion for Bash, Zsh, and Fish out of the box
(via the ``_CLM_COMPLETE={shell}_source`` protocol). It does **not**
support PowerShell, which is the primary shell on this Windows-first
project.

This module fills that gap by registering a :class:`PowerShellComplete`
subclass with Click's completion machinery. It reuses Click's completion
protocol entirely — the only PowerShell-specific pieces are:

* a ``source`` script that wires up ``Register-ArgumentCompleter`` and
  forwards the current command line to ``clm`` via environment variables,
  and
* :meth:`PowerShellComplete.get_completion_args` /
  :meth:`PowerShellComplete.format_completion`, which translate between
  PowerShell's calling convention and Click's ``CompletionItem`` model.

The public entry point is :func:`get_completion_source`, used by the
``clm completion`` command.
"""

import logging
import os

import click
from click.shell_completion import (
    CompletionItem,
    ShellComplete,
    add_completion_class,
    get_completion_class,
)

# ``split_arg_string`` lives in ``click.shell_completion`` on Click 8.2+
# (importing it from ``click.parser`` is deprecated there and removed in
# Click 9). On Click 8.1 it is only available from ``click.parser``. CLM
# supports both, so try the modern location first.
try:
    from click.shell_completion import split_arg_string
except ImportError:  # pragma: no cover - Click 8.1 fallback
    from click.parser import split_arg_string  # type: ignore[no-redef, unused-ignore]

logger = logging.getLogger(__name__)

#: Shells with native Click support. ``clm completion <shell>`` delegates
#: to Click's built-in generator for these.
NATIVE_SHELLS: tuple[str, ...] = ("bash", "zsh", "fish")

#: Name PowerShell completion is registered under. Completion instructions
#: arrive as ``powershell_source`` / ``powershell_complete``.
POWERSHELL_SHELL_NAME = "powershell"

#: All shells understood by ``clm completion``.
SUPPORTED_SHELLS: tuple[str, ...] = (*NATIVE_SHELLS, POWERSHELL_SHELL_NAME)


# PowerShell registration script.
#
# ``Register-ArgumentCompleter`` invokes the scriptblock with
# ``$wordToComplete``, ``$commandAst`` and ``$cursorPosition``. We forward
# the full command-line text and cursor position to ``clm`` through
# environment variables and let the Python side compute the completions,
# then emit one ``CompletionResult`` per line returned.
#
# ``%(prog_name)s`` is the executable name and ``%(complete_var)s`` is the
# completion-instruction env var (``_CLM_COMPLETE``).
_SOURCE_POWERSHELL = """\
Register-ArgumentCompleter -Native -CommandName %(prog_name)s -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $env:%(complete_var)s = "powershell_complete"
    $env:COMP_LINE = $commandAst.ToString()
    $env:COMP_POINT = $cursorPosition
    $env:COMP_WORD_TO_COMPLETE = $wordToComplete
    try {
        %(prog_name)s | ForEach-Object {
            $parts = $_ -split "`t", 2
            $value = $parts[0]
            if ($parts.Length -gt 1 -and $parts[1]) {
                $tooltip = $parts[1]
            } else {
                $tooltip = $value
            }
            [System.Management.Automation.CompletionResult]::new(
                $value, $value, 'ParameterValue', $tooltip
            )
        }
    } finally {
        Remove-Item Env:\\%(complete_var)s -ErrorAction SilentlyContinue
        Remove-Item Env:\\COMP_LINE -ErrorAction SilentlyContinue
        Remove-Item Env:\\COMP_POINT -ErrorAction SilentlyContinue
        Remove-Item Env:\\COMP_WORD_TO_COMPLETE -ErrorAction SilentlyContinue
    }
}
"""


class PowerShellComplete(ShellComplete):
    """Click shell completion for PowerShell.

    PowerShell is not supported by Click natively. This subclass plugs
    into Click's completion protocol so PowerShell gets the same
    context-aware completions as Bash/Zsh/Fish, including command names,
    options, and Click-provided dynamic value completions.
    """

    name = POWERSHELL_SHELL_NAME
    source_template = _SOURCE_POWERSHELL

    def get_completion_args(self) -> tuple[list[str], str]:
        """Derive ``(args, incomplete)`` from the env vars set by the
        PowerShell registration script.

        PowerShell hands us the entire command line (``COMP_LINE``), the
        cursor position (``COMP_POINT``), and the word currently being
        completed (``COMP_WORD_TO_COMPLETE``). We tokenise the line up to
        the cursor, drop the program name, and treat the final partial
        token as the incomplete value.
        """
        comp_line = os.environ.get("COMP_LINE", "")
        comp_point_raw = os.environ.get("COMP_POINT", str(len(comp_line)))
        word_to_complete = os.environ.get("COMP_WORD_TO_COMPLETE", "")

        try:
            comp_point = int(comp_point_raw)
        except ValueError:
            comp_point = len(comp_line)

        # Only consider the part of the line before the cursor.
        truncated = comp_line[:comp_point]
        cwords = split_arg_string(truncated)

        # Drop the program name (first token).
        args = cwords[1:] if cwords else []

        # If the cursor sits mid-token, the last parsed token is the
        # incomplete word; otherwise (cursor after whitespace) the
        # incomplete word is empty. PowerShell's ``$wordToComplete`` tells
        # us which case we are in.
        if word_to_complete:
            incomplete = word_to_complete
            # Remove the partial word from args if it was tokenised in.
            if args and args[-1] == incomplete:
                args = args[:-1]
        else:
            incomplete = ""

        return args, incomplete

    def format_completion(self, item: CompletionItem) -> str:
        """Format a completion as ``value<TAB>help``.

        The PowerShell registration script splits on the tab and uses the
        help text as the tooltip shown in the completion menu.
        """
        help_text = item.help or ""
        return f"{item.value}\t{help_text}"


def register_powershell_completion() -> None:
    """Register :class:`PowerShellComplete` with Click.

    Idempotent: registering the same name twice is harmless. Called at CLI
    import time so the ``_CLM_COMPLETE=powershell_complete`` protocol works
    once a user has installed the completion script.
    """
    add_completion_class(PowerShellComplete)


def get_completion_source(shell: str, prog_name: str = "clm") -> str:
    """Return the activation script for ``shell``.

    For Bash/Zsh/Fish this is exactly what Click's native generator emits;
    for PowerShell it is the :data:`_SOURCE_POWERSHELL` registration
    script. The returned text is meant to be ``eval``-ed (POSIX shells) or
    dot-sourced / piped to ``Invoke-Expression`` (PowerShell).

    Args:
        shell: One of :data:`SUPPORTED_SHELLS`.
        prog_name: Executable name to wire completion for. Defaults to
            ``clm``.

    Returns:
        The completion script as a string.

    Raises:
        ValueError: If ``shell`` is not a supported shell.
    """
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(
            f"Unsupported shell {shell!r}. Choose from: {', '.join(SUPPORTED_SHELLS)}."
        )

    # Ensure PowerShell is registered before we look it up.
    register_powershell_completion()

    complete_var = f"_{prog_name.replace('-', '_').upper()}_COMPLETE"

    comp_cls = get_completion_class(shell)
    if comp_cls is None:  # pragma: no cover - guarded by SUPPORTED_SHELLS
        raise ValueError(f"No completion class registered for {shell!r}.")

    # ``cli`` is only needed for the ``complete`` instruction, not for
    # ``source``; ``source()`` formats the template with prog/var names.
    comp = comp_cls(
        cli=click.Group(),
        ctx_args={},
        prog_name=prog_name,
        complete_var=complete_var,
    )
    return comp.source()
