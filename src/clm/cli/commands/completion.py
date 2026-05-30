"""``clm completion`` command — emit shell completion activation scripts.

Click 8.x supports Bash, Zsh, and Fish completion natively. PowerShell —
the primary shell on this Windows-first project — is added by CLM via
:mod:`clm.cli.completion`. This command prints the activation script for a
chosen shell so users can enable tab completion.
"""

import click

from clm.cli.completion import SUPPORTED_SHELLS, get_completion_source

# Per-shell instructions shown when ``--help`` is used or as a guide for
# users on how to install the emitted script.
_INSTALL_HINTS: dict[str, str] = {
    "bash": (
        "Add to ~/.bashrc:\n"
        '    eval "$(clm completion bash)"\n'
        "Or write it once:\n"
        "    clm completion bash > ~/.clm-complete.bash\n"
        "    echo 'source ~/.clm-complete.bash' >> ~/.bashrc"
    ),
    "zsh": ('Add to ~/.zshrc:\n    eval "$(clm completion zsh)"'),
    "fish": (
        "Write to the fish completions dir:\n"
        "    clm completion fish > ~/.config/fish/completions/clm.fish"
    ),
    "powershell": (
        "Add to your PowerShell profile ($PROFILE):\n"
        "    clm completion powershell | Out-String | Invoke-Expression\n"
        "Or persist it:\n"
        "    clm completion powershell >> $PROFILE"
    ),
}


@click.command(name="completion")
@click.argument("shell", type=click.Choice(SUPPORTED_SHELLS))
@click.option(
    "--install-hint",
    is_flag=True,
    help="Print instructions for installing the script instead of the script itself.",
)
def completion_cmd(shell: str, install_hint: bool) -> None:
    """Emit a shell completion script for SHELL.

    Supported shells: bash, zsh, fish (native Click support) and
    powershell (added by CLM). Pipe or eval the output to enable
    tab completion for the ``clm`` command.

    \b
    Examples:
      # Bash / Zsh (current session)
      eval "$(clm completion bash)"

      # PowerShell (current session)
      clm completion powershell | Out-String | Invoke-Expression

      # Show how to make it permanent
      clm completion powershell --install-hint
    """
    if install_hint:
        click.echo(_INSTALL_HINTS[shell])
        return

    click.echo(get_completion_source(shell))
