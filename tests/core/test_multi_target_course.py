"""Integration tests for multi-target course processing."""

import asyncio
import io
from pathlib import Path

import pytest
from attrs import evolve

from clm.core.course import Course
from clm.core.course_spec import (
    CourseSpec,
    GitHubSpec,
    JupyterLiteConfig,
    OutputTargetSpec,
)
from clm.core.output_target import (
    ALL_KINDS,
    ALL_LANGUAGES,
    DEFAULT_FORMATS,
    OutputTarget,
)
from clm.core.utils.text_utils import Text


class TestCourseFromSpecWithTargets:
    """Tests for Course.from_spec() with output targets."""

    @pytest.fixture
    def course_root(self, tmp_path):
        """Create a course root directory with required structure."""
        # Create the slides directory (required by Course._build_topic_map)
        slides_dir = tmp_path / "slides"
        slides_dir.mkdir()
        return tmp_path

    @pytest.fixture
    def course_spec_with_targets(self):
        """Create a CourseSpec with multiple output targets."""
        return CourseSpec(
            name={"de": "Test Kurs", "en": "Test Course"},
            prog_lang="python",
            description={"de": "Beschreibung", "en": "Description"},
            certificate={"de": "Zertifikat", "en": "Certificate"},
            sections=[],
            github=GitHubSpec(
                project_slug="test-course",
                repository_base="https://github.com/test",
            ),
            output_targets=[
                OutputTargetSpec(
                    name="students",
                    path="./output/students",
                    kinds=["code-along"],
                    formats=["html", "notebook"],
                ),
                OutputTargetSpec(
                    name="solutions",
                    path="./output/solutions",
                    kinds=["completed"],
                    formats=["html", "notebook", "code"],
                ),
                OutputTargetSpec(
                    name="instructor",
                    path="./output/instructor",
                    kinds=["speaker"],
                    formats=["html", "notebook"],
                    languages=["en"],
                ),
            ],
        )

    @pytest.fixture
    def course_spec_no_targets(self):
        """Create a CourseSpec without output targets."""
        return CourseSpec(
            name={"de": "Test Kurs", "en": "Test Course"},
            prog_lang="python",
            description={"de": "Beschreibung", "en": "Description"},
            certificate={"de": "Zertifikat", "en": "Certificate"},
            sections=[],
            github=GitHubSpec(),
        )

    def test_from_spec_with_output_targets(self, course_spec_with_targets, course_root):
        """Test Course.from_spec creates OutputTarget objects from spec."""
        course = Course.from_spec(
            spec=course_spec_with_targets,
            course_root=course_root,
            output_root=None,  # Use spec targets
        )

        # Should have 3 targets
        assert len(course.output_targets) == 3

        # Check first target (students)
        students = course.output_targets[0]
        assert students.name == "students"
        assert students.kinds == frozenset({"code-along"})
        assert students.formats == frozenset({"html", "notebook"})
        assert students.languages == ALL_LANGUAGES  # Not specified = all

        # Check second target (solutions)
        solutions = course.output_targets[1]
        assert solutions.name == "solutions"
        assert solutions.kinds == frozenset({"completed"})
        assert "code" in solutions.formats

        # Check third target (instructor)
        instructor = course.output_targets[2]
        assert instructor.name == "instructor"
        assert instructor.languages == frozenset({"en"})

    def test_from_spec_cli_output_dir_reroots_spec_targets(
        self, course_spec_with_targets, course_root
    ):
        """``--output-dir DIR`` on a spec with ``<output-targets>``
        re-roots each target to ``<DIR>/<target.name>/`` (matching
        the per-target layout used by ``--snapshot`` and the regular
        spec-driven build). It does NOT collapse all targets into a
        single ``public/speaker`` tree — the old collapsed behavior
        silently dropped non-default targets like ``trainer``."""
        output_dir = course_root / "cli_output"

        course = Course.from_spec(
            spec=course_spec_with_targets,
            course_root=course_root,
            output_root=output_dir,  # CLI override, per-target re-root
        )

        # All three spec targets must be preserved with their filters.
        assert len(course.output_targets) == 3
        names = {t.name for t in course.output_targets}
        assert names == {"students", "solutions", "instructor"}

        by_name = {t.name: t for t in course.output_targets}
        assert by_name["students"].output_root == (output_dir / "students").resolve()
        assert by_name["solutions"].output_root == (output_dir / "solutions").resolve()
        assert by_name["instructor"].output_root == (output_dir / "instructor").resolve()

        # Per-target filters from the spec are preserved (not flattened
        # to the default-target ALL_KINDS / DEFAULT_FORMATS).
        assert by_name["students"].kinds == frozenset({"code-along"})
        assert by_name["solutions"].kinds == frozenset({"completed"})
        assert by_name["instructor"].languages == frozenset({"en"})

    def test_from_spec_cli_output_dir_no_spec_targets_uses_default_structure(
        self, course_spec_no_targets, course_root
    ):
        """``--output-dir DIR`` on a spec without ``<output-targets>`` re-roots
        the default shared/trainer/speaker structure under ``DIR/<tier>/`` (#383)."""
        output_dir = course_root / "cli_output"

        course = Course.from_spec(
            spec=course_spec_no_targets,
            course_root=course_root,
            output_root=output_dir,
        )

        assert [t.name for t in course.output_targets] == ["shared", "trainer", "speaker"]
        by_name = {t.name: t for t in course.output_targets}
        assert by_name["shared"].output_root == (output_dir / "shared").resolve()
        assert by_name["trainer"].output_root == (output_dir / "trainer").resolve()
        assert by_name["shared"].kinds == frozenset({"code-along", "completed"})
        assert by_name["trainer"].kinds == frozenset({"code-along", "completed", "trainer"})
        assert by_name["speaker"].kinds == frozenset({"recording"})
        # Default formats/languages still apply when a tier doesn't narrow them.
        assert by_name["shared"].formats == DEFAULT_FORMATS
        assert by_name["shared"].languages == ALL_LANGUAGES

    def test_from_spec_no_targets_uses_default_structure(self, course_spec_no_targets, course_root):
        """A spec without targets defaults to shared/trainer/speaker (#383)."""
        course = Course.from_spec(
            spec=course_spec_no_targets,
            course_root=course_root,
            output_root=None,
        )

        assert [t.name for t in course.output_targets] == ["shared", "trainer", "speaker"]
        by_name = {t.name: t for t in course.output_targets}
        # Each tier roots at its own ``output/<tier>`` under the course root.
        assert by_name["shared"].output_root == (course_root / "output" / "shared").resolve()
        assert by_name["speaker"].output_root == (course_root / "output" / "speaker").resolve()

    def test_from_spec_selected_targets_filter(self, course_spec_with_targets, course_root):
        """Test selecting specific targets."""
        course = Course.from_spec(
            spec=course_spec_with_targets,
            course_root=course_root,
            output_root=None,
            selected_targets=["students", "instructor"],
        )

        # Should only have 2 targets
        assert len(course.output_targets) == 2
        target_names = {t.name for t in course.output_targets}
        assert target_names == {"students", "instructor"}

    def test_from_spec_selected_targets_invalid_raises(self, course_spec_with_targets, course_root):
        """Test that selecting non-existent targets raises error."""
        with pytest.raises(ValueError) as exc_info:
            Course.from_spec(
                spec=course_spec_with_targets,
                course_root=course_root,
                output_root=None,
                selected_targets=["nonexistent"],
            )

        assert "No matching targets found" in str(exc_info.value)

    def test_from_spec_cli_filters_applied_to_targets(self, course_spec_with_targets, course_root):
        """Test CLI language/kind filters are applied to all targets."""
        course = Course.from_spec(
            spec=course_spec_with_targets,
            course_root=course_root,
            output_root=None,
            output_languages=["en"],
            output_kinds=["completed"],
        )

        # All targets should have filters applied
        for target in course.output_targets:
            # Language filter intersects with target's languages
            assert "de" not in target.languages

            # Kind filter intersects with target's kinds
            # Students originally has only code-along, so intersection is empty
            # Solutions has completed
            # Instructor has speaker, so intersection is empty


