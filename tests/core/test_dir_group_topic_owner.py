"""Optional owning topic for global dir-groups and their subdirs (#1288).

``<subdir topic="a b">`` (or ``<dir-group topic="…">`` for the whole group)
lists candidate owners; the first one in course order whose section and
subsection are enabled owns the files. Owned files are recorded in the
provenance manifest under that topic, so ``clm release`` ships them with it
instead of freezing them into the skeleton. With no enabled candidate the
subdir is not copied; an unknown topic id is a spec error.
"""

import io
from pathlib import Path

import pytest

from clm.core.course import Course
from clm.core.course_spec import CourseSpec, find_unknown_elements
from clm.core.provenance_manifest import enumerate_expected_outputs, manifest_files_by_topic
from tests.conftest import PytestLocalOpsBackend

SPEC_TEMPLATE = """\
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <description><de>D</de><en>E</en></description>
    <certificate><de>C</de><en>C</en></certificate>
    <sections>
        <section id="s0" enabled="false">
            <name><de>S0</de><en>S0</en></name>
            <topics><topic>t_off</topic></topics>
        </section>
        <section id="s1">
            <name><de>S1</de><en>S1</en></name>
            <topics>
                <topic>t_a</topic>
                <subsection enabled="false">
                    <name><de>Sub</de><en>Sub</en></name>
                    <topic>t_sub_off</topic>
                </subsection>
                <topic>t_b</topic>
            </topics>
        </section>
    </sections>
    <dir-groups>
{groups}
    </dir-groups>
</course>
"""

COMPLETED = """\
        <dir-group include-root-files="true">
            <name>code/completed</name>
            <path>code/completed</path>
            <subdirs>
                <subdir topic="t_a">proj_a</subdir>
                <subdir topic="t_b t_off">proj_shared</subdir>
                <subdir topic="t_off t_sub_off">proj_gone</subdir>
                <subdir>proj_free</subdir>
            </subdirs>
        </dir-group>"""

WHOLE = """\
        <dir-group topic="t_b">
            <name>code/whole</name>
            <path>code/whole</path>
        </dir-group>"""

# An empty <name> walks the whole output root; owned files must still win.
ROOT_WALK = """\
        <dir-group>
            <name></name>
            <path>toplevel</path>
        </dir-group>"""


def _spec(*groups: str, keep_disabled: bool = False) -> CourseSpec:
    xml = SPEC_TEMPLATE.format(groups="\n".join(groups))
    return CourseSpec.from_file(io.StringIO(xml), keep_disabled=keep_disabled)


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def course_root(tmp_path: Path) -> Path:
    root = tmp_path / "course"
    completed = root / "code" / "completed"
    _write(completed / "CMakeLists.txt")
    for proj in ("proj_a", "proj_shared", "proj_gone", "proj_free"):
        _write(completed / proj / "main.cpp")
    _write(root / "code" / "whole" / "README.md")
    _write(root / "code" / "whole" / "sub" / "a.txt")
    _write(root / "toplevel" / ".gitattributes")
    (root / "slides").mkdir()
    return root


async def _build_dir_groups(spec: CourseSpec, course_root: Path, out: Path) -> Course:
    course = Course.from_spec(spec, course_root, out)
    async with PytestLocalOpsBackend() as backend:
        await course.process_dir_group_for_targets(backend)
    return course


def _dir_group_records(course: Course, target_name: str = "shared") -> dict[str, dict]:
    target = next(t for t in course.output_targets if t.name == target_name)
    root = target.output_root
    records: dict[str, dict] = {}
    for path, record in enumerate_expected_outputs(course, target):
        if record["format"] != "dir-group" or record["language"] != "de":
            continue
        # First entry per path wins, exactly as the manifest builder keeps it.
        records.setdefault(path.relative_to(root).as_posix(), record)
    return records


