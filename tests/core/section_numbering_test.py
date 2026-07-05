"""Tests for :meth:`clm.core.section.Section.add_notebook_numbers`."""

from pathlib import Path
from types import SimpleNamespace

from clm.core.course_files.notebook_file import NotebookFile
from clm.core.section import Section
from clm.core.utils.text_utils import Text


def _section_with_notebooks(paths: list[Path]) -> tuple[Section, list[NotebookFile]]:
    notebooks = [NotebookFile(path=path, course=None, topic=None) for path in paths]
    topic = SimpleNamespace(files=notebooks)
    section = Section(name=Text(de="s", en="s"), course=None, topics=[topic])
    return section, notebooks


def test_same_file_name_in_different_folders_gets_distinct_numbers():
    # Regression: five topic folders each containing a `workshop.py` all
    # received number 1 because the fallback key was the bare file name.
    section, notebooks = _section_with_notebooks(
        [
            Path("course/intro/workshop.py"),
            Path("course/basics/workshop.py"),
            Path("course/advanced/workshop.py"),
        ]
    )
    section.add_notebook_numbers()
    assert [nb.number_in_section for nb in notebooks] == [1, 2, 3]


def test_split_companions_in_same_folder_share_one_slot():
    section, notebooks = _section_with_notebooks(
        [
            Path("course/intro/slides_foo.de.py"),
            Path("course/intro/slides_foo.en.py"),
            Path("course/intro/slides_bar.py"),
        ]
    )
    section.add_notebook_numbers()
    assert [nb.number_in_section for nb in notebooks] == [1, 1, 2]


def test_same_split_family_name_in_different_folders_gets_distinct_slots():
    section, notebooks = _section_with_notebooks(
        [
            Path("course/intro/slides_workshop.de.py"),
            Path("course/intro/slides_workshop.en.py"),
            Path("course/basics/slides_workshop.de.py"),
            Path("course/basics/slides_workshop.en.py"),
        ]
    )
    section.add_notebook_numbers()
    assert [nb.number_in_section for nb in notebooks] == [1, 1, 2, 2]
