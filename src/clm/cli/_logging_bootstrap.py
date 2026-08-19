"""The console log handler CLM installs before a command configures logging.

Imported by :mod:`clm.cli.main` at startup, so it is **stdlib-only** on
purpose: pulling in ``clm.infrastructure`` here would undo the lazy-import
work that keeps ``clm --help`` fast (pinned by
``tests/cli/test_cli_startup.py``).

Why this exists at all, and why it is not ``logging.basicConfig``:

``main.py`` used to call ``logging.basicConfig(level=logging.INFO)`` at import
time. That installs a ``StreamHandler`` with **no level of its own**, so it
emits whatever the *logger* lets through — and
:func:`clm.cli.commands.shared.setup_logging` then raises the root logger to
``DEBUG`` so its file handler can capture everything. The combination turned
the console into a firehose that no flag could close: every third-party
``DEBUG`` record (``docker.utils.config``, ``urllib3.connectionpool``) printed
on every ``clm build``, and ``--log-level=warning`` did not stop it, because
that flag only sets the level of the ``clm`` logger.

So the handler here carries an explicit level, and ``setup_logging`` retires
it by tag rather than clearing the root logger — clearing it wholesale is what
tore out pytest's handlers and made an unrelated capture bug look like a
nightly flake (see ``_retire_previously_installed_handlers``).
"""

from __future__ import annotations

import logging

#: Marks a handler as installed by :func:`install_bootstrap_console_handler`,
#: so ``setup_logging`` can retire exactly this one and leave handlers owned by
#: pytest or an embedding application alone.
BOOTSTRAP_HANDLER_ATTR = "_clm_bootstrap_console_handler"

#: What the console shows before (or without) a command calling
#: ``setup_logging``. Warnings and errors, nothing else: anything below that is
#: diagnostic detail that belongs in the log file, which is what
#: ``--verbose-logging``'s help has always promised ("by default logs go to
#: file only").
BOOTSTRAP_CONSOLE_LEVEL = logging.WARNING

BOOTSTRAP_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def install_bootstrap_console_handler() -> logging.Handler | None:
    """Install the pre-command console handler; return it, or ``None``.

    Returns ``None`` without touching anything when the root logger already
    has handlers — someone else (pytest, an embedding application, a prior
    call) owns logging, and stacking a second console sink on top of theirs
    would double every line. This mirrors ``logging.basicConfig``'s own
    no-op-if-configured rule, which is the behaviour this replaced.
    """
    root = logging.getLogger()
    if root.handlers:
        return None

    handler = logging.StreamHandler()
    handler.setLevel(BOOTSTRAP_CONSOLE_LEVEL)
    handler.setFormatter(logging.Formatter(BOOTSTRAP_FORMAT))
    setattr(handler, BOOTSTRAP_HANDLER_ATTR, True)
    root.addHandler(handler)

    # The *logger* stays permissive so a later ``setup_logging`` can route
    # everything to the log file; the handler above is what keeps the console
    # quiet. Setting the logger to WARNING instead would discard records
    # before any file handler could see them.
    root.setLevel(logging.DEBUG)
    return handler


def retire_bootstrap_console_handlers(root: logging.Logger | None = None) -> None:
    """Remove the bootstrap handler(s), leaving every other handler in place.

    Called by ``setup_logging``, which installs the real console/file pair and
    is the authority on console output from that point on. Handlers are only
    detached, never closed: this one wraps ``sys.stderr``.
    """
    root = root if root is not None else logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, BOOTSTRAP_HANDLER_ATTR, False):
            root.removeHandler(handler)