class TestCourseImplicitExecutions:
    """Tests for implicit execution dependency resolution."""

    @pytest.fixture
    def course_root(self, tmp_path):
        """Create a course root directory with required structure."""
        slides_dir = tmp_path / "slides"
        slides_dir.mkdir()
        return tmp_path

    def test_implicit_executions_for_completed_html_only(self, course_root):
        """Test implicit recording HTML execution when only completed HTML requested."""
        spec = CourseSpec(
            name={"de": "Test", "en": "Test"},
            prog_lang="python",
            description={"de": "Desc", "en": "Desc"},
            certificate={"de": "Cert", "en": "Cert"},
            sections=[],
            github=GitHubSpec(),
            output_targets=[
                # Only completed HTML - needs implicit recording HTML for cache
                OutputTargetSpec(
                    name="solutions",
                    path="./solutions",
                    kinds=["completed"],
                    formats=["html"],
                    languages=["en"],
                ),
            ],
        )

        course = Course.from_spec(spec, course_root, output_root=None)

        # Should have implicit execution for recording HTML (the cache producer)
        assert ("en", "html", "recording") in course.implicit_executions

    def test_no_implicit_when_recording_included(self, course_root):
        """Test no implicit executions when recording is already included."""
        spec = CourseSpec(
            name={"de": "Test", "en": "Test"},
            prog_lang="python",
            description={"de": "Desc", "en": "Desc"},
            certificate={"de": "Cert", "en": "Cert"},
            sections=[],
            github=GitHubSpec(),
            output_targets=[
                OutputTargetSpec(
                    name="all",
                    path="./all",
                    kinds=["completed", "recording"],
                    formats=["html"],
                ),
            ],
        )

        course = Course.from_spec(spec, course_root, output_root=None)

        # Should have no implicit executions
        assert course.implicit_executions == set()


