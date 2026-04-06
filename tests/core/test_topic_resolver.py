"""Tests for clm.core.topic_resolver — standalone topic resolution."""

import pytest

from clm.core.topic_resolver import (
    GlobMatchEntry,
    ResolutionResult,
    TopicMatch,
    build_topic_map,
    find_slide_files,
    get_course_topic_ids,
    resolve_topic,
)


@pytest.fixture()
def slides_dir(tmp_path):
    """Create a minimal slides/ directory tree for testing.

    Structure:
        slides/
            module_100_basics/
                topic_010_intro/
                    slides_intro.py
                topic_020_variables/
                    slides_variables.py
                    slides_variables_extra.py
            module_200_oop/
                topic_010_classes/
                    slides_classes.py
                topic_020_methods/
                    slides_methods.py
            module_300_advanced/
                topic_010_decorators/        # ambiguous with module_200
                    slides_decorators.py
    """
    root = tmp_path / "slides"

    # Module 100: basics
    m100 = root / "module_100_basics"
    t_intro = m100 / "topic_010_intro"
    t_intro.mkdir(parents=True)
    (t_intro / "slides_intro.py").write_text("# intro", encoding="utf-8")

    t_vars = m100 / "topic_020_variables"
    t_vars.mkdir(parents=True)
    (t_vars / "slides_variables.py").write_text("# vars", encoding="utf-8")
    (t_vars / "slides_variables_extra.py").write_text("# extra", encoding="utf-8")
    # Non-slide file should be excluded
    (t_vars / "data.json").write_text("{}", encoding="utf-8")

    # Module 200: oop
    m200 = root / "module_200_oop"
    t_classes = m200 / "topic_010_classes"
    t_classes.mkdir(parents=True)
    (t_classes / "slides_classes.py").write_text("# classes", encoding="utf-8")

    t_methods = m200 / "topic_020_methods"
    t_methods.mkdir(parents=True)
    (t_methods / "slides_methods.py").write_text("# methods", encoding="utf-8")

    # Module 300: advanced — creates ambiguous "decorators" topic
    m300 = root / "module_300_advanced"
    t_dec = m300 / "topic_010_decorators"
    t_dec.mkdir(parents=True)
    (t_dec / "slides_decorators.py").write_text("# decorators", encoding="utf-8")

    return root


@pytest.fixture()
def slides_dir_with_file_topic(tmp_path):
    """Slides directory with a single-file topic (legacy format)."""
    root = tmp_path / "slides"
    m100 = root / "module_100_basics"
    m100.mkdir(parents=True)
    (m100 / "topic_050_legacy_topic.py").write_text("# legacy", encoding="utf-8")
    return root


