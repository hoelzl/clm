"""Windows-resilient rotating file log handler.

On Windows a file cannot be renamed while another process holds it open.
CLM runs the build CLI and one or more worker subprocesses, and they all
attach a :class:`logging.handlers.RotatingFileHandler` to the *same* log
file. When the file crosses the rollover threshold, whichever process trips
the rollover first calls ``doRollover()``, which tries to rename
``clm.log`` -> ``clm.log.1`` while a sibling process still has ``clm.log``
open. On Windows that rename raises ``PermissionError: [WinError 32] The
process cannot access the file because it is being used by another
process``.

The stock handler funnels that exception through ``logging.Handler.handleError``,
which prints a full traceback to ``stderr`` for *every* log record emitted
during the contended window. On a long build this floods the console and
buries the real build output (issue #143, sub-bug B).

:class:`ResilientRotatingFileHandler` swallows the rollover failure: if the
rename cannot happen because the file is locked, it simply keeps writing to
the current file (which may grow slightly past ``maxBytes`` until a later,
uncontended rollover succeeds). Losing a rollover is harmless; flooding the
console is not.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)


class ResilientRotatingFileHandler(RotatingFileHandler):
    """A :class:`RotatingFileHandler` that tolerates locked-file rollovers.

    Behaves identically to the base handler except that an ``OSError``
    (which includes Windows ``PermissionError``/``WinError 32``) raised
    while rotating is caught and logged once at debug level rather than
    propagated to ``handleError``. The handler reopens its stream if the
    failed rollover closed it, so logging continues uninterrupted.
    """

    def doRollover(self) -> None:  # noqa: N802 - matches stdlib name
        try:
            super().doRollover()
        except OSError as exc:
            # The rollover could not rename the file (typically because a
            # sibling process on Windows still holds it open). Keep using
            # the current file rather than spamming a traceback per record.
            #
            # ``RotatingFileHandler.doRollover`` closes ``self.stream``
            # before attempting the rename, so if the rename failed we may
            # be left without an open stream. Reopen it so subsequent
            # ``emit`` calls do not fail.
            if self.stream is None and not self.delay:
                try:
                    self.stream = self._open()
                except OSError:
                    # If we cannot even reopen, leave the stream as None;
                    # the next emit() will attempt to open again. Do not
                    # raise — this path must never crash logging.
                    self.stream = None
            logger.debug(
                "Log rollover skipped (file locked by another process): %s",
                exc,
            )