class TestCourseXMLParsing:
    """Tests for parsing course spec XML with output targets."""

    def test_parse_xml_with_output_targets(self):
        """Test parsing course spec XML with output-targets element."""
        xml_str = """
        <course>
            <name>
                <de>Python Programmierung</de>
                <en>Python Programming</en>
            </name>
            <prog-lang>python</prog-lang>
            <description><de>Desc</de><en>Desc</en></description>
            <certificate><de>Cert</de><en>Cert</en></certificate>
            <github><de>repo</de><en>repo</en></github>
            <sections></sections>
            <output-targets>
                <output-target name="student-immediate">
                    <path>./output/students</path>
                    <kinds>
                        <kind>code-along</kind>
                    </kinds>
                </output-target>
                <output-target name="student-solutions">
                    <path>./output/solutions</path>
                    <kinds>
                        <kind>completed</kind>
                    </kinds>
                </output-target>
                <output-target name="instructor">
                    <path>./output/instructor</path>
                    <kinds>
                        <kind>recording</kind>
                    </kinds>
                </output-target>
            </output-targets>
        </course>
        """
        spec = CourseSpec.from_file(io.StringIO(xml_str))

        assert len(spec.output_targets) == 3
        assert spec.output_targets[0].name == "student-immediate"
        assert spec.output_targets[0].kinds == ["code-along"]
        assert spec.output_targets[1].name == "student-solutions"
        assert spec.output_targets[1].kinds == ["completed"]
        assert spec.output_targets[2].name == "instructor"
        assert spec.output_targets[2].kinds == ["recording"]

    def test_parse_xml_with_language_specific_targets(self):
        """Test parsing targets with language filters."""
        xml_str = """
        <course>
            <name>
                <de>Kurs</de>
                <en>Course</en>
            </name>
            <prog-lang>python</prog-lang>
            <description><de>Desc</de><en>Desc</en></description>
            <certificate><de>Cert</de><en>Cert</en></certificate>
            <github><de>repo</de><en>repo</en></github>
            <sections></sections>
            <output-targets>
                <output-target name="de-materials">
                    <path>./output/de</path>
                    <languages>
                        <language>de</language>
                    </languages>
                </output-target>
                <output-target name="en-materials">
                    <path>./output/en</path>
                    <languages>
                        <language>en</language>
                    </languages>
                </output-target>
            </output-targets>
        </course>
        """
        spec = CourseSpec.from_file(io.StringIO(xml_str))

        assert len(spec.output_targets) == 2
        assert spec.output_targets[0].languages == ["de"]
        assert spec.output_targets[1].languages == ["en"]