class TestBuildTopicMap:
    def test_finds_all_topics(self, slides_dir):
        topic_map = build_topic_map(slides_dir)
        assert "intro" in topic_map
        assert "variables" in topic_map
        assert "classes" in topic_map
        assert "methods" in topic_map
        assert "decorators" in topic_map

    def test_detects_ambiguity(self, slides_dir):
        """Same topic ID in multiple modules produces multiple matches."""
        # Add a second decorators topic in module_200
        m200_dec = slides_dir / "module_200_oop" / "topic_030_decorators"
        m200_dec.mkdir(parents=True)
        (m200_dec / "slides_decorators.py").write_text("# oop decorators", encoding="utf-8")

        topic_map = build_topic_map(slides_dir)
        assert len(topic_map["decorators"]) == 2

    def test_records_module_name(self, slides_dir):
        topic_map = build_topic_map(slides_dir)
        intro_matches = topic_map["intro"]
        assert len(intro_matches) == 1
        assert intro_matches[0].module == "module_100_basics"

    def test_finds_slide_files(self, slides_dir):
        topic_map = build_topic_map(slides_dir)
        vars_matches = topic_map["variables"]
        assert len(vars_matches) == 1
        slide_names = [f.name for f in vars_matches[0].slide_files]
        assert "slides_variables.py" in slide_names
        assert "slides_variables_extra.py" in slide_names
        # Non-slide file should NOT be included
        assert "data.json" not in slide_names

    def test_path_type_directory(self, slides_dir):
        topic_map = build_topic_map(slides_dir)
        assert topic_map["intro"][0].path_type == "directory"

    def test_path_type_file(self, slides_dir_with_file_topic):
        topic_map = build_topic_map(slides_dir_with_file_topic)
        assert "legacy_topic" in topic_map
        assert topic_map["legacy_topic"][0].path_type == "file"

    def test_empty_dir(self, tmp_path):
        empty = tmp_path / "slides"
        empty.mkdir()
        assert build_topic_map(empty) == {}

    def test_nonexistent_dir(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist"
        assert build_topic_map(nonexistent) == {}

    def test_ignores_hidden_dirs(self, slides_dir):
        hidden = slides_dir / ".git"
        hidden.mkdir()
        (hidden / "topic_010_secret").mkdir()
        topic_map = build_topic_map(slides_dir)
        assert "secret" not in topic_map

    def test_ignores_pycache(self, slides_dir):
        pycache = slides_dir / "__pycache__"
        pycache.mkdir()
        topic_map = build_topic_map(slides_dir)
        # Should not crash or include junk
        for tid in topic_map:
            assert "__pycache__" not in tid


class TestFindSlideFiles:
    def test_directory_topic(self, slides_dir):
        topic_path = slides_dir / "module_100_basics" / "topic_020_variables"
        files = find_slide_files(topic_path)
        names = [f.name for f in files]
        assert "slides_variables.py" in names
        assert "slides_variables_extra.py" in names
        assert "data.json" not in names

    def test_file_topic(self, slides_dir_with_file_topic):
        topic_path = slides_dir_with_file_topic / "module_100_basics" / "topic_050_legacy_topic.py"
        files = find_slide_files(topic_path)
        assert len(files) == 1
        assert files[0].name == "topic_050_legacy_topic.py"

    def test_empty_directory(self, tmp_path):
        empty = tmp_path / "empty_topic"
        empty.mkdir()
        assert find_slide_files(empty) == []

    def test_nonexistent_path(self, tmp_path):
        assert find_slide_files(tmp_path / "nope") == []


class TestResolveTopic:
    def test_exact_match(self, slides_dir):
        result = resolve_topic("intro", slides_dir)
        assert not result.ambiguous
        assert not result.glob
        assert result.path is not None
        assert result.path_type == "directory"
        assert any(f.name == "slides_intro.py" for f in result.slide_files)

    def test_not_found(self, slides_dir):
        result = resolve_topic("nonexistent", slides_dir)
        assert result.path is None
        assert not result.ambiguous
        assert result.slide_files == []

    def test_ambiguous(self, slides_dir):
        # Add a second decorators topic
        m200_dec = slides_dir / "module_200_oop" / "topic_030_decorators"
        m200_dec.mkdir(parents=True)
        (m200_dec / "slides_decorators.py").write_text("# oop dec", encoding="utf-8")

        result = resolve_topic("decorators", slides_dir)
        assert result.ambiguous
        assert result.path is None
        assert len(result.alternatives) == 2

    def test_glob_match(self, slides_dir):
        result = resolve_topic("intro*", slides_dir)
        assert result.glob
        assert not result.ambiguous
        assert len(result.matches) >= 1
        assert any(m.topic_id == "intro" for m in result.matches)

    def test_glob_multiple_matches(self, slides_dir):
        # "variables*" should match just "variables" (if no other var* topics)
        result = resolve_topic("*", slides_dir)
        assert result.glob
        # Should find all topics
        topic_ids = {m.topic_id for m in result.matches}
        assert "intro" in topic_ids
        assert "variables" in topic_ids
        assert "classes" in topic_ids

    def test_glob_no_matches(self, slides_dir):
        result = resolve_topic("zzz*", slides_dir)
        assert result.glob
        assert result.matches == []

    def test_course_scoping(self, slides_dir):
        # Only search for topics in a specific course
        course_ids = {"intro", "classes"}
        result = resolve_topic("intro", slides_dir, course_topic_ids=course_ids)
        assert result.path is not None

        result = resolve_topic("variables", slides_dir, course_topic_ids=course_ids)
        assert result.path is None  # Not in scope

    def test_course_scoping_glob(self, slides_dir):
        course_ids = {"intro", "classes"}
        result = resolve_topic("*", slides_dir, course_topic_ids=course_ids)
        topic_ids = {m.topic_id for m in result.matches}
        assert topic_ids == {"intro", "classes"}

    def test_file_topic_resolves(self, slides_dir_with_file_topic):
        result = resolve_topic("legacy_topic", slides_dir_with_file_topic)
        assert result.path is not None
        assert result.path_type == "file"


class TestGetCourseTopicIds:
    def test_extracts_ids(self):
        """Test with a mock-like spec object."""

        class FakeTopicSpec:
            def __init__(self, id):
                self.id = id

        class FakeSection:
            def __init__(self, topics):
                self.topics = topics

        class FakeSpec:
            def __init__(self, sections):
                self.sections = sections

        spec = FakeSpec(
            [
                FakeSection([FakeTopicSpec("intro"), FakeTopicSpec("variables")]),
                FakeSection([FakeTopicSpec("classes")]),
            ]
        )

        ids = get_course_topic_ids(spec)
        assert ids == {"intro", "variables", "classes"}