class TestResolution:
    def test_subdir_owners_follow_first_enabled_candidate_in_course_order(self):
        (dg,) = _spec(COMPLETED).dictionaries
        # proj_gone (only disabled candidates) is dropped from the copy.
        assert dg.subdirs == ["proj_a", "proj_shared", "proj_free"]
        assert [dg.subdir_owner(i) for i in range(3)] == [
            ("s1", "t_a"),
            # t_off comes first in course order, but its section is disabled.
            ("s1", "t_b"),
            (None, None),
        ]
        assert dg.topic_id is None and dg.include_root_files
        assert dg.ownership_errors == ()
        (warning,) = dg.ownership_warnings
        assert "proj_gone" in warning and "not copied" in warning

    def test_disabled_subsection_counts_as_disabled(self):
        group = COMPLETED.replace('topic="t_a"', 'topic="t_sub_off t_b"')
        (dg,) = _spec(group).dictionaries
        assert dg.subdir_owner(0) == ("s1", "t_b")

    def test_keep_disabled_treats_every_section_as_enabled(self):
        (dg,) = _spec(COMPLETED, keep_disabled=True).dictionaries
        assert dg.subdirs == ["proj_a", "proj_shared", "proj_gone", "proj_free"]
        assert dg.subdir_owner(1) == ("s0", "t_off")
        assert dg.subdir_owner(2) == ("s0", "t_off")
        assert dg.ownership_warnings == ()

    def test_group_level_topic_owns_the_whole_group(self):
        (dg,) = _spec(WHOLE).dictionaries
        assert (dg.section_id, dg.topic_id) == ("s1", "t_b")

    def test_subdir_topic_overrides_group_topic(self):
        group = COMPLETED.replace(
            '<dir-group include-root-files="true">',
            '<dir-group include-root-files="true" topic="t_b">',
        )
        (dg,) = _spec(group).dictionaries
        assert dg.subdir_owner(0) == ("s1", "t_a")
        # The unowned subdir inherits the group's owner.
        assert dg.subdir_owner(2) == ("s1", "t_b")
        assert (dg.section_id, dg.topic_id) == ("s1", "t_b")

    def test_dropped_group_copies_nothing(self):
        group = WHOLE.replace('topic="t_b"', 'topic="t_off"')
        (dg,) = _spec(group).dictionaries
        assert dg.recursive is False and not dg.include_root_files and not dg.subdirs
        assert dg.ownership_warnings

    def test_all_subdirs_dropped_never_falls_back_to_a_whole_tree_copy(self):
        group = """\
        <dir-group include-root-files="true">
            <name>code/completed</name>
            <path>code/completed</path>
            <subdirs><subdir topic="t_off">proj_gone</subdir></subdirs>
        </dir-group>"""
        (dg,) = _spec(group).dictionaries
        assert dg.subdirs == []
        # An empty subdir list with recursive=True would copy the whole tree.
        assert dg.recursive is False
        assert dg.include_root_files

    def test_without_topic_attributes_specs_are_unchanged(self):
        group = COMPLETED.replace(' topic="t_a"', "").replace(' topic="t_b t_off"', "")
        group = group.replace(' topic="t_off t_sub_off"', "")
        (dg,) = _spec(group).dictionaries
        assert dg.subdirs == ["proj_a", "proj_shared", "proj_gone", "proj_free"]
        assert dg.subdir_owners is None
        assert dg.subdir_owner(0) == (None, None)


class TestValidation:
    def test_valid_ownership_validates(self):
        assert _spec(COMPLETED, WHOLE).validate() == []

    def test_unknown_topic_id_is_a_spec_error(self):
        spec = _spec(COMPLETED.replace('topic="t_a"', 'topic="t_typo"'))
        errors = spec.validate()
        assert any("unknown topic id 't_typo'" in e and "proj_a" in e for e in errors), errors

    def test_unknown_group_topic_id_is_a_spec_error(self):
        spec = _spec(WHOLE.replace('topic="t_b"', 'topic="nope"'))
        assert any("unknown topic id 'nope'" in e for e in spec.validate())

    def test_topic_on_a_topic_scoped_dir_group_is_an_error(self):
        xml = SPEC_TEMPLATE.format(groups="").replace(
            "<topic>t_a</topic>",
            '<topic>t_a<dir-group topic="t_b"><name>x</name><path>x</path></dir-group></topic>',
        )
        spec = CourseSpec.from_file(io.StringIO(xml))
        errors = spec.validate()
        assert any("only valid on a global" in e for e in errors), errors
        # The enclosing topic still owns it.
        (dg,) = spec.dictionaries
        assert dg.topic_id == "t_a"

    def test_misspelt_topic_attribute_is_reported(self):
        from xml.etree import ElementTree as ETree

        xml = SPEC_TEMPLATE.format(groups=COMPLETED.replace('topic="t_a"', 'topics="t_a"'))
        found = find_unknown_elements(ETree.fromstring(xml))
        assert any(f.attribute == "topics" and f.tag == "subdir" for f in found), found

    def test_clm_validate_reports_errors_and_warnings(self, course_root):
        from clm.slides.spec_validator import validate_spec

        xml = SPEC_TEMPLATE.format(groups=COMPLETED.replace('topic="t_a"', 'topic="t_typo"'))
        spec_path = course_root / "course.xml"
        spec_path.write_text(xml, encoding="utf-8")
        result = validate_spec(spec_path, course_root / "slides")
        types = {(f.severity, f.type) for f in result.findings}
        assert ("error", "invalid_dir_group_owner") in types
        assert ("warning", "disabled_dir_group_owner") in types