class TestDirGroupMultiTarget:
    """Tests for directory group processing with multiple targets."""

    @pytest.fixture
    def dir_group_spec(self):
        """Create a CourseSpec with dir groups and multiple targets."""
        return CourseSpec(
            name={"de": "Test Kurs", "en": "Test Course"},
            prog_lang="python",
            description={"de": "Desc", "en": "Desc"},
            certificate={"de": "Cert", "en": "Cert"},
            sections=[],
            github=GitHubSpec(),
            output_targets=[
                OutputTargetSpec(
                    name="public-only",
                    path="./output/public",
                    kinds=["code-along", "completed"],
                ),
                OutputTargetSpec(
                    name="speaker-only",
                    path="./output/speaker",
                    kinds=["recording"],
                ),
                OutputTargetSpec(
                    name="all-kinds",
                    path="./output/all",
                    # Default: all kinds
                ),
            ],
        )

    @pytest.fixture
    def course_root(self, tmp_path):
        """Create a course root directory with slides folder."""
        slides_dir = tmp_path / "slides"
        slides_dir.mkdir()
        return tmp_path

    def test_target_with_only_public_kinds_generates_public_is_speaker_option(
        self, dir_group_spec, course_root
    ):
        """Test that targets with only code-along/completed kinds get is_speaker=False."""
        course = Course.from_spec(
            spec=dir_group_spec,
            course_root=course_root,
            output_root=None,
        )

        # Find the public-only target
        public_target = next(t for t in course.output_targets if t.name == "public-only")

        # Check it only has public kinds
        assert public_target.kinds == frozenset({"code-along", "completed"})
        assert not (public_target.kinds & {"trainer", "recording", "speaker"})

        # Verify the logic that would be used in process_dir_group_for_targets
        has_public = bool(public_target.kinds & {"code-along", "completed"})
        has_speaker = bool(public_target.kinds & {"trainer", "recording", "speaker"})
        assert has_public is True
        assert has_speaker is False

    def test_target_with_only_recording_kind_generates_speaker_is_speaker_option(
        self, dir_group_spec, course_root
    ):
        """Targets with only ``recording`` kind get is_speaker=True (private toplevel)."""
        course = Course.from_spec(
            spec=dir_group_spec,
            course_root=course_root,
            output_root=None,
        )

        # Find the speaker-only target (now configured with recording kind).
        speaker_target = next(t for t in course.output_targets if t.name == "speaker-only")

        # Check it only has the recording kind
        assert speaker_target.kinds == frozenset({"recording"})
        assert "code-along" not in speaker_target.kinds
        assert "completed" not in speaker_target.kinds

        # Verify the logic
        has_public = bool(speaker_target.kinds & {"code-along", "completed"})
        has_speaker = bool(speaker_target.kinds & {"trainer", "recording", "speaker"})
        assert has_public is False
        assert has_speaker is True

    def test_target_with_all_kinds_generates_both_is_speaker_options(
        self, dir_group_spec, course_root
    ):
        """Test that targets with all kinds get both is_speaker options."""
        course = Course.from_spec(
            spec=dir_group_spec,
            course_root=course_root,
            output_root=None,
        )

        # Find the all-kinds target
        all_target = next(t for t in course.output_targets if t.name == "all-kinds")

        # Check it has all kinds
        assert all_target.kinds == ALL_KINDS

        # Verify the logic
        has_public = bool(all_target.kinds & {"code-along", "completed"})
        has_speaker = bool(all_target.kinds & {"trainer", "recording", "speaker"})
        assert has_public is True
        assert has_speaker is True

    def test_cli_language_filter_applies_to_dir_groups(self, dir_group_spec, course_root):
        """Test that --language CLI filter applies to dir group operations."""
        course = Course.from_spec(
            spec=dir_group_spec,
            course_root=course_root,
            output_root=None,
            output_languages=["de"],  # Only German
        )

        # All targets should have only German language
        for target in course.output_targets:
            assert target.languages == frozenset({"de"})
            assert "en" not in target.languages


