from pathlib import Path
from tempfile import TemporaryDirectory

from clm.core.course import Course

# DATA_DIR is defined in tests/conftest.py and available as a fixture
# For direct use, we compute it here
DATA_DIR = Path(__file__).parent.parent / "test-data"


def test_topic_matches_path(topic_1):
    # Existing slides in topic dir match
    assert topic_1.matches_path(topic_1.path / "slides_some_topic_from_test_1.py", False)
    # New slides in topic dir match
    assert topic_1.matches_path(topic_1.path / "slides_new_topic.py", False)
    # Images in the img/ subdirectory match
    assert topic_1.matches_path(topic_1.path / "img/my_image.png", False)
    # PlantUML files in the pu/ subdirectory match
    assert topic_1.matches_path(topic_1.path / "pu/my_diag.pu", False)
    # DrawIO files in the drawio/ subdirectory match
    assert topic_1.matches_path(topic_1.path / "drawio/my_drawing.drawio", False)
    # Deeply nested data files match
    assert topic_1.matches_path(topic_1.path / "test-data/more_data/csv/test.csv", False)

    # Files in other topics do not match
    other_topic = Path(DATA_DIR / "module_010_test_2" / "topic_200_other")
    assert not topic_1.matches_path(other_topic / "slides_a_topic_from_test_2.py", False)

    # Files in the parent module do not match
    assert not topic_1.matches_path(topic_1.path.parent / "slides_in_parent.py", False)


def test_topic_files(course_2_spec):
    with TemporaryDirectory() as out_dir:
        course = Course.from_spec(course_2_spec, DATA_DIR, Path(out_dir))

        unit = course.topics[0]

        assert len(unit.files) == 3


# ---------------------------------------------------------------------------
# Virtual-include splicing (Feature 1: shared-source includes)
#
# These tests build a minimal Topic in isolation rather than going
# through CourseSpec → Course, so they cover only the splicing in
# topic.py. Course-level wiring (resolving IncludeSpec → ResolvedInclude
# against the course root) is exercised in PR1.3 tests.
# ---------------------------------------------------------------------------


