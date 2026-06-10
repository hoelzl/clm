"""Guard the lazy-import structure of the CLI entry point.

``clm.cli.main`` loads command modules on demand (``LazyGroup``), and the
``clm`` / ``clm.core`` / ``clm.infrastructure`` package inits resolve their
convenience exports lazily (PEP 562). A single stray module-level import
can silently reintroduce the whole core/infrastructure chain into every
CLI start — these tests fail loudly when that happens.
"""

import json
import subprocess
import sys

# Modules that must NOT load when the CLI merely starts up. Each is the
# head of an expensive import chain (course model, SQLite backend stack,
# pydantic message classes, slide tooling).
_HEAVY_MODULES = (
    "clm.core.course",
    "clm.core.course_spec",
    "clm.infrastructure.backends.sqlite_backend",
    "clm.infrastructure.messaging.base_classes",
    "clm.cli.commands.build",
    "clm.cli.commands.slides",
    "clm.slides",
)


def _modules_after(statement: str) -> set[str]:
    """Return sys.modules after running ``statement`` in a fresh interpreter."""
    code = f"{statement}\nimport json, sys\nprint(json.dumps(sorted(sys.modules)))"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(json.loads(result.stdout))


class TestLazyCliImports:
    def test_importing_cli_main_does_not_load_command_modules(self):
        loaded = _modules_after("import clm.cli.main")
        offenders = [m for m in _HEAVY_MODULES if m in loaded]
        assert not offenders, (
            f"Importing clm.cli.main eagerly loaded {offenders}. "
            f"Command modules must be imported lazily via LazyGroup — "
            f"see clm/cli/_lazy_group.py."
        )

    def test_importing_clm_package_does_not_load_core_model(self):
        loaded = _modules_after("import clm")
        assert "clm.core.course" not in loaded, (
            "Importing the clm package eagerly loaded clm.core.course; "
            "the convenience exports in clm/__init__.py must stay lazy (PEP 562)."
        )


class TestLazyExportsCompatibility:
    """The lazy exports must look exactly like the old eager imports."""

    def test_clm_package_exports_resolve(self):
        from clm import Course, CourseFile, CourseSpec, Section, Topic
        from clm.core.course import Course as DirectCourse

        assert Course is DirectCourse
        assert all(isinstance(cls, type) for cls in (CourseFile, CourseSpec, Section, Topic))

    def test_clm_core_exports_resolve(self):
        from clm.core import Course, CourseSpecError

        assert isinstance(Course, type)
        assert issubclass(CourseSpecError, Exception)

    def test_clm_infrastructure_exports_resolve(self):
        from clm.infrastructure import Backend, Operation

        assert isinstance(Backend, type)
        assert isinstance(Operation, type)

    def test_main_compat_attributes_resolve(self):
        import click

        from clm.cli import main

        assert isinstance(main.BuildConfig, type)
        assert callable(main.initialize_paths_and_course)
        assert callable(main._is_ci_environment)
        assert isinstance(main.build, click.Command)
        assert isinstance(main.db, click.Group)

    def test_unknown_attribute_raises(self):
        import pytest

        from clm.cli import main

        with pytest.raises(AttributeError):
            main.does_not_exist  # noqa: B018
