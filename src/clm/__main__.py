"""Module entry point so ``python -m clm`` works.

``clm run`` re-invokes the CLI through ``sys.executable -m clm`` (not a
PATH lookup of ``clm``) so task steps are guaranteed to use the same
interpreter and virtual environment as the parent process.
"""

from clm.cli.main import cli

if __name__ == "__main__":
    cli()
