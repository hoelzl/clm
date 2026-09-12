"""Tests for the C++ code-export CMake generation (#333 phase 2).

The integration tests follow the provenance-manifest test pattern: build a
real ``Course`` (here: a minimal C++ course materialized under ``tmp_path``),
discover output paths via the same enumeration the build uses, write a
subset to disk, and assert the generator picks up exactly those.
"""

import io

from clm.core.cmake_export import (
    CMAKELISTS_FILENAME,
    SUPPORT_INCLUDE_DIRNAME,
    cmake_identifier,
    collect_cpp_code_outputs,
    deck_target_name,
    generate_cmake_files,
    generate_cmakelists,
    needed_support_headers,
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


class TestGenerateCmakeFiles:
    """Phase 3 of #928: a root project plus one standalone project per module."""

    def test_root_adds_one_subdirectory_per_module(self):
        files = generate_cmake_files(
            "My Course Completed",
            ["01 Intro/01 Hello.cpp", "01 Intro/02 World.cpp", "02 More/01 Deep.cpp"],
        )
        assert set(files) == {
            "CMakeLists.txt",
            "01 Intro/CMakeLists.txt",
            "02 More/CMakeLists.txt",
        }
        root = files["CMakeLists.txt"]
        assert "cmake_minimum_required(VERSION 3.21)" in root
        assert "project(my_course_completed LANGUAGES CXX)" in root
        assert "set(CMAKE_CXX_STANDARD 20)" in root
        assert "set(CLM_CODE_EXPORT_CONFIGURED ON)" in root
        assert root.index('add_subdirectory("01 Intro")') < root.index(
            'add_subdirectory("02 More")'
        )
        assert "add_executable(" not in root
        assert root.endswith("\n")

    def test_module_project_lists_its_decks_relative_to_the_module(self):
        files = generate_cmake_files("p", ["01 Intro/01 Hello.cpp", "01 Intro/02 World.cpp"])
        module = files["01 Intro/CMakeLists.txt"]
        assert "project(p_01_intro LANGUAGES CXX)" in module
        assert 'add_executable(s01_01_hello "01 Hello.cpp")' in module
        assert 'add_executable(s01_02_world "02 World.cpp")' in module
        assert module.endswith("\n")

    def test_module_project_configures_the_toolchain_only_when_standalone(self):
        files = generate_cmake_files("p", ["01 Intro/01 Hello.cpp"], with_include_dir=True)
        module = files["01 Intro/CMakeLists.txt"]
        assert "if(NOT CLM_CODE_EXPORT_CONFIGURED)" in module
        block = module[
            module.index("if(NOT CLM_CODE_EXPORT_CONFIGURED)") : module.index("endif()\n\nadd_")
        ]
        assert "    set(CMAKE_CXX_STANDARD 20)" in block
        assert "    add_compile_options(/utf-8)" in block
        # The include dir is reached from the module directory.
        assert (
            f'    include_directories("${{CMAKE_CURRENT_SOURCE_DIR}}/../{SUPPORT_INCLUDE_DIRNAME}")'
            in block
        )
        root = files["CMakeLists.txt"]
        assert (
            f'include_directories("${{CMAKE_CURRENT_SOURCE_DIR}}/{SUPPORT_INCLUDE_DIRNAME}")'
            in root
        )

    def test_no_include_dir_lines_without_support_headers(self):
        files = generate_cmake_files("p", ["01 Intro/01 Hello.cpp"])
        assert all("include_directories" not in text for text in files.values())

    def test_workshop_files_become_targets_named_after_their_deck(self):
        files = generate_cmake_files(
            "p",
            ["01 Intro/01 Hello.cpp"],
            workshops={
                "01 Intro/01 Hello.cpp": [
                    "01 Intro/01 Hello_workshop_2.cpp",
                    "01 Intro/01 Hello_workshop_1.cpp",
                ]
            },
        )
        module = files["01 Intro/CMakeLists.txt"]
        lines = [line for line in module.splitlines() if line.startswith("add_executable(")]
        assert lines == [
            'add_executable(s01_01_hello "01 Hello.cpp")',
            'add_executable(s01_01_hello_workshop_1 "01 Hello_workshop_1.cpp")',
            'add_executable(s01_01_hello_workshop_2 "01 Hello_workshop_2.cpp")',
        ]

    def test_targets_sorted_deterministically(self):
        files = generate_cmake_files("p", ["02 B/01 X.cpp", "01 A/01 X.cpp"])
        root = files["CMakeLists.txt"]
        assert root.index('add_subdirectory("01 A")') < root.index('add_subdirectory("02 B")')

    def test_duplicate_target_names_disambiguated_project_wide(self):
        # Target names are global in CMake, so the suffix must be assigned
        # across modules, and workshop targets follow the disambiguated name.
        files = generate_cmake_files(
            "p",
            ["01 A/01 Same.cpp", "01 B/01 Same.cpp"],
            workshops={"01 B/01 Same.cpp": ["01 B/01 Same_workshop_1.cpp"]},
        )
        assert "add_executable(s01_01_same " in files["01 A/CMakeLists.txt"]
        assert "add_executable(s01_01_same_2 " in files["01 B/CMakeLists.txt"]
        assert "add_executable(s01_01_same_2_workshop_1 " in files["01 B/CMakeLists.txt"]

    def test_msvc_utf8_flag(self):
        assert "/utf-8" in generate_cmakelists("p", ["01 A/01 X.cpp"])

    def test_excluded_decks_and_their_workshops_are_exclude_from_all(self):
        files = generate_cmake_files(
            "p",
            ["01 A/01 X.cpp", "01 A/02 Y.cpp"],
            excluded={"01 A/02 Y.cpp"},
            workshops={"01 A/02 Y.cpp": ["01 A/02 Y_workshop_1.cpp"]},
        )
        module = files["01 A/CMakeLists.txt"]
        assert 'add_executable(s01_01_x "01 X.cpp")' in module
        assert 'add_executable(s01_02_y EXCLUDE_FROM_ALL "02 Y.cpp")' in module
        assert (
            'add_executable(s01_02_y_workshop_1 EXCLUDE_FROM_ALL "02 Y_workshop_1.cpp")' in module
        )

    def test_deck_at_the_kind_root_is_a_root_target(self):
        files = generate_cmake_files("p", ["01 Intro.cpp"])
        assert set(files) == {"CMakeLists.txt"}
        assert 'add_executable(deck_01_intro "01 Intro.cpp")' in files["CMakeLists.txt"]

    def test_generate_cmakelists_is_the_root_file(self):
        files = generate_cmake_files("p", ["01 A/01 X.cpp"])
        assert generate_cmakelists("p", ["01 A/01 X.cpp"]) == files["CMakeLists.txt"]


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
        module_dirs = {p.parent for p in outputs}
        root_files = [w for w in written if w.parent in kind_roots]
        module_files = [w for w in written if w.parent in module_dirs]
        assert {w.parent for w in root_files} == kind_roots
        assert {w.parent for w in module_files} == module_dirs
        assert len(written) == len(root_files) + len(module_files)
        for path in written:
            assert path.name == CMAKELISTS_FILENAME
        for path in root_files:
            text = path.read_text(encoding="utf-8")
            assert text.count("add_subdirectory(") == 1
            assert "CMAKE_CXX_STANDARD 20" in text
        for path in module_files:
            assert path.read_text(encoding="utf-8").count("add_executable(") == 2

    def test_lists_only_existing_outputs(self, tmp_path):
        course = _make_cpp_course(tmp_path)
        target = course.output_targets[0]
        _materialize_code_outputs(course, target, limit=1)

        written = write_cmake_projects(course)

        # One kind root got outputs: its root file plus one module file.
        assert len(written) == 2
        module_file = next(w for w in written if "add_executable(" in w.read_text(encoding="utf-8"))
        assert module_file.read_text(encoding="utf-8").count("add_executable(") == 1

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

    def test_vendored_headers_copied_when_referenced(self, tmp_path):
        course = _make_cpp_course(tmp_path)
        target = course.output_targets[0]
        outputs = _materialize_code_outputs(course, target)
        # One TU references the xcpp display shim (e.g. via xcpp::display).
        outputs[0].write_text("#include <xcpp/xdisplay.hpp>\nint main() {}\n", encoding="utf-8")

        written = write_cmake_projects(course)

        kind_root = outputs[0].parent.parent
        include_dir = kind_root / SUPPORT_INCLUDE_DIRNAME
        # The shim pulls in the vendored nlohmann header as well.
        assert (include_dir / "xcpp" / "xdisplay.hpp").is_file()
        assert (include_dir / "nlohmann" / "json.hpp").is_file()
        cmake = (kind_root / CMAKELISTS_FILENAME).read_text(encoding="utf-8")
        assert (
            f'include_directories("${{CMAKE_CURRENT_SOURCE_DIR}}/{SUPPORT_INCLUDE_DIRNAME}")'
            in cmake
        )
        # Other kind roots don't reference the headers: no include dir there,
        # in neither the root nor the module project files.
        other_files = [w for w in written if kind_root not in w.parents]
        assert other_files
        for path in other_files:
            assert not (path.parent / SUPPORT_INCLUDE_DIRNAME).exists()
            assert "include_directories" not in path.read_text(encoding="utf-8")

    def test_display_helper_header_copied_when_referenced(self, tmp_path):
        # The #928 emitter includes the labeled-display helper instead of
        # inlining it; the CMake export must vendor it like the xcpp shim.
        course = _make_cpp_course(tmp_path)
        target = course.output_targets[0]
        outputs = _materialize_code_outputs(course, target)
        outputs[0].write_text(
            "#include <clm/display.hpp>\nint main() { CLM_DISPLAY(1 + 1); }\n", encoding="utf-8"
        )

        write_cmake_projects(course)

        kind_root = outputs[0].parent.parent
        header = kind_root / SUPPORT_INCLUDE_DIRNAME / "clm" / "display.hpp"
        assert header.is_file()
        assert "#define CLM_DISPLAY" in header.read_text(encoding="utf-8")
        # Standalone: the display helper does not drag in nlohmann.
        assert not (kind_root / SUPPORT_INCLUDE_DIRNAME / "nlohmann").exists()
        cmake = (kind_root / CMAKELISTS_FILENAME).read_text(encoding="utf-8")
        assert "include_directories" in cmake

    def test_deck_local_header_triggers_vendoring(self, tmp_path):
        course = _make_cpp_course(tmp_path)
        target = course.output_targets[0]
        outputs = _materialize_code_outputs(course, target)
        # nlohmann referenced from a header next to the TU, not the TU itself.
        section_dir = outputs[0].parent
        (section_dir / "point.hpp").write_text(
            "#pragma once\n#include <nlohmann/json.hpp>\n", encoding="utf-8"
        )

        write_cmake_projects(course)

        kind_root = section_dir.parent
        assert (kind_root / SUPPORT_INCLUDE_DIRNAME / "nlohmann" / "json.hpp").is_file()
        assert not (kind_root / SUPPORT_INCLUDE_DIRNAME / "xcpp").exists()

    def test_vendored_headers_do_not_self_trigger_on_regeneration(self, tmp_path):
        kind_root = tmp_path / "Completed"
        include_dir = kind_root / SUPPORT_INCLUDE_DIRNAME / "xcpp"
        include_dir.mkdir(parents=True)
        # Only the vendored copy mentions the tokens.
        (include_dir / "xdisplay.hpp").write_text(
            "#include <nlohmann/json.hpp>\n", encoding="utf-8"
        )
        assert needed_support_headers(kind_root) == set()

    def test_no_compile_marker_excludes_deck_from_all(self, tmp_path):
        course = _make_cpp_course(tmp_path)
        # Mark the "more" deck as not compilable outside the kernel.
        deck_path = (
            tmp_path
            / "course"
            / "slides"
            / "module_100_test"
            / "topic_110_more"
            / "slides_more.cpp"
        )
        deck_path.write_text(
            "// clm: no-compile\n" + deck_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        target = course.output_targets[0]
        _materialize_code_outputs(course, target)

        written = write_cmake_projects(course)

        module_files = [w for w in written if "add_executable(" in w.read_text(encoding="utf-8")]
        assert module_files
        for path in module_files:
            text = path.read_text(encoding="utf-8")
            more_line = next(line for line in text.splitlines() if "More" in line)
            assert "EXCLUDE_FROM_ALL" in more_line
            intro_line = next(line for line in text.splitlines() if "Intro" in line)
            assert "EXCLUDE_FROM_ALL" not in intro_line

    def test_workshop_files_next_to_a_deck_become_targets(self, tmp_path):
        # The emitter writes ``<deck>.hpp`` and ``<deck>_workshop_N.cpp``
        # next to the deck (#928 phase 3); the header is no target, every
        # workshop file is one, and a stale-looking name of another deck
        # is not picked up.
        course = _make_cpp_course(tmp_path)
        target = course.output_targets[0]
        outputs = _materialize_code_outputs(course, target)
        deck = outputs[0]
        (deck.parent / f"{deck.stem}.hpp").write_text("#pragma once\n", encoding="utf-8")
        for n in (1, 2):
            (deck.parent / f"{deck.stem}_workshop_{n}.cpp").write_text(
                f'#include "{deck.stem}.hpp"\nint main() {{}}\n', encoding="utf-8"
            )
        (deck.parent / "Other_workshop_1.cpp").write_text("int main() {}\n", encoding="utf-8")

        groups = collect_cpp_code_outputs(course)
        kind_root = deck.parent.parent
        project = groups[kind_root]
        deck_rel = deck.relative_to(kind_root).as_posix()
        assert project.workshops == {
            deck_rel: [
                f"{deck.parent.name}/{deck.stem}_workshop_1.cpp",
                f"{deck.parent.name}/{deck.stem}_workshop_2.cpp",
            ]
        }

        write_cmake_projects(course)

        module = (deck.parent / CMAKELISTS_FILENAME).read_text(encoding="utf-8")
        deck_target = (
            next(line for line in module.splitlines() if line.endswith(f'"{deck.name}")'))
            .split("(")[1]
            .split(" ")[0]
        )
        assert f'add_executable({deck_target}_workshop_1 "{deck.stem}_workshop_1.cpp")' in module
        assert f'add_executable({deck_target}_workshop_2 "{deck.stem}_workshop_2.cpp")' in module
        assert ".hpp" not in module
        assert "Other_workshop_1" not in module
