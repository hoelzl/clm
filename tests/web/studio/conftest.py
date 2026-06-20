"""Fixtures for Mobile Deck Studio tests.

Builds a minimal but realistic course on disk: a spec under ``course-specs/``
and a per-language deck under ``slides/<module>/<topic>/`` (CLM ships decks as
``.de.py`` / ``.en.py`` files, so ``(slide_id, role)`` is unique per file).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import pytest

from clm.web.studio.service import StudioService

# A single-language deck with two id'd markdown cells (distinct roles → distinct
# keys) and one shared, id-less code cell (not per-cell addressable → read-only).
DECK_SOURCE = dedent(
    """\
    # %% [markdown] lang="de" tags=["slide"] slide_id="intro-welcome"
    # Willkommen
    #
    # Schön, dass du da bist.

    # %% [markdown] lang="de" tags=["notes"] slide_id="intro-welcome"
    # Sprechernotizen hier.

    # %%
    print("hello")
    """
)

DECK_REL = "module_100_basics/topic_010_intro/slides_intro.de.py"


@dataclass
class Course:
    spec_path: Path
    slides_dir: Path
    deck_id: str

    @property
    def deck_path(self) -> Path:
        return self.slides_dir / self.deck_id


@pytest.fixture()
def course(tmp_path: Path) -> Course:
    spec_file = tmp_path / "course-specs" / "test.xml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        dedent(
            """\
            <course>
              <name><de>Test</de><en>Test</en></name>
              <prog-lang>python</prog-lang>
              <description><de></de><en></en></description>
              <certificate><de></de><en></en></certificate>
              <sections><section><name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics></section></sections>
            </course>
            """
        ),
        encoding="utf-8",
    )

    deck = tmp_path / "slides" / DECK_REL
    deck.parent.mkdir(parents=True, exist_ok=True)
    deck.write_text(DECK_SOURCE, encoding="utf-8")

    return Course(spec_path=spec_file, slides_dir=tmp_path / "slides", deck_id=DECK_REL)


@pytest.fixture()
def service(course: Course) -> StudioService:
    return StudioService(course.spec_path)
