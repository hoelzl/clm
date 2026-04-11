"""Tests for ``clm build --only-sections --watch`` filtering.

These tests exercise the ``selected_section_source_dirs`` guard in
``FileEventHandler``. A full watchdog + real-filesystem integration test
would require running the whole build loop, so we instead drive the
handler's event callbacks directly with mock courses/backends — the same
pattern as ``tests/cli/test_watch_mode.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from clm.cli.file_event_handler import FileEventHandler


class MockEvent:
    """Mock watchdog event."""

    def __init__(self, src_path: str, dest_path: str | None = None):
        self.src_path = src_path
        self.dest_path = dest_path


@pytest.fixture
def mock_course():
    course = MagicMock()
    # find_course_file returns None by default; individual tests override
    # it when they want to simulate a file that belongs to the filtered
    # course.
    course.find_course_file = MagicMock(return_value=None)
    course.process_file = AsyncMock()
    course.add_file = MagicMock(return_value=MagicMock())
    return course


@pytest.fixture
def mock_backend():
    backend = MagicMock()
    backend.delete_dependencies = AsyncMock()
    backend.cancel_jobs_for_file = AsyncMock(return_value=0)
    return backend


@pytest.fixture
def section_tree(tmp_path):
    """Create a three-section source tree and return its roots.

    ``section1/`` and ``section2/`` each contain a single ``.py`` file.
    ``unselected/`` is a sibling directory meant to simulate a section
    that was not picked up by ``--only-sections``.
    """
    section1 = tmp_path / "section1"
    section2 = tmp_path / "section2"
    unselected = tmp_path / "unselected"
    for d in (section1, section2, unselected):
        d.mkdir()

    (section1 / "slides_intro.py").write_text("# slides_intro\n", encoding="utf-8")
    (section2 / "slides_more.py").write_text("# slides_more\n", encoding="utf-8")
    (unselected / "slides_ignored.py").write_text("# slides_ignored\n", encoding="utf-8")

    return {
        "root": tmp_path,
        "section1": section1,
        "section2": section2,
        "unselected": unselected,
    }


class TestCreationEventFiltering:
    """``on_created`` should drop paths outside the selected sections."""

    @pytest.mark.asyncio
    async def test_creation_in_selected_section_is_added(
        self, mock_course, mock_backend, section_tree
    ):
        loop = asyncio.get_running_loop()
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=loop,
            debounce_delay=0.05,
            patterns=["*"],
            selected_section_source_dirs={section_tree["section1"]},
        )

        new_file = section_tree["section1"] / "slides_new.py"
        new_file.write_text("# new\n", encoding="utf-8")

        handler.on_created(MockEvent(str(new_file)))
        await asyncio.sleep(0.1)

        assert mock_course.add_file.call_count == 1

    @pytest.mark.asyncio
    async def test_creation_in_unselected_section_is_ignored(
        self, mock_course, mock_backend, section_tree
    ):
        loop = asyncio.get_running_loop()
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=loop,
            debounce_delay=0.05,
            patterns=["*"],
            selected_section_source_dirs={section_tree["section1"]},
        )

        ignored_file = section_tree["unselected"] / "slides_extra.py"
        ignored_file.write_text("# extra\n", encoding="utf-8")

        handler.on_created(MockEvent(str(ignored_file)))
        await asyncio.sleep(0.1)

        assert mock_course.add_file.call_count == 0

    @pytest.mark.asyncio
    async def test_no_filter_permits_all_creations(self, mock_course, mock_backend, section_tree):
        """Without ``selected_section_source_dirs``, every create event
        flows through to ``add_file``."""
        loop = asyncio.get_running_loop()
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=loop,
            debounce_delay=0.05,
            patterns=["*"],
        )

        unselected = section_tree["unselected"] / "slides_extra.py"
        unselected.write_text("# extra\n", encoding="utf-8")

        handler.on_created(MockEvent(str(unselected)))
        await asyncio.sleep(0.1)

        assert mock_course.add_file.call_count == 1

    @pytest.mark.asyncio
    async def test_multiple_selected_dirs(self, mock_course, mock_backend, section_tree):
        """Passing multiple selected source dirs allows any of them."""
        loop = asyncio.get_running_loop()
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=loop,
            debounce_delay=0.05,
            patterns=["*"],
            selected_section_source_dirs={
                section_tree["section1"],
                section_tree["section2"],
            },
        )

        f1 = section_tree["section1"] / "a.py"
        f2 = section_tree["section2"] / "b.py"
        f1.write_text("# a\n", encoding="utf-8")
        f2.write_text("# b\n", encoding="utf-8")

        handler.on_created(MockEvent(str(f1)))
        handler.on_created(MockEvent(str(f2)))
        await asyncio.sleep(0.1)

        assert mock_course.add_file.call_count == 2


class TestModificationEventFiltering:
    """``on_modified`` does not need an explicit guard because
    ``course.find_course_file`` returns ``None`` for files outside the
    filtered ``course.files`` list. We still verify the behaviour here
    so the guarantee is documented and tested."""

    @pytest.mark.asyncio
    async def test_modification_in_selected_section_triggers_process(
        self, mock_course, mock_backend, section_tree
    ):
        mock_course.find_course_file.return_value = True  # pretend it's known

        loop = asyncio.get_running_loop()
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=loop,
            debounce_delay=0.05,
            patterns=["*"],
            selected_section_source_dirs={section_tree["section1"]},
        )

        f1 = section_tree["section1"] / "slides_intro.py"
        handler.on_modified(MockEvent(str(f1)))
        await asyncio.sleep(0.1)

        assert mock_course.process_file.call_count == 1

    @pytest.mark.asyncio
    async def test_modification_in_unselected_section_is_noop(
        self, mock_course, mock_backend, section_tree
    ):
        """Modified file outside the filtered course is a no-op because
        ``course.find_course_file`` returns ``None``."""
        mock_course.find_course_file.return_value = None

        loop = asyncio.get_running_loop()
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=loop,
            debounce_delay=0.05,
            patterns=["*"],
            selected_section_source_dirs={section_tree["section1"]},
        )

        ignored = section_tree["unselected"] / "slides_ignored.py"
        handler.on_modified(MockEvent(str(ignored)))
        await asyncio.sleep(0.1)

        # `on_modified` still fires, but `on_file_modified` short-circuits
        # because find_course_file returns None.
        assert mock_course.process_file.call_count == 0


class TestIsInSelectedSectionsHelper:
    """Direct tests for the private guard helper so edge cases are
    documented."""

    def test_returns_true_when_no_filter(self, mock_course, mock_backend, tmp_path):
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=tmp_path,
            loop=MagicMock(),
            patterns=["*"],
        )
        assert handler._is_in_selected_sections(tmp_path / "anything.py") is True

    def test_returns_true_when_path_under_selected_dir(
        self, mock_course, mock_backend, section_tree
    ):
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=MagicMock(),
            patterns=["*"],
            selected_section_source_dirs={section_tree["section1"]},
        )
        assert (
            handler._is_in_selected_sections(section_tree["section1"] / "slides_intro.py") is True
        )

    def test_returns_true_for_nested_path(self, mock_course, mock_backend, section_tree):
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=MagicMock(),
            patterns=["*"],
            selected_section_source_dirs={section_tree["section1"]},
        )
        # Nested file.
        nested = section_tree["section1"] / "deep" / "slides_nested.py"
        nested.parent.mkdir()
        nested.write_text("# nested\n", encoding="utf-8")
        assert handler._is_in_selected_sections(nested) is True

    def test_returns_false_when_path_is_sibling(self, mock_course, mock_backend, section_tree):
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=MagicMock(),
            patterns=["*"],
            selected_section_source_dirs={section_tree["section1"]},
        )
        assert (
            handler._is_in_selected_sections(section_tree["unselected"] / "slides_ignored.py")
            is False
        )

    def test_empty_set_rejects_everything(self, mock_course, mock_backend, section_tree):
        handler = FileEventHandler(
            backend=mock_backend,
            course=mock_course,
            data_dir=section_tree["root"],
            loop=MagicMock(),
            patterns=["*"],
            selected_section_source_dirs=set(),
        )
        assert (
            handler._is_in_selected_sections(section_tree["section1"] / "slides_intro.py") is False
        )
