"""Tests for cross-references between notebooks (Issue #17).

Covers three layers:

* the decision-agnostic extractor (``extract_cross_references`` /
  ``has_cross_references``);
* the ``CrossReferenceResolver`` (topic-id -> per-variant relative href,
  multi-notebook disambiguation/ambiguity, missing targets, per-format
  rules); and
* the mechanical worker-side rewrite (``rewrite_cross_references``).

The resolver is exercised against a hand-built ``Course`` (no filesystem,
no kernel) so the path/rename logic is unit-testable in isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.core.course_files.notebook_file import NotebookFile
from clm.core.cross_references import (
    SCHEME,
    CrossReferenceResolver,
    extract_cross_references,
    has_cross_references,
    rewrite_cross_references,
    split_reference,
    validate_cross_references,
)
from clm.core.section import Section
from clm.core.topic import Topic
from clm.core.utils.text_utils import Text


# --------------------------------------------------------------------------- #
# Extractor
# --------------------------------------------------------------------------- #
def test_scheme_constant() -> None:
    assert SCHEME == "clm:"


def test_extracts_single_reference() -> None:
    text = "See the [Functions workshop](clm:functions_workshop) for exercises."
    assert extract_cross_references(text) == ["functions_workshop"]


def test_extracts_multiple_references_in_order() -> None:
    text = "First [intro](clm:introduction), then [the workshop](clm:functions_workshop)."
    assert extract_cross_references(text) == [
        "introduction",
        "functions_workshop",
    ]


def test_preserves_duplicate_references() -> None:
    text = "[a](clm:intro) and again [b](clm:intro)"
    assert extract_cross_references(text) == ["intro", "intro"]


def test_ignores_ordinary_links() -> None:
    text = "A [normal link](https://example.com) and a [relative](../foo/bar.html)."
    assert extract_cross_references(text) == []


def test_ignores_image_links() -> None:
    text = "![diagram](img/diagram.png)"
    assert extract_cross_references(text) == []


def test_captures_disambiguator_and_anchor_verbatim() -> None:
    text = "[deck](clm:topic_id/slides_foo) and [section](clm:topic_id#heading)"
    assert extract_cross_references(text) == [
        "topic_id/slides_foo",
        "topic_id#heading",
    ]


def test_tolerates_internal_whitespace_in_href() -> None:
    text = "[x](clm: introduction )"
    assert extract_cross_references(text) == ["introduction"]


def test_has_cross_references() -> None:
    assert has_cross_references("[x](clm:intro)") is True
    assert has_cross_references("[x](https://example.com)") is False
    assert has_cross_references("plain text, no links") is False


# --------------------------------------------------------------------------- #
# split_reference
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("intro", ("intro", None)),
        ("intro/slides_basics", ("intro", "slides_basics")),
        ("intro#heading", ("intro", None)),
        ("intro/deck#heading", ("intro", "deck")),
    ],
)
def test_split_reference(reference: str, expected: tuple[str, str | None]) -> None:
    assert split_reference(reference) == expected


# --------------------------------------------------------------------------- #
# rewrite_cross_references
# --------------------------------------------------------------------------- #
def test_rewrite_replaces_href() -> None:
    # The resolver always hands ``rewrite_cross_references`` an already
    # percent-encoded href (issue #17), so the substitution is verbatim.
    text = "See [the workshop](clm:functions_workshop)."
    out = rewrite_cross_references(text, {"functions_workshop": "../Workshops/03%20Functions.html"})
    assert out == "See [the workshop](../Workshops/03%20Functions.html)."


def test_rewrite_drops_link_on_empty_href() -> None:
    text = "See [the workshop](clm:functions_workshop)."
    out = rewrite_cross_references(text, {"functions_workshop": ""})
    assert out == "See the workshop."


def test_rewrite_leaves_unmapped_reference_verbatim() -> None:
    text = "See [the workshop](clm:functions_workshop)."
    out = rewrite_cross_references(text, {"other": "x.html"})
    assert out == text


def test_rewrite_noop_on_empty_map() -> None:
    text = "See [the workshop](clm:functions_workshop)."
    assert rewrite_cross_references(text, {}) == text


# --------------------------------------------------------------------------- #
# Resolver — hand-built course
# --------------------------------------------------------------------------- #
class _FakeSpec:
    """Minimal stand-in for the bits of CourseSpec the resolver touches."""

    prog_lang = "python"


class _FakeCourse:
    """A course-shaped object exposing only ``sections``/``spec``.

    The resolver reads ``course.sections`` and each notebook's
    ``section.name``, ``topic.id``, ``number_in_section``, ``title``,
    ``path`` and ``prog_lang`` — all settable without a filesystem.
    """

    def __init__(self) -> None:
        self.sections: list[Section] = []
        self.spec = _FakeSpec()
        self.image_mode = "duplicated"
        self.fail_on_missing_xref = False

    @property
    def cross_reference_resolver(self) -> CrossReferenceResolver:
        return CrossReferenceResolver(self)  # type: ignore[arg-type]


def _make_notebook(
    course: _FakeCourse,
    topic: Topic,
    *,
    stem: str,
    title_en: str,
    title_de: str,
    number: int,
) -> NotebookFile:
    nb = NotebookFile(
        course=course,  # type: ignore[arg-type]
        path=topic.path / f"{stem}.py",
        topic=topic,
        title=Text(en=title_en, de=title_de),
        number_in_section=number,
    )
    topic._file_map[nb.path] = nb
    return nb


def _build_course() -> tuple[_FakeCourse, NotebookFile, NotebookFile]:
    """Two sections, two single-notebook topics.

    Section "Basics" contains topic ``intro`` (one deck); section
    "Workshops" contains topic ``functions_workshop`` (one deck).
    """
    course = _FakeCourse()

    sec_basics = Section(name=Text(en="Basics", de="Grundlagen"), course=course)  # type: ignore[arg-type]
    sec_workshops = Section(name=Text(en="Workshops", de="Workshops"), course=course)  # type: ignore[arg-type]
    course.sections = [sec_basics, sec_workshops]

    topic_intro = Topic.from_spec(
        __import__("clm.core.course_spec", fromlist=["TopicSpec"]).TopicSpec(id="intro"),
        section=sec_basics,
        path=Path("slides/module_000/topic_100_intro"),
    )
    sec_basics.topics.append(topic_intro)

    topic_workshop = Topic.from_spec(
        __import__("clm.core.course_spec", fromlist=["TopicSpec"]).TopicSpec(
            id="functions_workshop"
        ),
        section=sec_workshops,
        path=Path("slides/module_010/topic_200_functions_workshop"),
    )
    sec_workshops.topics.append(topic_workshop)

    nb_intro = _make_notebook(
        course,
        topic_intro,
        stem="slides_intro",
        title_en="Introduction",
        title_de="Einführung",
        number=1,
    )
    nb_workshop = _make_notebook(
        course,
        topic_workshop,
        stem="slides_functions",
        title_en="Functions",
        title_de="Funktionen",
        number=3,
    )
    return course, nb_intro, nb_workshop


def test_resolver_maps_topic_id_to_html_href() -> None:
    course, nb_intro, _nb_workshop = _build_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]

    resolved = resolver.resolve(
        "functions_workshop",
        from_notebook=nb_intro,
        language="en",
        kind="completed",
        format="html",
    )
    assert resolved is not None
    # From section "Basics" up one and into "Workshops" with the renamed file.
    # The space in the filename is percent-encoded so the link renders as a
    # working anchor (issue #17).
    assert resolved.href == "../Workshops/03%20Functions.html"
    assert resolved.ambiguous is False


def test_resolver_href_is_per_variant() -> None:
    """German titles and notebook format yield a different href."""
    course, nb_intro, _nb = _build_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]

    de_nb = resolver.resolve(
        "functions_workshop",
        from_notebook=nb_intro,
        language="de",
        kind="completed",
        format="notebook",
    )
    assert de_nb is not None
    assert de_nb.href == "../Workshops/03%20Funktionen.ipynb"

    en_code = resolver.resolve(
        "functions_workshop",
        from_notebook=nb_intro,
        language="en",
        kind="completed",
        format="code",
    )
    assert en_code is not None
    # code format extension follows the prog lang (.py for python).
    assert en_code.href == "../Workshops/03%20Functions.py"


def test_resolver_returns_none_for_missing_topic() -> None:
    course, nb_intro, _nb = _build_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]
    assert (
        resolver.resolve(
            "does_not_exist",
            from_notebook=nb_intro,
            language="en",
            kind="completed",
            format="html",
        )
        is None
    )


def _build_multi_notebook_course() -> tuple[_FakeCourse, NotebookFile]:
    """One topic ``advanced`` containing two slide decks."""
    course = _FakeCourse()
    sec = Section(name=Text(en="Basics", de="Grundlagen"), course=course)  # type: ignore[arg-type]
    sec_adv = Section(name=Text(en="Advanced", de="Fortgeschritten"), course=course)  # type: ignore[arg-type]
    course.sections = [sec, sec_adv]

    from clm.core.course_spec import TopicSpec

    topic_from = Topic.from_spec(
        TopicSpec(id="intro"),
        section=sec,
        path=Path("slides/module_000/topic_100_intro"),
    )
    sec.topics.append(topic_from)
    nb_from = _make_notebook(
        course, topic_from, stem="slides_intro", title_en="Intro", title_de="Intro", number=1
    )

    topic_adv = Topic.from_spec(
        TopicSpec(id="advanced"),
        section=sec_adv,
        path=Path("slides/module_010/topic_200_advanced"),
    )
    sec_adv.topics.append(topic_adv)
    _make_notebook(
        course, topic_adv, stem="slides_part_a", title_en="Part A", title_de="Teil A", number=1
    )
    _make_notebook(
        course, topic_adv, stem="slides_part_b", title_en="Part B", title_de="Teil B", number=2
    )
    return course, nb_from


def test_resolver_ambiguous_multi_notebook_topic() -> None:
    course, nb_from = _build_multi_notebook_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]

    resolved = resolver.resolve(
        "advanced",
        from_notebook=nb_from,
        language="en",
        kind="completed",
        format="html",
    )
    assert resolved is not None
    assert resolved.ambiguous is True
    # Deterministic fallback: lowest number_in_section -> Part A (slot 1).
    assert resolved.href == "../Advanced/01%20Part%20A.html"


def test_resolver_disambiguator_selects_specific_deck() -> None:
    course, nb_from = _build_multi_notebook_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]

    resolved = resolver.resolve(
        "advanced/slides_part_b",
        from_notebook=nb_from,
        language="en",
        kind="completed",
        format="html",
    )
    assert resolved is not None
    assert resolved.ambiguous is False
    assert resolved.href == "../Advanced/02%20Part%20B.html"


# --------------------------------------------------------------------------- #
# build_href_map — per-format and missing-target policy
# --------------------------------------------------------------------------- #
def test_href_map_code_format_drops_links() -> None:
    course, nb_intro, _nb = _build_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]
    hrefs, issues = resolver.build_href_map(
        ["functions_workshop"],
        from_notebook=nb_intro,
        language="en",
        kind="completed",
        format="code",
        fail_on_missing=False,
    )
    assert hrefs == {"functions_workshop": ""}
    assert issues == []


def test_href_map_jupyterlite_is_deferred() -> None:
    course, nb_intro, _nb = _build_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]
    hrefs, issues = resolver.build_href_map(
        ["functions_workshop"],
        from_notebook=nb_intro,
        language="en",
        kind="completed",
        format="jupyterlite",
        fail_on_missing=False,
    )
    # Deferred: no href produced (left verbatim), one info issue.
    assert hrefs == {}
    assert len(issues) == 1
    assert issues[0].severity == "info"


def test_href_map_missing_warns_and_drops_when_not_failing() -> None:
    course, nb_intro, _nb = _build_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]
    hrefs, issues = resolver.build_href_map(
        ["nope"],
        from_notebook=nb_intro,
        language="en",
        kind="completed",
        format="html",
        fail_on_missing=False,
    )
    assert hrefs == {"nope": ""}
    assert len(issues) == 1
    assert issues[0].severity == "warning"


def test_href_map_missing_errors_and_leaves_verbatim_when_failing() -> None:
    course, nb_intro, _nb = _build_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]
    hrefs, issues = resolver.build_href_map(
        ["nope"],
        from_notebook=nb_intro,
        language="en",
        kind="completed",
        format="html",
        fail_on_missing=True,
    )
    # Left verbatim (omitted) so the failing build does not silently rewrite.
    assert hrefs == {}
    assert len(issues) == 1
    assert issues[0].severity == "error"


def test_href_map_ambiguous_emits_warning_but_resolves() -> None:
    course, nb_from = _build_multi_notebook_course()
    resolver = CrossReferenceResolver(course)  # type: ignore[arg-type]
    hrefs, issues = resolver.build_href_map(
        ["advanced"],
        from_notebook=nb_from,
        language="en",
        kind="completed",
        format="html",
        fail_on_missing=True,
    )
    assert hrefs == {"advanced": "../Advanced/01%20Part%20A.html"}
    assert len(issues) == 1
    assert issues[0].severity == "warning"


# --------------------------------------------------------------------------- #
# validate_cross_references — host-side build validation (mocked file read)
# --------------------------------------------------------------------------- #
def test_validate_reports_missing_target(monkeypatch) -> None:
    course, nb_intro, _nb = _build_course()

    def fake_read(self, encoding="utf-8"):
        if self.name == nb_intro.path.name:
            return "See [the deck](clm:does_not_exist)."
        return "no refs here"

    monkeypatch.setattr(Path, "read_text", fake_read, raising=False)

    findings = validate_cross_references(course, fail_on_missing=True)  # type: ignore[arg-type]
    missing = [f for f in findings if f.type == "cross_reference_target_missing"]
    assert len(missing) == 1
    assert missing[0].severity == "error"

    findings_warn = validate_cross_references(course, fail_on_missing=False)  # type: ignore[arg-type]
    missing_warn = [f for f in findings_warn if f.type == "cross_reference_target_missing"]
    assert len(missing_warn) == 1
    assert missing_warn[0].severity == "warning"


def test_validate_reports_ambiguous_target(monkeypatch) -> None:
    course, nb_from = _build_multi_notebook_course()

    def fake_read(self, encoding="utf-8"):
        if self.name == nb_from.path.name:
            return "See [advanced material](clm:advanced)."
        return "no refs"

    monkeypatch.setattr(Path, "read_text", fake_read, raising=False)

    findings = validate_cross_references(course, fail_on_missing=True)  # type: ignore[arg-type]
    ambiguous = [f for f in findings if f.type == "cross_reference_ambiguous"]
    assert len(ambiguous) == 1
    assert ambiguous[0].severity == "warning"


def test_validate_passes_when_all_targets_present(monkeypatch) -> None:
    course, nb_intro, _nb = _build_course()

    def fake_read(self, encoding="utf-8"):
        if self.name == nb_intro.path.name:
            return "See [the workshop](clm:functions_workshop)."
        return "no refs"

    monkeypatch.setattr(Path, "read_text", fake_read, raising=False)

    findings = validate_cross_references(course, fail_on_missing=True)  # type: ignore[arg-type]
    assert findings == []


# --------------------------------------------------------------------------- #
# Payload wiring — ProcessNotebookOperation.compute_cross_references
# --------------------------------------------------------------------------- #
def test_payload_compute_cross_references_resolves_for_variant() -> None:
    from clm.core.operations.process_notebook import ProcessNotebookOperation

    course, nb_intro, _nb = _build_course()
    op = ProcessNotebookOperation(
        input_file=nb_intro,
        output_file=Path("out/Basics/01 Introduction.html"),
        language="en",
        format="html",
        kind="completed",
        prog_lang="python",
    )
    data = "See [the workshop](clm:functions_workshop)."
    hrefs = op.compute_cross_references(data)
    assert hrefs == {"functions_workshop": "../Workshops/03%20Functions.html"}


def test_payload_compute_cross_references_empty_without_refs() -> None:
    from clm.core.operations.process_notebook import ProcessNotebookOperation

    course, nb_intro, _nb = _build_course()
    op = ProcessNotebookOperation(
        input_file=nb_intro,
        output_file=Path("out/Basics/01 Introduction.html"),
        language="en",
        format="html",
        kind="completed",
        prog_lang="python",
    )
    assert op.compute_cross_references("no references here") == {}