class _RecordingBackend:
    """Minimal ``Backend`` stub that records what got submitted.

    ``process_jupyterlite_for_targets`` runs an outer ``TaskGroup`` that
    contains two tasks: one submits per-``(target, language)`` jobs, the
    other awaits ``backend.wait_for_completion(all_submitted)``. We only
    need to drain the first; ``wait_for_completion`` just has to return
    once the submitter sets ``all_submitted``.
    """

    def __init__(self) -> None:
        self.operations: list[object] = []

    async def wait_for_completion(self, all_submitted=None) -> bool:
        if all_submitted is not None:
            await all_submitted.wait()
        return True


class TestCountJupyterLiteOperations:
    """Tests for ``Course.count_jupyterlite_operations``."""

    @pytest.fixture
    def course_root(self, tmp_path):
        (tmp_path / "slides").mkdir()
        return tmp_path

    def _course_with_targets(self, course_root, targets, course_jupyterlite=None):
        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="python",
            description=Text(de="D", en="D"),
            certificate=Text(de="C", en="C"),
            sections=[],
            github=GitHubSpec(),
            output_targets=targets,
            jupyterlite=course_jupyterlite,
        )
        return Course.from_spec(spec, course_root, output_root=None)

    def test_count_zero_when_no_target_opts_in(self, course_root):
        course = self._course_with_targets(
            course_root,
            [
                OutputTargetSpec(
                    name="students",
                    path="./students",
                    kinds=["code-along"],
                    formats=["html", "notebook"],
                ),
            ],
        )
        assert course.count_jupyterlite_operations() == 0

    def test_count_one_per_language_on_course_level_config(self, course_root):
        course = self._course_with_targets(
            course_root,
            [
                OutputTargetSpec(
                    name="playground",
                    path="./playground",
                    kinds=["completed"],
                    formats=["notebook", "jupyterlite"],
                    # languages unspecified = both de + en = 2 jobs
                ),
            ],
            course_jupyterlite=JupyterLiteConfig(kernel="pyodide"),
        )
        assert course.count_jupyterlite_operations() == 2

    def test_count_sums_across_multiple_opted_in_targets(self, course_root):
        course = self._course_with_targets(
            course_root,
            [
                OutputTargetSpec(
                    name="en-playground",
                    path="./en",
                    kinds=["completed"],
                    formats=["notebook", "jupyterlite"],
                    languages=["en"],
                    jupyterlite=JupyterLiteConfig(kernel="pyodide"),
                ),
                OutputTargetSpec(
                    name="bilingual",
                    path="./bi",
                    kinds=["completed"],
                    formats=["notebook", "jupyterlite"],
                    jupyterlite=JupyterLiteConfig(kernel="xeus-python"),
                ),
                OutputTargetSpec(
                    name="not-jl",
                    path="./n",
                    kinds=["code-along"],
                    formats=["html"],
                ),
            ],
        )
        # 1 (en-only) + 2 (both langs) + 0 (no jl format) = 3
        assert course.count_jupyterlite_operations() == 3