def _make_isolated_topic(tmp_path, *, includes=None):
    """Build a DirectoryTopic with a minimal Course around it."""
    import io as _io

    from clm.core.course import Course
    from clm.core.course_spec import CourseSpec, TopicSpec
    from clm.core.section import Section
    from clm.core.topic import DirectoryTopic
    from clm.core.utils.text_utils import Text

    course_root = tmp_path / "course-root"
    course_root.mkdir()
    (course_root / "slides").mkdir()  # required by Course.from_spec
    spec = CourseSpec.from_file(
        _io.StringIO(
            """
            <course>
                <name><de>T</de><en>T</en></name>
                <prog-lang>python</prog-lang>
                <description><de></de><en></en></description>
                <certificate><de></de><en></en></certificate>
                <sections><section><name><de>S</de><en>S</en></name>
                    <topics><topic>tx</topic></topics>
                </section></sections>
            </course>
            """
        )
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    course = Course.from_spec(spec, course_root, out_dir)
    section = Section(name=Text(de="S", en="S"), course=course)
    topic_path = course_root / "topic-x"
    topic_path.mkdir()
    topic = DirectoryTopic(
        id="tx",
        section=section,
        path=topic_path,
        includes=list(includes or []),
    )
    return course, topic, course_root


def test_include_splices_directory_files_into_topic(tmp_path):
    """A directory `<include>` makes every file under it visible to the topic."""
    from clm.core.topic import ResolvedInclude

    course, topic, course_root = _make_isolated_topic(tmp_path)

    # Canonical source: examples/SimpleChatbot/src/simple_chatbot/{__init__,main}.py
    src = course_root / "examples" / "SimpleChatbot" / "src" / "simple_chatbot"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("VERSION = '0.1.0'\n")
    (src / "main.py").write_text("def run():\n    pass\n")
    (src / "sub").mkdir()
    (src / "sub" / "helper.py").write_text("HELPER = True\n")

    topic.includes.append(ResolvedInclude(source_root=src, as_path="simple_chatbot"))
    topic.build_file_map()

    expected_paths = {
        topic.path / "simple_chatbot" / "__init__.py",
        topic.path / "simple_chatbot" / "main.py",
        topic.path / "simple_chatbot" / "sub" / "helper.py",
    }
    assert {f.path for f in topic.files} == expected_paths
    # Every spliced file's content reads back from the canonical source.
    by_logical = {f.path: f for f in topic.files}
    assert by_logical[topic.path / "simple_chatbot" / "__init__.py"].source_path == (
        src / "__init__.py"
    )
    assert by_logical[topic.path / "simple_chatbot" / "main.py"].source_path == (src / "main.py")
    assert (
        by_logical[topic.path / "simple_chatbot" / "main.py"].source_path.read_text()
        == "def run():\n    pass\n"
    )


def test_include_splices_single_file(tmp_path):
    from clm.core.topic import ResolvedInclude

    course, topic, course_root = _make_isolated_topic(tmp_path)

    src_file = course_root / "examples" / "Foo" / ".env.example"
    src_file.parent.mkdir(parents=True)
    src_file.write_text("API_KEY=changeme\n")

    topic.includes.append(ResolvedInclude(source_root=src_file, as_path=".env.example"))
    topic.build_file_map()

    files = list(topic.files)
    assert len(files) == 1
    assert files[0].path == topic.path / ".env.example"
    assert files[0].source_path == src_file


def test_real_local_file_shadows_virtual_include(tmp_path):
    """A physical file at the include's `as_path` wins; warning is recorded."""
    from clm.core.topic import ResolvedInclude

    course, topic, course_root = _make_isolated_topic(tmp_path)

    src = course_root / "examples" / "SimpleChatbot" / "src" / "simple_chatbot"
    src.mkdir(parents=True)
    (src / "main.py").write_text("ORIGIN = 'canonical'\n")

    # Physical override at the same target path inside the topic.
    override = topic.path / "simple_chatbot" / "main.py"
    override.parent.mkdir(parents=True)
    override.write_text("ORIGIN = 'local-override'\n")

    topic.includes.append(ResolvedInclude(source_root=src, as_path="simple_chatbot"))
    topic.build_file_map()

    by_logical = {f.path: f for f in topic.files}
    file_at_override = by_logical[override]
    # The local file wins: source_path is the on-disk override, not the canonical source.
    assert file_at_override.source_path == override
    assert file_at_override.source_origin is None

    # And a structured warning was recorded so validate-spec / build can surface it.
    categories = [w["category"] for w in course.loading_warnings]
    assert "include_shadowed_by_local" in categories


def test_ledger_authorized_shadow_suppresses_warning(tmp_path):
    """Files materialized by `clm sync-includes` shadow the include but
    are listed in the topic's `.clm-include` ledger — those shadowings
    are authorized and must not emit the include_shadowed_by_local
    warning."""
    import json as _json

    from clm.core.topic import ResolvedInclude

    course, topic, course_root = _make_isolated_topic(tmp_path)

    src = course_root / "examples" / "SimpleChatbot" / "src" / "simple_chatbot"
    src.mkdir(parents=True)
    (src / "main.py").write_text("ORIGIN = 'canonical'\n")

    # The materialized copy that `sync-includes` would have placed.
    materialized = topic.path / "simple_chatbot" / "main.py"
    materialized.parent.mkdir(parents=True)
    materialized.write_text("ORIGIN = 'canonical'\n")

    # Ledger entry pointing at this include.
    ledger_path = topic.path / ".clm-include"
    ledger_path.write_text(
        _json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "as_path": "simple_chatbot",
                        "source": "examples/SimpleChatbot/src/simple_chatbot",
                        "mode": "copy",
                    }
                ],
            }
        )
    )

    topic.includes.append(ResolvedInclude(source_root=src, as_path="simple_chatbot"))
    before = list(course.loading_warnings)
    topic.build_file_map()
    new_warnings = [
        w
        for w in course.loading_warnings
        if w not in before and w["category"] == "include_shadowed_by_local"
    ]
    assert new_warnings == [], (
        f"Ledger-authorized materialization should not warn, but got: {new_warnings}"
    )


