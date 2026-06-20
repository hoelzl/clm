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

# The EN twin of DECK_SOURCE: same slide_ids/roles, English bodies, shared code.
DECK_SOURCE_EN = dedent(
    """\
    # %% [markdown] lang="en" tags=["slide"] slide_id="intro-welcome"
    # Welcome
    #
    # Glad you're here.

    # %% [markdown] lang="en" tags=["notes"] slide_id="intro-welcome"
    # Speaker notes here.

    # %%
    print("hello")
    """
)

DECK_REL_EN = "module_100_basics/topic_010_intro/slides_intro.en.py"


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


@dataclass
class Bilingual:
    spec_path: Path
    slides_dir: Path
    de_id: str
    en_id: str

    @property
    def de_path(self) -> Path:
        return self.slides_dir / self.de_id

    @property
    def en_path(self) -> Path:
        return self.slides_dir / self.en_id


@pytest.fixture()
def bilingual(course: Course, monkeypatch) -> Bilingual:
    """A DE/EN split twin pair (reuses ``course`` for spec + the .de.py half).

    Isolates the sync watermark cache to a tmp dir via ``CLM_CACHE_DIR`` so the
    lock derivation never reads or writes a developer's real cache.
    """
    monkeypatch.setenv("CLM_CACHE_DIR", str(course.slides_dir.parent / ".clm-cache"))
    en = course.slides_dir / DECK_REL_EN
    en.write_text(DECK_SOURCE_EN, encoding="utf-8")
    return Bilingual(
        spec_path=course.spec_path,
        slides_dir=course.slides_dir,
        de_id=DECK_REL,
        en_id=DECK_REL_EN,
    )


@pytest.fixture()
def bilingual_service(bilingual: Bilingual) -> StudioService:
    return StudioService(bilingual.spec_path)
