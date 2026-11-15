"""``--channel`` → cohort calendar resolution (regression tests for #1205).

A bilingual cohort has several channels (per stream and language) but one
calendar. ``--channel`` used to resolve only ``<ledger dir>/<channel>.calendar.toml``,
so ``--channel materials/2026-10-de`` could not find
``release/ml-2026-10.calendar.toml``.
"""

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from clm.cli.commands.calendar import resolve_calendar_path
from clm.cli.main import cli

# Mirrors the real AZAV ML 2026-10 layout: two streams x two languages, one
# calendar named after the cohort stem the ledgers share.
SPEC_BILINGUAL = """
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
  <output-targets>
    <output-target name="shared"><path>output/shared</path></output-target>
    <output-target name="solutions"><path>output/solutions</path></output-target>
  </output-targets>
  <release-channels name="materials" source-target="shared">
    <channel name="2026-10-de" lang="de" path="cohorts/ml/de"
             ledger="release/materials-ml-2026-10-de.txt"/>
    <channel name="2026-10-en" lang="en" path="cohorts/ml/en"
             ledger="release/materials-ml-2026-10-en.txt"/>
    <channel name="2026-08-de" lang="de" path="cohorts/aug/de"
             ledger="release/materials-2026-08-de.txt"/>
  </release-channels>
  <release-channels name="solutions" source-target="solutions">
    <channel name="2026-10-de" lang="de" path="cohorts/ml/de"
             ledger="release/solutions-ml-2026-10-de.txt"/>
    <channel name="2026-10-en" lang="en" path="cohorts/ml/en"
             ledger="release/solutions-ml-2026-10-en.txt"/>
    <channel name="jan" path="cohorts/jan" ledger="release/jan.txt"/>
  </release-channels>
</course>
""".strip()


@pytest.fixture
def course(tmp_path: Path) -> Path:
    """Course root with ``course-specs/spec.xml`` and an empty ``release/``."""
    (tmp_path / "course-specs").mkdir()
    (tmp_path / "release").mkdir()
    spec = tmp_path / "course-specs" / "spec.xml"
    spec.write_text(SPEC_BILINGUAL, encoding="utf-8")
    return tmp_path


def _spec(course: Path) -> Path:
    return course / "course-specs" / "spec.xml"


def _cal(course: Path, stem: str) -> Path:
    path = course / "release" / f"{stem}.calendar.toml"
    path.write_text("start = 2026-03-02\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "ref",
    [
        "materials/2026-10-de",
        "materials/2026-10-en",
        "solutions/2026-10-de",
        "solutions/2026-10-en",
    ],
)
def test_language_channels_share_the_cohort_calendar(course, ref):
    cal = _cal(course, "ml-2026-10")
    assert resolve_calendar_path(_spec(course), ref, None) == cal


def test_cohort_without_name_prefix(course):
    """Ledger ``materials-2026-08-de.txt`` → ``2026-08.calendar.toml``."""
    cal = _cal(course, "2026-08")
    assert resolve_calendar_path(_spec(course), "2026-08-de", None) == cal


def test_per_channel_calendar_still_wins(course):
    """Backward compatibility: an existing ``<channel>.calendar.toml`` is preferred."""
    _cal(course, "ml-2026-10")
    own = _cal(course, "2026-10-de")
    assert resolve_calendar_path(_spec(course), "materials/2026-10-de", None) == own


def test_lang_stripped_channel_name_is_the_last_fallback(course):
    cal = _cal(course, "2026-10")
    assert resolve_calendar_path(_spec(course), "materials/2026-10-en", None) == cal


def test_single_language_channel_unchanged(course):
    cal = _cal(course, "jan")
    assert resolve_calendar_path(_spec(course), "jan", None) == cal


def test_cohort_stem_accepted_as_channel(course):
    """``--channel ml-2026-10`` (the calendar's own stem) used to be ``Unknown channel``."""
    cal = _cal(course, "ml-2026-10")
    assert resolve_calendar_path(_spec(course), "ml-2026-10", None) == cal


def test_unknown_ref_keeps_the_channel_error(course):
    with pytest.raises(click.ClickException, match="Unknown channel"):
        resolve_calendar_path(_spec(course), "nope", None)


def test_missing_calendar_lists_every_candidate(course):
    with pytest.raises(click.ClickException) as excinfo:
        resolve_calendar_path(_spec(course), "materials/2026-10-de", None)
    message = excinfo.value.message
    for stem in ("2026-10-de", "ml-2026-10", "2026-10"):
        assert f"{stem}.calendar.toml" in message
    assert "--calendar" in message


def test_explicit_calendar_wins(course, tmp_path):
    _cal(course, "ml-2026-10")
    explicit = tmp_path / "other.calendar.toml"
    assert resolve_calendar_path(_spec(course), "materials/2026-10-de", explicit) == explicit


def test_cli_check_finds_the_cohort_calendar(course):
    """End to end through ``clm calendar check`` (the issue's failing command shape)."""
    _cal(course, "ml-2026-10")
    result = CliRunner().invoke(
        cli, ["calendar", "check", str(_spec(course)), "--channel", "materials/2026-10-de"]
    )
    assert "calendar file not found" not in result.output
    assert "No calendar file found" not in result.output
