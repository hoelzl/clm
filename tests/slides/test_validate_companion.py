"""``clm validate`` on a separated voiceover companion file (#946).

A companion (``voiceover_<stem>.<lang>.<ext>``, beside its deck or in the
topic's ``voiceover/`` subdirectory) holds only narration cells, each bound
to its slide by ``for_slide``. The slide anchors live in the *deck*, so the
deck-only slide_id anchor rule reported one spurious "no preceding
slide/subslide anchor" error per cell when the companion was validated
standalone — on every companion in the corpus, while validating the topic
directory (which never lists companions) passed.

Standalone validation of a companion now validates it *as a companion*:
format/tag checks on the file itself, and the for_slide targets resolved
against the owning deck (the same build-equivalent check the deck side
already ran), with a single ``info`` when no owning deck can be found.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from clm.slides.validator import validate_file, validate_quick

DECK = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Einführung

# %% [markdown] lang="de" tags=["slide"] slide_id="setup"
# ## Aufbau
"""

# The corpus shape: narration cells carry for_slide (the join key), a
# vo_anchor, and their OWN slide_id (the sync-v3 own-id convention) that
# differs from for_slide. (The corpus also uses the ``for_slide="title"``
# greeting convention, which needs a titled deck; the deck-side check that
# resolves it is covered by its own tests.)
COMPANION = """\
# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro" vo_anchor="id:intro#0" slide_id="welcome"
#
# - Herzlich willkommen!

# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro" vo_anchor="id:intro#0" slide_id="intro-said"
#
# - Zur Einführung.

# %% [markdown] lang="de" tags=["voiceover"] for_slide="setup" vo_anchor="id:setup#0" slide_id="setup-said"
#
# - Zum Aufbau.
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content), encoding="utf-8")
    return path


def _errors(result):
    return [f for f in result.findings if f.severity == "error"]


@pytest.fixture
def subdir_layout(tmp_path: Path):
    """``<topic>/slides_intro.de.py`` + ``<topic>/voiceover/voiceover_intro.de.py``."""
    topic = tmp_path / "topic_100_intro"
    deck = _write(topic / "slides_intro.de.py", DECK)
    companion = _write(topic / "voiceover" / "voiceover_intro.de.py", COMPANION)
    return deck, companion


@pytest.fixture
def sibling_layout(tmp_path: Path):
    topic = tmp_path / "topic_100_intro"
    deck = _write(topic / "slides_intro.de.py", DECK)
    companion = _write(topic / "voiceover_intro.de.py", COMPANION)
    return deck, companion


def test_companion_in_voiceover_subdir_validates_clean(subdir_layout):
    """Regression test for #946: no 'no preceding slide/subslide anchor' storm."""
    _deck, companion = subdir_layout

    result = validate_file(companion)

    assert _errors(result) == [], [f.message for f in result.findings]
    assert not any("preceding slide/subslide anchor" in f.message for f in result.findings)
    assert result.files_checked == 1


def test_companion_beside_its_deck_validates_clean(sibling_layout):
    _deck, companion = sibling_layout
    assert _errors(validate_file(companion)) == []


def test_quick_mode_on_a_companion_is_clean_too(subdir_layout):
    """``--quick`` (the PostToolUse hook path) ran the same anchor rule."""
    _deck, companion = subdir_layout

    result = validate_quick(companion)

    assert _errors(result) == [], [f.message for f in result.findings]


def test_unresolvable_for_slide_is_reported_against_the_deck(subdir_layout):
    """The companion is validated against its owning deck: a for_slide that
    matches no slide_id there is the build's "dropped narration" error,
    caught statically."""
    deck, companion = subdir_layout
    companion.write_text(
        companion.read_text(encoding="utf-8").replace('for_slide="setup"', 'for_slide="gone"'),
        encoding="utf-8",
    )

    errors = _errors(validate_file(companion))

    assert len(errors) == 1
    assert "'gone'" in errors[0].message
    assert deck.name in errors[0].message
    assert Path(errors[0].file) == companion


def test_companion_without_an_owning_deck_gets_one_info_not_errors(tmp_path: Path):
    """No deck next to it or one directory up: say so once, do not invent
    anchors, and do not fail."""
    companion = _write(tmp_path / "voiceover" / "voiceover_orphan.de.py", COMPANION)

    result = validate_file(companion)

    assert _errors(result) == []
    infos = [f for f in result.findings if f.severity == "info"]
    assert len(infos) == 1
    assert "owning deck" in infos[0].message.lower() or "deck" in infos[0].message.lower()


def test_format_problems_in_a_companion_are_still_reported(subdir_layout):
    """Companion mode narrows the *pairing* rules, not the format/tag rules:
    an unknown tag on a narration cell is still an error."""
    _deck, companion = subdir_layout
    companion.write_text(
        companion.read_text(encoding="utf-8").replace('tags=["voiceover"]', 'tags=["voiceovr"]', 1),
        encoding="utf-8",
    )

    errors = _errors(validate_file(companion))

    assert errors, "the misspelled tag must still be flagged"
    assert not any("preceding slide/subslide anchor" in f.message for f in errors)


def test_deck_validation_is_unchanged(subdir_layout):
    """Validating the deck still runs the deck rules and the deck-side
    companion check; nothing about companion mode leaks into it."""
    deck, _companion = subdir_layout
    assert _errors(validate_file(deck)) == []


class TestCompanionPredicates:
    def test_is_voiceover_companion(self, tmp_path: Path):
        from clm.core.voiceover_companions import is_voiceover_companion

        assert is_voiceover_companion(Path("voiceover_intro.de.py"))
        assert is_voiceover_companion(Path("voiceover/voiceover_intro.cpp"))
        assert not is_voiceover_companion(Path("slides_intro.de.py"))
        assert not is_voiceover_companion(Path("voiceover_notes.txt"))

    def test_deck_for_companion_both_layouts(self, subdir_layout, tmp_path: Path):
        from clm.core.voiceover_companions import deck_for_companion

        deck, companion = subdir_layout
        assert deck_for_companion(companion) == deck

        topic = tmp_path / "topic_200_other"
        deck2 = _write(topic / "topic_other.py", DECK)
        companion2 = _write(topic / "voiceover_other.py", COMPANION)
        assert deck_for_companion(companion2) == deck2

    def test_deck_for_companion_none_when_no_deck_claims_it(self, tmp_path: Path):
        from clm.core.voiceover_companions import deck_for_companion

        companion = _write(tmp_path / "voiceover" / "voiceover_orphan.de.py", COMPANION)
        assert deck_for_companion(companion) is None
        assert deck_for_companion(Path("slides_intro.de.py")) is None