class TestBuildAndManifest:
    async def test_dropped_subdir_is_not_copied(self, course_root, tmp_path):
        out = tmp_path / "out"
        await _build_dir_groups(_spec(COMPLETED), course_root, out)
        completed = out / "shared" / "Kurs-de" / "code" / "completed"
        assert sorted(p.name for p in completed.iterdir()) == [
            "CMakeLists.txt",
            "proj_a",
            "proj_free",
            "proj_shared",
        ]

    async def test_manifest_records_subdir_owners(self, course_root, tmp_path):
        out = tmp_path / "out"
        course = await _build_dir_groups(_spec(COMPLETED, WHOLE), course_root, out)
        records = _dir_group_records(course)
        base = "Kurs-de/code"
        assert records[f"{base}/completed/proj_a/main.cpp"]["topic_id"] == "t_a"
        assert records[f"{base}/completed/proj_a/main.cpp"]["section_id"] == "s1"
        assert records[f"{base}/completed/proj_shared/main.cpp"]["topic_id"] == "t_b"
        assert records[f"{base}/completed/proj_free/main.cpp"]["topic_id"] is None
        assert records[f"{base}/whole/README.md"]["topic_id"] == "t_b"
        assert records[f"{base}/whole/sub/a.txt"]["topic_id"] == "t_b"

    async def test_root_files_of_a_subdir_group_reach_the_manifest_as_skeleton(
        self, course_root, tmp_path
    ):
        """Before #1288 the root files of a dir-group with <subdirs> were never
        walked; they reached CppCourses' manifest only because an empty-named
        group walked the whole output root."""
        out = tmp_path / "out"
        course = await _build_dir_groups(_spec(COMPLETED), course_root, out)
        records = _dir_group_records(course)
        root_file = records["Kurs-de/code/completed/CMakeLists.txt"]
        assert root_file["topic_id"] is None

    async def test_owned_files_win_over_an_overlapping_root_walk(self, course_root, tmp_path):
        out = tmp_path / "out"
        # The root walk is declared first, so document order alone would let
        # it claim every file as skeleton.
        course = await _build_dir_groups(_spec(ROOT_WALK, COMPLETED), course_root, out)
        records = _dir_group_records(course)
        assert records["Kurs-de/code/completed/proj_a/main.cpp"]["topic_id"] == "t_a"
        assert records["Kurs-de/code/completed/proj_free/main.cpp"]["topic_id"] is None
        assert records["Kurs-de/.gitattributes"]["topic_id"] is None

    async def test_release_groups_owned_files_with_their_topic(self, course_root, tmp_path):
        """What ``clm release`` consumes: owned files are no longer skeleton."""
        out = tmp_path / "out"
        course = await _build_dir_groups(_spec(COMPLETED), course_root, out)
        files = [{"path": path, **record} for path, record in _dir_group_records(course).items()]
        by_topic = manifest_files_by_topic({"files": files})
        assert {e["path"] for e in by_topic["t_a"]} == {"Kurs-de/code/completed/proj_a/main.cpp"}
        skeleton = {e["path"] for e in by_topic[None]}
        assert "Kurs-de/code/completed/CMakeLists.txt" in skeleton
        assert "Kurs-de/code/completed/proj_free/main.cpp" in skeleton
        assert not any("proj_a" in p or "proj_shared" in p for p in skeleton)
