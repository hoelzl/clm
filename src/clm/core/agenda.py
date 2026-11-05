"""The course agenda built from a spec's ``<block>`` layer (issue #1163).

A certification agenda lists, per week, the time blocks of every day with
their teaching units ("Unterrichtseinheiten", UE), form and contents. The
course spec already models weeks (``<section>``) and days
(``<subsection weekday="...">``, issue #261); ``<block>`` adds the time blocks
below the day. This module turns a parsed :class:`CourseSpec` into that
week/day/block structure. It is pure spec data — no filesystem access — so
``clm export agenda`` and the ``clm validate`` agenda rules work on planning
specs whose material does not exist yet, and both see the same weeks.

Selection rules (shared by the export and the validator):

* A section is an agenda **week** when it carries an ``<agenda>`` element or
  at least one ``<block>`` in a listed subsection. Other sections (an intro,
  an appendix) are not part of the agenda.
* Disabled sections and subsections are never listed. Optional ones are
  listed only with ``include_optional`` (same as ``clm export schedule``).
* A week's number is the one its section name starts with (``"Woche 05:"``,
  ``"Week 5 –"``), so skipping an unplanned or optional week keeps the
  author's numbering; a name without one gets its 1-based position among the
  agenda weeks.
* Every listed subsection of a week is a **day**, numbered 1.. within the
  week; a subsection spanning several weekdays (``weekday="mon,tue"``)
  occupies that many day numbers (label ``"1–2"``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from clm.core.course_spec import BlockSpec, CourseSpec, SectionSpec, SubsectionSpec
from clm.core.utils.text_utils import Text

# Localized ``form`` labels; the spec deals in language-neutral tokens.
FORM_LABELS: dict[str, Text] = {
    "theory": Text(de="Theorie", en="Theory"),
    "practice": Text(de="Praxis", en="Practice"),
    "theory-practice": Text(de="Theorie/Praxis", en="Theory/Practice"),
}

_WEEK_PREFIX = re.compile(r"^\s*(?:woche|week)\s*\d+\s*[:.\-–—]\s*(?=\S)", re.IGNORECASE)
_WEEK_NUMBER = re.compile(r"^\s*(?:woche|week)\s*(\d+)", re.IGNORECASE)


def week_number_from_name(section: SectionSpec) -> int | None:
    """The week number a section name starts with (``"Woche 05: …"`` -> 5), if any.

    German name first, then English. Used to number agenda weeks the way the
    author did, so a skipped week (unplanned, optional) does not shift the
    numbers of the following weeks.
    """
    for title in (section.name.de, section.name.en):
        match = _WEEK_NUMBER.match(title or "")
        if match:
            return int(match.group(1))
    return None


def strip_week_prefix(title: str) -> str:
    """Drop a leading ``"Woche 05:"`` / ``"Week 5 –"`` from a section name.

    The agenda overview numbers the weeks itself, so a name that already
    starts with its week number would repeat it. A name that is *only* a
    week label (``"Woche 1"``) is returned unchanged.
    """
    return _WEEK_PREFIX.sub("", title, count=1)


@dataclass
class AgendaDay:
    """One day (``<subsection>``) of an agenda week with its blocks in order."""

    label: str  # "3", or "1–2" for a subsection spanning two weekdays
    span: int  # number of days the subsection covers (>= 1)
    subsection: SubsectionSpec
    blocks: list[BlockSpec] = field(default_factory=list)

    @property
    def units(self) -> int:
        """Teaching units of the day; a block without ``units`` counts as 0."""
        return sum(block.units or 0 for block in self.blocks)


@dataclass
class AgendaWeek:
    """One week (``<section>``) of the agenda."""

    number: int  # from a "Woche NN"/"Week NN" name prefix, else 1-based position
    section: SectionSpec
    days: list[AgendaDay] = field(default_factory=list)

    @property
    def units(self) -> int:
        return sum(day.units for day in self.days)


def _listed(subsection: SubsectionSpec, *, include_optional: bool) -> bool:
    return subsection.enabled and (include_optional or not subsection.optional)


def spec_uses_blocks(spec: CourseSpec) -> bool:
    """Whether any subsection of *spec* carries a ``<block>``."""
    return any(sub.blocks for section in spec.sections for sub in section.subsections)


def build_agenda(spec: CourseSpec, *, include_optional: bool = False) -> list[AgendaWeek]:
    """Build the agenda weeks of *spec* (see the module docstring for the rules)."""
    weeks: list[AgendaWeek] = []
    for section in spec.sections:
        if not section.enabled or (section.optional and not include_optional):
            continue
        subsections = [
            sub for sub in section.subsections if _listed(sub, include_optional=include_optional)
        ]
        if section.agenda is None and not any(sub.blocks for sub in subsections):
            continue
        days: list[AgendaDay] = []
        next_day = 1
        for sub in subsections:
            span = max(1, len(sub.weekdays))
            last = next_day + span - 1
            label = str(next_day) if span == 1 else f"{next_day}–{last}"
            days.append(AgendaDay(label=label, span=span, subsection=sub, blocks=list(sub.blocks)))
            next_day = last + 1
        number = week_number_from_name(section) or len(weeks) + 1
        weeks.append(AgendaWeek(number=number, section=section, days=days))
    return weeks