class TestProcessJupyterLiteForTargets:
    """Tests for ``Course.process_jupyterlite_for_targets``.

    We monkeypatch ``BuildJupyterLiteSiteOperation.execute`` so the test
    never has to produce a real notebook tree on disk — the operation
    otherwise ``rglob(*.ipynb)`` inside ``collect_notebook_trees``.
    """

    @pytest.fixture
    def course_root(self, tmp_path):
        (tmp_path / "slides").mkdir()
        return tmp_path

    @pytest.fixture
    def captured_ops(self, monkeypatch):
        captured: list[object] = []

        async def fake_execute(self, backend, *args, **kwargs):
            captured.append(self)

        monkeypatch.setattr(
            "clm.core.operations.build_jupyterlite_site.BuildJupyterLiteSiteOperation.execute",
            fake_execute,
            raising=True,
        )
        return captured

    def _course(self, course_root, targets, course_jupyterlite=None):
        spec = CourseSpec(
            name=Text(de="T", en="T"),
            prog_lang="python",
            description=Text(de="D", en="D"),
            certificate=Text(de="C", en="C"),
            sections=[],
            github=GitHubSpec(),
            output_targets=targets,
            jupyterlite=course_jupyterlite,
        )
        return Course.from_spec(spec, course_root, output_root=None)

    def test_no_ops_when_no_target_opts_in(self, course_root, captured_ops):
        course = self._course(
            course_root,
            [
                OutputTargetSpec(
                    name="students",
                    path="./students",
                    kinds=["code-along"],
                    formats=["html"],
                ),
            ],
        )
        backend = _RecordingBackend()
        asyncio.run(course.process_jupyterlite_for_targets(backend))
        assert captured_ops == []

    def test_one_op_per_target_language_pair(self, course_root, captured_ops):
        course = self._course(
            course_root,
            [
                OutputTargetSpec(
                    name="playground",
                    path="./playground",
                    kinds=["completed", "code-along"],
                    formats=["notebook", "jupyterlite"],
                    # Both languages de + en → 2 ops
                ),
            ],
            course_jupyterlite=JupyterLiteConfig(kernel="pyodide"),
        )
        backend = _RecordingBackend()
        asyncio.run(course.process_jupyterlite_for_targets(backend))

        assert len(captured_ops) == 2
        languages = {op.language for op in captured_ops}  # type: ignore[attr-defined]
        assert languages == {"de", "en"}
        # kinds list merges all kinds from the target, sorted.
        for op in captured_ops:
            assert op.kinds == ["code-along", "completed"]  # type: ignore[attr-defined]
            # notebook_trees contains one entry per kind.
            assert set(op.notebook_trees.keys()) == {  # type: ignore[attr-defined]
                "code-along",
                "completed",
            }
            assert op.target_name == "playground"  # type: ignore[attr-defined]
            assert op.config.kernel == "pyodide"  # type: ignore[attr-defined]

    def test_target_level_config_overrides_course_level(self, course_root, captured_ops):
        course = self._course(
            course_root,
            [
                OutputTargetSpec(
                    name="xeus-only",
                    path="./x",
                    kinds=["completed"],
                    formats=["jupyterlite"],
                    languages=["en"],
                    jupyterlite=JupyterLiteConfig(kernel="xeus-python"),
                ),
            ],
            # Course level says pyodide — target override must win.
            course_jupyterlite=JupyterLiteConfig(kernel="pyodide"),
        )
        backend = _RecordingBackend()
        asyncio.run(course.process_jupyterlite_for_targets(backend))

        assert len(captured_ops) == 1
        assert captured_ops[0].config.kernel == "xeus-python"  # type: ignore[attr-defined]

    def test_missing_config_is_skipped_with_warning(self, course_root, captured_ops, caplog):
        """Target opts into format='jupyterlite' but the runtime target has
        no ``<jupyterlite>`` config resolved.

        CourseSpec validation normally rejects this, so we bypass by
        assembling the ``Course`` and overwriting the output target's
        ``course_jupyterlite`` attribute manually.
        """
        spec = CourseSpec(
            name=Text(de="T", en="T"),
            prog_lang="python",
            description=Text(de="D", en="D"),
            certificate=Text(de="C", en="C"),
            sections=[],
            github=GitHubSpec(),
            # No output targets so CourseSpec validation doesn't reject.
        )
        course = Course.from_spec(spec, course_root, output_root=None)
        # Build a single target directly and attach it — this bypasses
        # CourseSpec's opt-in validation. Both jupyterlite attrs are
        # None so ``effective_jupyterlite_config`` returns None.
        bad_target = OutputTarget(
            name="broken",
            output_root=course_root / "broken",
            kinds=frozenset({"completed"}),
            formats=frozenset({"jupyterlite"}),
            languages=frozenset({"en"}),
            is_explicit=True,
            jupyterlite=None,
            course_jupyterlite=None,
        )
        course.output_targets = [bad_target]

        backend = _RecordingBackend()
        import logging

        with caplog.at_level(logging.WARNING, logger="clm.core.course"):
            asyncio.run(course.process_jupyterlite_for_targets(backend))

        assert captured_ops == []
        assert any("no effective <jupyterlite> config" in rec.message for rec in caplog.records)

    def test_returns_early_when_no_jupyterlite_targets(self, course_root, captured_ops):
        """Early-return branch: no target lists 'jupyterlite' at all."""
        course = self._course(
            course_root,
            [
                OutputTargetSpec(
                    name="html-only",
                    path="./h",
                    kinds=["code-along"],
                    formats=["html"],
                ),
            ],
        )
        backend = _RecordingBackend()
        # Should return immediately without constructing the outer TaskGroup.
        asyncio.run(course.process_jupyterlite_for_targets(backend))
        assert captured_ops == []

    def test_output_dir_uses_parent_of_jupyterlite_spec(self, course_root, captured_ops):
        """The per-op ``output_dir`` should be the parent of the jupyterlite
        OutputSpec output_dir, so the site lands at the target's jupyterlite
        directory level (not inside a per-kind subfolder).
        """
        course = self._course(
            course_root,
            [
                OutputTargetSpec(
                    name="p",
                    path="./p",
                    kinds=["completed"],
                    formats=["jupyterlite", "notebook"],
                    languages=["en"],
                ),
            ],
            course_jupyterlite=JupyterLiteConfig(kernel="pyodide"),
        )
        backend = _RecordingBackend()
        asyncio.run(course.process_jupyterlite_for_targets(backend))
        assert len(captured_ops) == 1
        op = captured_ops[0]
        # output_dir is the parent of the jupyterlite spec's output_dir
        # (i.e. not inside a per-kind subfolder).
        assert "completed" not in op.output_dir.parts  # type: ignore[attr-defined]