def test_unauthorized_shadow_still_warns_when_ledger_missing_entry(tmp_path):
    """An on-disk override with no matching ledger entry still warns —
    only `sync-includes`-managed materializations get the pass."""
    import json as _json

    from clm.core.topic import ResolvedInclude

    course, topic, course_root = _make_isolated_topic(tmp_path)

    src = course_root / "examples" / "SimpleChatbot" / "src" / "simple_chatbot"
    src.mkdir(parents=True)
    (src / "main.py").write_text("ORIGIN = 'canonical'\n")

    override = topic.path / "simple_chatbot" / "main.py"
    override.parent.mkdir(parents=True)
    override.write_text("ORIGIN = 'local-override'\n")

    # Ledger exists but lists a *different* include — does not authorize.
    (topic.path / ".clm-include").write_text(
        _json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "as_path": "something_else",
                        "source": "examples/Other/src",
                        "mode": "copy",
                    }
                ],
            }
        )
    )

    topic.includes.append(ResolvedInclude(source_root=src, as_path="simple_chatbot"))
    topic.build_file_map()
    categories = [w["category"] for w in course.loading_warnings]
    assert "include_shadowed_by_local" in categories


def test_optional_include_with_missing_source_is_silent(tmp_path):
    from clm.core.topic import ResolvedInclude

    course, topic, course_root = _make_isolated_topic(tmp_path)

    missing = course_root / "examples" / "Missing"
    topic.includes.append(
        ResolvedInclude(
            source_root=missing,
            as_path="missing",
            optional=True,
        )
    )
    # Snapshot loading_errors so we ignore unrelated errors recorded by
    # Course.from_spec when the topic ID has no on-disk match (the
    # isolated-topic helper does not lay down a slides/module/topic dir).
    before = list(course.loading_errors)
    topic.build_file_map()

    assert list(topic.files) == []
    assert course.loading_errors == before  # no new include_source_missing


def test_required_include_with_missing_source_records_loading_error(tmp_path):
    from clm.core.topic import ResolvedInclude

    course, topic, course_root = _make_isolated_topic(tmp_path)

    missing = course_root / "examples" / "Missing"
    topic.includes.append(ResolvedInclude(source_root=missing, as_path="missing", optional=False))
    before_categories = {e["category"] for e in course.loading_errors}
    topic.build_file_map()

    after_categories = {e["category"] for e in course.loading_errors}
    new_categories = after_categories - before_categories
    assert "include_source_missing" in new_categories
    assert list(topic.files) == []


def test_include_skips_pycache_and_venv(tmp_path):
    """Splicing must not pull in `__pycache__/` or `.venv/` from the source dir."""
    from clm.core.topic import ResolvedInclude

    course, topic, course_root = _make_isolated_topic(tmp_path)
    src = course_root / "examples" / "Foo" / "src" / "foo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("\n")
    cache = src / "__pycache__"
    cache.mkdir()
    (cache / "compiled.pyc").write_bytes(b"\x00\x00")
    venv = src / ".venv"
    venv.mkdir()
    (venv / "marker").write_text("")

    topic.includes.append(ResolvedInclude(source_root=src, as_path="foo"))
    topic.build_file_map()

    spliced = {f.path.name for f in topic.files}
    assert spliced == {"__init__.py"}


def test_build_file_map_skips_sync_includes_ledger(tmp_path):
    """The `.clm-include` ledger at the topic root must not enter the file map.

    Regression for PR1.7 smoke test: the ledger was leaking into output as
    student-visible `.clm-include` files alongside the materialized includes.
    """
    course, topic, course_root = _make_isolated_topic(tmp_path)
    (topic.path / "slides_010.py").write_text("# slide content\n")
    (topic.path / ".clm-include").write_text('{"version": 1, "entries": []}')

    topic.build_file_map()

    names = {f.path.name for f in topic.files}
    assert names == {"slides_010.py"}, f"ledger leaked: {names}"
