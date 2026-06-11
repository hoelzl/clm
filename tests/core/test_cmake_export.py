"""Tests for the C++ code-export CMake generation (#333 phase 2).

The integration tests follow the provenance-manifest test pattern: build a
real ``Course`` (here: a minimal C++ course materialized under ``tmp_path``),
discover output paths via the same enumeration the build uses, write a
subset to disk, and assert the generator picks up exactly those.
"""

import io

from clm.core.cmake_export import (
    CMAKELISTS_FILENAME,
    cmake_identifier,
    collect_cpp_code_outputs,
    deck_target_name,
    generate_cmakelists,
    write_cmake_projects,
)
from clm.core.course import Course
from clm.core.course_spec import CourseSpec
from clm.core.provenance_manifest import enumerate_expected_outputs

COURSE_CPP_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/cpp-course-de</de>
        <en>https://github.com/hoelzl/cpp-course-en</en>
    </github>
    <name>
        <de>C++ Kurs</de>
        <en>C++ Course</en>
    </name>
    <prog-lang>cpp</prog-lang>
    <description>
        <de>Ein C++ Kurs</de>
        <en>A C++ course</en>
    </description>
    <certificate>
        <de>...</de>
        <en>...</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>intro</topic>
                <topic>more</topic>
            </topics>
        </section>
    </sections>
</course>
"""

CPP_DECK_TEMPLATE = """\
// j2 from 'macros.j2' import header
// {{{{ header("{de} De", "{en} En") }}}}

// %%
int x = 1;
"""


def _make_cpp_course(tmp_path) -> Course:
    data_dir = tmp_path / "course"
    for topic, slug in (("topic_100_intro", "intro"), ("topic_110_more", "more")):
        topic_dir = data_dir / "slides" / "module_100_test" / topic
        topic_dir.mkdir(parents=True)
        (topic_dir / f"slides_{slug}.cpp").write_text(
            CPP_DECK_TEMPLATE.format(de=slug.title(), en=slug.title()), encoding="utf-8"
        )
    spec = CourseSpec.from_file(io.StringIO(COURSE_CPP_XML))
    return Course.from_spec(spec, data_dir, tmp_path / "out")


def _materialize_code_outputs(course, target, limit: int | None = None) -> list:
    written = []
    for out_path, record in enumerate_expected_outputs(course, target):
        if record["format"] != "code" or out_path.suffix != ".cpp":
            continue
        if limit is not None and len(written) >= limit:
            break
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("int main() {}\n", encoding="utf-8")
        written.append(out_path)
    return written


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestCmakeIdentifier:
    def test_folds_umlauts_and_spaces(self):
        assert cmake_identifier("01 Einführung") == "01_einfuhrung"

    def test_strips_special_characters(self):
        assert cmake_identifier("C++ Best Practice-de Completed") == "c_best_practice_de_completed"

    def test_empty_input_falls_back(self):
        assert cmake_identifier("§§§") == "deck"


class TestDeckTargetName:
    def test_section_and_deck_numbers(self):
        name = deck_target_name("01 Einführung/03 Entwicklungsumgebungen.cpp")
        assert name == "s01_03_entwicklungsumgebungen"

    def test_deck_without_section_dir(self):
        assert deck_target_name("01 Intro.cpp") == "deck_01_intro"

    def test_unnumbered_section(self):
        assert deck_target_name("Bonus/01 Intro.cpp") == "bonus_01_intro"


class TestGenerateCmakelists:
    def test_basic_structure(self):
        text = generate_cmakelists(
            "My Course Completed",
            ["01 Intro/01 Hello.cpp", "01 Intro/02 World.cpp"],
        )
        assert "cmake_minimum_required(VERSION 3.21)" in text
        assert "project(my_course_completed LANGUAGES CXX)" in text
        assert "set(CMAKE_CXX_STANDARD 20)" in text
        assert 'add_executable(s01_01_hello "01 Intro/01 Hello.cpp")' in text
        assert 'add_executable(s01_02_world "01 Intro/02 World.cpp")' in text
        assert text.endswith("\n")

    def test_targets_sorted_deterministically(self):
        text = generate_cmakelists("p", ["02 B/01 X.cpp", "01 A/01 X.cpp"])
        assert text.index("01 A/01 X.cpp") < text.index("02 B/01 X.cpp")

    def test_duplicate_target_names_disambiguated(self):
        text = generate_cmakelists("p", ["01 A/01 Same.cpp", "01 B/01 Same.cpp"])
        assert "add_executable(s01_01_same " in text
        assert "add_executable(s01_01_same_2 " in text

    def test_msvc_utf8_flag(self):
        assert "/utf-8" in generate_cmakelists("p", ["01 A/01 X.cpp"])


# ---------------------------------------------------------------------------
# Course integration
# ---------------------------------------------------------------------------


class TestWriteCmakeProjects:
    def test_writes_one_cmakelists_per_code_kind_dir(self, tmp_path):
        course = _make_cpp_course(tmp_path)
        target = course.output_targets[0]
        outputs = _materialize_code_outputs(course, target)
        assert outputs, "the cpp course should enumerate code outputs"

        written = write_cmake_projects(course)

        kind_roots = {p.parent.parent for p in outputs}
        assert {w.parent for w in written} == kind_roots
        for path in written:
            assert path.name == CMAKELISTS_FILENAME
            text = path.read_text(encoding="utf-8")
            assert text.count("add_executable(") == 2
            assert "CMAKE_CXX_STANDARD 20" in text

    def test_lists_only_existing_outputs(self, tmp_path):
        course = _make_cpp_course(tmp_path)
        target = course.output_targets[0]
        _materialize_code_outputs(course, target, limit=1)

        written = write_cmake_projects(course)

        assert len(written) == 1
        text = written[0].read_text(encoding="utf-8")
        assert text.count("add_executable(") == 1

    def test_collect_groups_by_kind_root(self, tmp_path):
        course = _make_cpp_course(tmp_path)
        target = course.output_targets[0]
        outputs = _materialize_code_outputs(course, target)

        groups = collect_cpp_code_outputs(course)

        assert set(groups) == {p.parent.parent for p in outputs}
        for project in groups.values():
            assert all(deck.endswith(".cpp") for deck in project.decks)

    def test_no_outputs_written_without_built_files(self, tmp_path):
        course = _make_cpp_course(tmp_path)
        course.output_targets[0].output_root.mkdir(parents=True, exist_ok=True)
        assert write_cmake_projects(course) == []

    def test_noop_for_python_course(self, course_1):
        target = course_1.output_targets[0]
        target.output_root.mkdir(parents=True, exist_ok=True)
        assert write_cmake_projects(course_1) == []
