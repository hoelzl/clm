"""Language-scoped ``<dir-group lang="de|en">`` (#1031).

A dir-group with ``lang`` is copied only into that language's output; one
without it is copied into every language as before. A ``de``/``en`` pair
sharing one ``<name>`` is the intended use: each language gets its own
project under the same directory name. The provenance manifest must follow
the same rule, or each group claims the other's files.
"""

import io
from pathlib import Path

import pytest

from clm.core.course import Course
from clm.core.course_spec import CourseSpec
from tests.conftest import PytestLocalOpsBackend

SPEC_XML = """\
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <description><de>D</de><en>E</en></description>
    <certificate><de>C</de><en>C</en></certificate>
    <sections>
        <section>
            <name><de>S</de><en>S</en></name>
            <topics></topics>
        </section>
    </sections>
    <dir-groups>
        <dir-group lang="de">
            <name>Code/Breakout</name>
            <path>examples/Breakout/de</path>
        </dir-group>
        <dir-group lang="en">
            <name>Code/Breakout</name>
            <path>examples/Breakout/en</path>
        </dir-group>
        <dir-group>
            <name>Shared</name>
            <path>examples/Shared</path>
        </dir-group>
    </dir-groups>
</course>
"""


def _spec(xml: str = SPEC_XML) -> CourseSpec:
    return CourseSpec.from_file(io.StringIO(xml))


@pytest.fixture
def course_root(tmp_path: Path) -> Path:
    root = tmp_path / "course"
    for lang in ("de", "en"):
        project = root / "examples" / "Breakout" / lang
        project.mkdir(parents=True)
        (project / f"README.{lang}.md").write_text(lang, encoding="utf-8")
        (project / "main.py").write_text(f"# {lang}\n", encoding="utf-8")
    shared = root / "examples" / "Shared"
    shared.mkdir(parents=True)
    (shared / "data.csv").write_text("x\n", encoding="utf-8")
    (root / "slides").mkdir()
    return root


async def _copy_dir_groups(course: Course) -> None:
    """Copy every dir-group for every output target, as ``clm build`` does."""
    async with PytestLocalOpsBackend() as backend:
        await course.process_dir_group_for_targets(backend)


class TestSpec:
    def test_lang_is_parsed(self):
        langs = [dg.lang for dg in _spec().dictionaries]
        assert langs == ["de", "en", None]

    def test_valid_lang_validates(self):
        assert _spec().validate() == []

    def test_unknown_lang_is_a_spec_error(self):
        spec = _spec(SPEC_XML.replace('lang="en"', 'lang="fr"'))
        errors = spec.validate()
        assert any("invalid lang 'fr'" in e for e in errors), errors

    def test_output_languages(self):
        de, en, shared = _spec().dictionaries
        assert de.output_languages(["de", "en"]) == ["de"]
        assert en.output_languages(["de", "en"]) == ["en"]
        assert shared.output_languages(["de", "en"]) == ["de", "en"]
        assert en.output_languages(["de"]) == []


class TestCopy:
    async def test_each_language_gets_only_its_project(self, course_root, tmp_path):
        out = tmp_path / "out"
        course = Course.from_spec(_spec(), course_root, out)
        await _copy_dir_groups(course)

        assert {t.name for t in course.output_targets} == {"shared", "trainer", "speaker"}
        for toplevel in ("shared", "trainer", "speaker"):
            de = out / toplevel / "Kurs-de" / "Code" / "Breakout"
            en = out / toplevel / "Course-en" / "Code" / "Breakout"
            assert sorted(p.name for p in de.iterdir()) == ["README.de.md", "main.py"]
            assert sorted(p.name for p in en.iterdir()) == ["README.en.md", "main.py"]
            assert (de / "main.py").read_text(encoding="utf-8") == "# de\n"
            assert (en / "main.py").read_text(encoding="utf-8") == "# en\n"
            # The unscoped group still reaches both languages.
            assert (out / toplevel / "Kurs-de" / "Shared" / "data.csv").is_file()
            assert (out / toplevel / "Course-en" / "Shared" / "data.csv").is_file()

    async def test_a_language_filter_excluding_the_group_copies_nothing(
        self, course_root, tmp_path
    ):
        course = Course.from_spec(_spec(), course_root, tmp_path / "out")
        en_group = course.dir_groups[1]
        op = await en_group.get_processing_operation(languages=frozenset({"de"}))
        from clm.core.operation import NoOperation

        assert isinstance(op, NoOperation)


class TestManifest:
    async def test_each_group_enumerates_only_its_language(self, course_root, tmp_path):
        """Without the filter, the ``de`` group's on-disk walk also visits
        ``Course-en/Code/Breakout`` (same ``<name>``) and claims the EN
        project's files — with the DE group's section/topic ownership, which
        is wrong as soon as the pair is topic-scoped to different topics."""
        from clm.core.provenance_manifest import enumerate_expected_outputs

        out = tmp_path / "out"
        course = Course.from_spec(_spec(), course_root, out)
        await _copy_dir_groups(course)
        all_groups = list(course.dir_groups)
        expected_langs = [{"de"}, {"en"}, {"de", "en"}]

        for dir_group, langs in zip(all_groups, expected_langs, strict=True):
            course.dir_groups = [dir_group]
            for target in course.output_targets:
                records = [
                    (path, record)
                    for path, record in enumerate_expected_outputs(course, target)
                    if record["format"] == "dir-group"
                ]
                assert records, dir_group.name
                assert {record["language"] for _, record in records} == langs
                for path, record in records:
                    course_dir = "Kurs-de" if record["language"] == "de" else "Course-en"
                    assert course_dir in path.parts, (path, record)
