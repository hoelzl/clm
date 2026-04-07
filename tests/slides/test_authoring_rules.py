"""Tests for course authoring rules lookup."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.authoring_rules import (
    AuthoringRulesResult,
    get_authoring_rules,
)


@pytest.fixture()
def data_dir(tmp_path):
    """Create a minimal data directory with course-specs/ and slides/."""
    specs = tmp_path / "course-specs"
    specs.mkdir()

    # Common authoring rules
    (specs / "_common.authoring.md").write_text(
        "## Universal Rules\n\n- Use proper formatting.\n- Keep slides concise.\n",
        encoding="utf-8",
    )

    # Course-specific rules
    (specs / "python-basics.authoring.md").write_text(
        "## Python Basics Rules\n\n- Target beginners.\n- No advanced patterns.\n",
        encoding="utf-8",
    )

    # A course spec XML referencing some topics
    (specs / "python-basics.xml").write_text(
        """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Python Grundlagen</de><en>Python Basics</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section>
            <name><de>Intro</de><en>Intro</en></name>
            <topics>
                <topic>intro</topic>
                <topic>variables</topic>
            </topics>
        </section>
    </sections>
</course>
""",
        encoding="utf-8",
    )

    # Another course spec referencing overlapping topics
    (specs / "ml-azav.xml").write_text(
        """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>ML AZAV</de><en>ML AZAV</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section>
            <name><de>ML</de><en>ML</en></name>
            <topics>
                <topic>intro</topic>
                <topic>neural_nets</topic>
            </topics>
        </section>
    </sections>
</course>
""",
        encoding="utf-8",
    )

    (specs / "ml-azav.authoring.md").write_text(
        "## ML AZAV Rules\n\n- Focus on practical examples.\n",
        encoding="utf-8",
    )

    # Slides directory
    slides = tmp_path / "slides"
    m1 = slides / "module_100_basics"
    t1 = m1 / "topic_010_intro"
    t1.mkdir(parents=True)
    (t1 / "slides_intro.py").write_text(
        "# %% [markdown]\n# ## Introduction\n",
        encoding="utf-8",
    )

    t2 = m1 / "topic_020_variables"
    t2.mkdir(parents=True)
    (t2 / "slides_variables.py").write_text(
        "# %% [markdown]\n# ## Variables\n",
        encoding="utf-8",
    )

    m2 = slides / "module_200_ml"
    t3 = m2 / "topic_010_neural_nets"
    t3.mkdir(parents=True)
    (t3 / "slides_neural_nets.py").write_text(
        "# %% [markdown]\n# ## Neural Networks\n",
        encoding="utf-8",
    )

    return tmp_path


class TestGetAuthoringRulesByCourseSpec:
    def test_slug_returns_merged_rules(self, data_dir):
        result = get_authoring_rules(data_dir, course_spec="python-basics")
        assert result.common_rules is not None
        assert "Universal Rules" in result.common_rules
        assert len(result.course_rules) == 1
        assert result.course_rules[0].course_spec == "python-basics"
        assert "Target beginners" in result.course_rules[0].rules

    def test_merged_text_contains_both(self, data_dir):
        result = get_authoring_rules(data_dir, course_spec="python-basics")
        assert "Universal Rules" in result.merged
        assert "Python Basics Rules" in result.merged

    def test_xml_path_resolves(self, data_dir):
        spec_path = str(data_dir / "course-specs" / "python-basics.xml")
        result = get_authoring_rules(data_dir, course_spec=spec_path)
        assert len(result.course_rules) == 1
        assert result.course_rules[0].course_spec == "python-basics"

    def test_missing_authoring_file(self, data_dir):
        # Create a spec with no authoring file
        specs = data_dir / "course-specs"
        (specs / "no-rules.xml").write_text(
            '<?xml version="1.0"?><course><name><de>X</de><en>X</en></name>'
            "<prog-lang>python</prog-lang>"
            "<description><de></de><en></en></description>"
            "<certificate><de></de><en></en></certificate>"
            "<sections></sections></course>",
            encoding="utf-8",
        )
        result = get_authoring_rules(data_dir, course_spec="no-rules")
        assert result.common_rules is not None
        assert len(result.course_rules) == 0
        assert any("No authoring rules file" in n for n in result.notes)

    def test_missing_common_rules(self, data_dir):
        (data_dir / "course-specs" / "_common.authoring.md").unlink()
        result = get_authoring_rules(data_dir, course_spec="python-basics")
        assert result.common_rules is None
        assert len(result.course_rules) == 1
        # merged still has course rules
        assert "Python Basics Rules" in result.merged

    def test_nonexistent_slug(self, data_dir):
        result = get_authoring_rules(data_dir, course_spec="nonexistent-course")
        assert len(result.course_rules) == 0
        assert any("No authoring rules file" in n for n in result.notes)


class TestGetAuthoringRulesBySlidePath:
    def test_slide_resolves_to_single_course(self, data_dir):
        slide = (
            data_dir
            / "slides"
            / "module_100_basics"
            / "topic_020_variables"
            / "slides_variables.py"
        )
        result = get_authoring_rules(data_dir, slide_path=str(slide))
        assert len(result.course_rules) == 1
        assert result.course_rules[0].course_spec == "python-basics"

    def test_slide_in_multiple_courses(self, data_dir):
        # "intro" is in both python-basics and ml-azav
        slide = data_dir / "slides" / "module_100_basics" / "topic_010_intro" / "slides_intro.py"
        result = get_authoring_rules(data_dir, slide_path=str(slide))
        course_names = {e.course_spec for e in result.course_rules}
        assert "ml-azav" in course_names
        assert "python-basics" in course_names

    def test_relative_slide_path(self, data_dir):
        rel = "slides/module_100_basics/topic_020_variables/slides_variables.py"
        result = get_authoring_rules(data_dir, slide_path=rel)
        assert len(result.course_rules) == 1
        assert result.course_rules[0].course_spec == "python-basics"

    def test_slide_not_in_any_course(self, data_dir):
        # Create a topic not referenced by any spec
        orphan = data_dir / "slides" / "module_300_orphan" / "topic_010_orphan"
        orphan.mkdir(parents=True)
        (orphan / "slides_orphan.py").write_text("# %% [markdown]\n# Orphan\n", encoding="utf-8")

        result = get_authoring_rules(data_dir, slide_path=str(orphan / "slides_orphan.py"))
        assert len(result.course_rules) == 0
        assert any("No matching course" in n for n in result.notes)


class TestEdgeCases:
    def test_no_arguments(self, data_dir):
        result = get_authoring_rules(data_dir)
        assert any("At least one" in n for n in result.notes)

    def test_empty_specs_dir(self, tmp_path):
        (tmp_path / "course-specs").mkdir()
        (tmp_path / "slides").mkdir()
        result = get_authoring_rules(tmp_path, course_spec="anything")
        assert result.common_rules is None
        assert len(result.course_rules) == 0

    def test_no_specs_dir(self, tmp_path):
        result = get_authoring_rules(tmp_path, course_spec="anything")
        assert result.common_rules is None
        assert len(result.course_rules) == 0

    def test_merged_text_fallback(self, tmp_path):
        (tmp_path / "course-specs").mkdir()
        (tmp_path / "slides").mkdir()
        result = get_authoring_rules(tmp_path, course_spec="nonexistent")
        assert "No authoring rules found" in result.merged