class TestCourseWorkspaceRoot:
    """Tests for ``Course.workspace_root`` — the Docker /workspace mount base.

    Regression coverage for issue #384: a multi-target Docker build mounted
    only the first target's root, so writes under other targets failed path
    conversion. ``workspace_root`` must cover *every* target root.
    """

    @pytest.fixture
    def course_root(self, tmp_path):
        (tmp_path / "slides").mkdir()
        return tmp_path

    def _course(self, course_root, targets, *, output_root=None):
        spec = CourseSpec(
            name=Text(de="T", en="T"),
            prog_lang="python",
            description=Text(de="D", en="D"),
            certificate=Text(de="C", en="C"),
            sections=[],
            github=GitHubSpec(),
            output_targets=targets,
        )
        return Course.from_spec(spec, course_root, output_root=output_root)

    def test_explicit_sibling_targets_use_common_ancestor(self, course_root):
        """shared/trainer/speaker under ``output/`` → mount ``output/`` (#384).

        ``output_root`` is the legacy primary (first target = ``output/shared``)
        which does NOT contain the other targets — the bug. ``workspace_root``
        must be their common parent so the Docker mount reaches all of them.
        """
        course = self._course(
            course_root,
            [
                OutputTargetSpec(name="shared", path="./output/shared", kinds=["code-along"]),
                OutputTargetSpec(name="trainer", path="./output/trainer", kinds=["completed"]),
                OutputTargetSpec(name="speaker", path="./output/speaker", kinds=["speaker"]),
            ],
        )

        # Precondition: the legacy primary is just the first target's root.
        assert course.output_root == (course_root / "output" / "shared").resolve()

        # The fix: cover all three targets.
        assert course.workspace_root == (course_root / "output").resolve()
        for target in course.output_targets:
            # Every target root is reachable from the mounted workspace.
            target.output_root.relative_to(course.workspace_root)

    def test_single_target_workspace_is_sole_root(self, course_root):
        """One target → ``workspace_root`` equals that root (no behavior change)."""
        course = self._course(
            course_root,
            [OutputTargetSpec(name="only", path="./output/only", kinds=["completed"])],
        )
        assert course.workspace_root == course.output_root
        assert course.workspace_root == (course_root / "output" / "only").resolve()

    def test_default_structure_workspace_is_umbrella_output(self, course_root):
        """No explicit targets → default shared/trainer/speaker under ``output/``.

        Here ``output_root`` is already the umbrella ``output/`` so the build
        was never broken, and ``workspace_root`` agrees with it.
        """
        spec = CourseSpec(
            name=Text(de="T", en="T"),
            prog_lang="python",
            description=Text(de="D", en="D"),
            certificate=Text(de="C", en="C"),
            sections=[],
            github=GitHubSpec(),
        )
        course = Course.from_spec(spec, course_root, output_root=None)
        assert [t.name for t in course.output_targets] == ["shared", "trainer", "speaker"]
        assert course.workspace_root == (course_root / "output").resolve()
        assert course.workspace_root == course.output_root.resolve()

    def test_cli_output_dir_targets_share_override_root(self, course_root):
        """``--output-dir`` re-roots every target under ``DIR/<name>`` → common
        ancestor is ``DIR`` (this case already worked; guard against regression)."""
        output_dir = course_root / "cli_out"
        course = self._course(
            course_root,
            [
                OutputTargetSpec(name="a", path="./output/a", kinds=["completed"]),
                OutputTargetSpec(name="b", path="./output/b", kinds=["speaker"]),
            ],
            output_root=output_dir,
        )
        assert course.workspace_root == output_dir.resolve()

    def test_targets_with_no_common_parent_raise_actionable_error(self, course_root, monkeypatch):
        """Targets that share only a drive/filesystem root must fail fast with
        an actionable message, not silently mount a whole volume (#384)."""
        # Two targets whose only common ancestor is the filesystem root.
        course = self._course(
            course_root,
            [
                OutputTargetSpec(name="a", path="./output/a", kinds=["completed"]),
                OutputTargetSpec(name="b", path="./output/b", kinds=["speaker"]),
            ],
        )
        # Force the resolved roots to be drive-root children so the common
        # ancestor is the root itself, exercising the volume-mount guard.
        root = Path(course_root.anchor or "/")
        course.output_targets = [
            evolve(course.output_targets[0], output_root=(root / "alpha")),
            evolve(course.output_targets[1], output_root=(root / "beta")),
        ]

        with pytest.raises(ValueError, match="whole drive|common parent|--targets"):
            _ = course.workspace_root
