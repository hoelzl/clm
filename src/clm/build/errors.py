"""Typed failures raised by the build engine (Phase 8 A4, #802).

The engine in :mod:`clm.build.engine` is callable without the Click CLI, so
option and spec problems surface as these exceptions instead of
``click.UsageError`` / ``click.ClickException``. The ``clm build`` command
converts them back into Click's error rendering at the entry point.
"""


class BuildOptionError(ValueError):
    """An option value (flag or ``CLM_*`` env var) failed validation.

    The CLI converts this to ``click.UsageError``; programmatic callers get
    a plain :class:`ValueError` subclass with the same message.
    """


class SpecValidationFailure(Exception):
    """The course spec parsed but failed validation.

    Raised by :func:`clm.build.engine.initialize_paths_and_course` after the
    validation errors have already been rendered (via
    :func:`clm.build.engine.report_validation_errors`). Carries only the
    summary message; the CLI converts it to ``click.ClickException``.
    """
