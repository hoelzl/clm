from pathlib import Path
from typing import cast

import pytest

from clm.core.course_file import CourseFile
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.course_spec import TopicSpec
from clm.core.operations.process_notebook import ProcessNotebookOperation
from clm.core.section import Section
from clm.core.topic import Topic
from clm.core.utils.text_utils import Text
from clm.infrastructure.backends.dummy_backend import DummyBackend
from clm.infrastructure.operation import Concurrently
from clm.infrastructure.utils.path_utils import output_specs

NOTEBOOK_FILE = "slides_some_topic_from_test_1.py"


def test_file_from_path_notebook(course_1, section_1, topic_1):
    file_path = topic_1.path / NOTEBOOK_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    assert isinstance(unit, NotebookFile)
    assert unit.path == file_path
    assert unit.topic == topic_1
    assert unit.section == section_1
    assert unit.relative_path == Path(NOTEBOOK_FILE)
    assert unit.generated_outputs == set()
    assert unit.source_outputs == frozenset()
    assert unit.prog_lang == "python"


async def test_file_from_path_notebook_operations(course_1, topic_1):
    file_path = topic_1.path / NOTEBOOK_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    process_op = await unit.get_processing_operation(course_1.output_root)
    assert isinstance(process_op, Concurrently)

    ops = cast(list[ProcessNotebookOperation], list(process_op.operations))
    op = ops[0]
    assert op.output_file == course_1.output_root / (
        "public/Mein Kurs-de/Folien/Html/Code-Along/Woche 1/00 Folien von Test 1.html"
    )

    assert len(ops) == len(list(output_specs(course_1, course_1.output_root)))
    assert all(isinstance(op, ProcessNotebookOperation) for op in ops)
    assert all(op.input_file == unit for op in ops)
    assert all(op.output_file.stem == "00 Folien von Test 1" for op in ops if op.language == "de")
    assert all(
        op.output_file.stem == "00 Some Topic from Test 1" for op in ops if op.language == "en"
    )


@pytest.fixture
def notebook_file_and_output_dir(course_1, topic_1):
    file_path = topic_1.path / NOTEBOOK_FILE
    notebook_file = course_1.find_file(file_path)
    output_dir = course_1.output_root
    return notebook_file, output_dir


async def test_notebook_file_executes_calls_backend(notebook_file_and_output_dir, mocker):
    spy = mocker.spy(DummyBackend, "execute_operation")
    backend = DummyBackend()
    notebook_file, output_dir = notebook_file_and_output_dir

    unit = await notebook_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    # The backend is called once for each output spec
    assert spy.call_count == len(list(output_specs(notebook_file.course, Path())))


async def test_notebook_file_source_outputs(notebook_file_and_output_dir):
    backend = DummyBackend()
    notebook_file, output_dir = notebook_file_and_output_dir

    unit = await notebook_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert notebook_file.source_outputs == frozenset()


async def test_notebook_file_generated_outputs(notebook_file_and_output_dir):
    backend = DummyBackend()
    notebook_file, output_dir = notebook_file_and_output_dir

    unit = await notebook_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    public_de = "public/Mein Kurs-de/Folien"
    public_en = "public/My Course-en/Slides"
    speaker_de = "speaker/Mein Kurs-de/Folien"
    speaker_en = "speaker/My Course-en/Slides"

    name_de = "01 Folien von Test 1"
    name_en = "01 Some Topic from Test 1"

    assert notebook_file.generated_outputs == {
        # Public/DE
        output_dir / f"{public_de}/Html/Code-Along/Woche 1/{name_de}.html",
        output_dir / f"{public_de}/Html/Completed/Woche 1/{name_de}.html",
        output_dir / f"{public_de}/Html/Partial/Woche 1/{name_de}.html",
        output_dir / f"{public_de}/Notebooks/Code-Along/Woche 1/{name_de}.ipynb",
        output_dir / f"{public_de}/Notebooks/Completed/Woche 1/{name_de}.ipynb",
        output_dir / f"{public_de}/Notebooks/Partial/Woche 1/{name_de}.ipynb",
        output_dir / f"{public_de}/Python/Code-Along/Woche 1/{name_de}.py",
        output_dir / f"{public_de}/Python/Completed/Woche 1/{name_de}.py",
        output_dir / f"{public_de}/Python/Partial/Woche 1/{name_de}.py",
        # Public/EN
        output_dir / f"{public_en}/Html/Code-Along/Week 1/{name_en}.html",
        output_dir / f"{public_en}/Html/Completed/Week 1/{name_en}.html",
        output_dir / f"{public_en}/Html/Partial/Week 1/{name_en}.html",
        output_dir / f"{public_en}/Notebooks/Code-Along/Week 1/{name_en}.ipynb",
        output_dir / f"{public_en}/Notebooks/Completed/Week 1/{name_en}.ipynb",
        output_dir / f"{public_en}/Notebooks/Partial/Week 1/{name_en}.ipynb",
        output_dir / f"{public_en}/Python/Code-Along/Week 1/{name_en}.py",
        output_dir / f"{public_en}/Python/Completed/Week 1/{name_en}.py",
        output_dir / f"{public_en}/Python/Partial/Week 1/{name_en}.py",
        # Speaker (private toplevel)/DE — both private kinds get their subdir
        output_dir / f"{speaker_de}/Html/Trainer/Woche 1/{name_de}.html",
        output_dir / f"{speaker_de}/Html/Recording/Woche 1/{name_de}.html",
        output_dir / f"{speaker_de}/Notebooks/Trainer/Woche 1/{name_de}.ipynb",
        output_dir / f"{speaker_de}/Notebooks/Recording/Woche 1/{name_de}.ipynb",
        output_dir / f"{speaker_de}/Python/Trainer/Woche 1/{name_de}.py",
        output_dir / f"{speaker_de}/Python/Recording/Woche 1/{name_de}.py",
        # Speaker (private toplevel)/EN — both private kinds get their subdir
        output_dir / f"{speaker_en}/Html/Trainer/Week 1/{name_en}.html",
        output_dir / f"{speaker_en}/Html/Recording/Week 1/{name_en}.html",
        output_dir / f"{speaker_en}/Notebooks/Trainer/Week 1/{name_en}.ipynb",
        output_dir / f"{speaker_en}/Notebooks/Recording/Week 1/{name_en}.ipynb",
        output_dir / f"{speaker_en}/Python/Trainer/Week 1/{name_en}.py",
        output_dir / f"{speaker_en}/Python/Recording/Week 1/{name_en}.py",
    }


# --- Tests for prog_lang override chain ---


class TestProgLangOverrideChain:
    """Test the prog_lang priority: topic attr > course prog_lang > extension default."""

    def test_py_file_uses_extension_mapping(self, course_1, topic_1):
        """A .py file always resolves to 'python' from extension."""
        file_path = topic_1.path / NOTEBOOK_FILE
        nb = CourseFile.from_path(course_1, file_path, topic_1)
        assert nb.prog_lang == "python"

    def test_md_file_defaults_to_python(self, course_1, tmp_path):
        """A .md file with no course prog_lang defaults to 'python'."""
        # course_1 has prog_lang="python", so create a minimal course with empty prog_lang
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t")
        md_file = tmp_path / "slides_test.md"
        md_file.write_text("# Title\nSome content\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)

        nb = CourseFile.from_path(course, md_file, topic)
        assert isinstance(nb, NotebookFile)
        assert nb.prog_lang == "python"

    def test_md_file_uses_course_prog_lang(self, course_1, tmp_path):
        """A .md file picks up the course-level prog_lang."""
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="cpp",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t")
        md_file = tmp_path / "slides_test.md"
        md_file.write_text("# Title\nSome content\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)

        nb = CourseFile.from_path(course, md_file, topic)
        assert isinstance(nb, NotebookFile)
        assert nb.prog_lang == "cpp"

    def test_topic_prog_lang_overrides_course(self, course_1, tmp_path):
        """Topic-level prog_lang attribute overrides course-level."""
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="python",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t", prog_lang="java")
        md_file = tmp_path / "slides_test.md"
        md_file.write_text("# Title\nSome content\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)

        nb = CourseFile.from_path(course, md_file, topic)
        assert isinstance(nb, NotebookFile)
        assert nb.prog_lang == "java"

    def test_topic_prog_lang_overrides_extension_for_py(self, course_1, tmp_path):
        """Topic-level prog_lang even overrides extension-based detection for .py files."""
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="python",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t", prog_lang="typescript")
        py_file = tmp_path / "slides_test.py"
        py_file.write_text("# %% [markdown]\n# Title\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)

        nb = CourseFile.from_path(course, py_file, topic)
        assert isinstance(nb, NotebookFile)
        assert nb.prog_lang == "typescript"


# --- Tests for HTTP replay cassette resolution ---


class TestCassetteResolution:
    """NotebookFile.cassette_path / cassette_relative_name."""

    def _make_nb_file(
        self, course_1, tmp_path: Path, *, with_cassette: bool = False, nested: bool = False
    ) -> NotebookFile:
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="python",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t")
        py_file = tmp_path / "slides_replay.py"
        py_file.write_text("# %% [markdown]\n# Title\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)
        if with_cassette:
            if nested:
                (tmp_path / "_cassettes").mkdir()
                (tmp_path / "_cassettes" / "slides_replay.http-cassette.yaml").write_text(
                    "interactions: []\n", encoding="utf-8"
                )
            else:
                (tmp_path / "slides_replay.http-cassette.yaml").write_text(
                    "interactions: []\n", encoding="utf-8"
                )
        nb = cast(NotebookFile, CourseFile.from_path(course, py_file, topic))
        return nb

    def test_cassette_path_none_when_missing(self, course_1, tmp_path):
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=False)
        assert nb.cassette_path is None
        assert nb.cassette_relative_name is None

    def test_cassette_path_resolves_sibling(self, course_1, tmp_path):
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=True)
        assert nb.cassette_path == tmp_path / "slides_replay.http-cassette.yaml"
        assert nb.cassette_relative_name == "slides_replay.http-cassette.yaml"

    def test_cassette_path_prefers_nested_cassettes_dir(self, course_1, tmp_path):
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=True, nested=True)
        assert nb.cassette_path == tmp_path / "_cassettes" / "slides_replay.http-cassette.yaml"
        assert nb.cassette_relative_name == "_cassettes/slides_replay.http-cassette.yaml"

    def test_cassette_path_picks_nested_when_both_exist(self, course_1, tmp_path):
        # Create both layouts; nested should win.
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=True, nested=True)
        (tmp_path / "slides_replay.http-cassette.yaml").write_text("# sibling\n", encoding="utf-8")
        assert nb.cassette_relative_name == "_cassettes/slides_replay.http-cassette.yaml"

    def test_expected_cassette_path_defaults_to_sibling(self, course_1, tmp_path):
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=False)
        assert nb.expected_cassette_path == tmp_path / "slides_replay.http-cassette.yaml"
        assert nb.expected_cassette_relative_name == "slides_replay.http-cassette.yaml"

    def test_expected_cassette_path_uses_nested_when_dir_present(self, course_1, tmp_path):
        # Empty _cassettes/ directory is enough to switch layouts — no
        # cassette file needs to exist yet (this is the first-record path).
        (tmp_path / "_cassettes").mkdir()
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=False)
        assert (
            nb.expected_cassette_path
            == tmp_path / "_cassettes" / "slides_replay.http-cassette.yaml"
        )
        assert nb.expected_cassette_relative_name == "_cassettes/slides_replay.http-cassette.yaml"


class TestProcessNotebookOperationHttpReplay:
    """http_replay_mode plumbing through ProcessNotebookOperation."""

    def _make_operation(
        self, course_1, tmp_path: Path, *, mode: str | None, with_cassette: bool
    ) -> tuple[ProcessNotebookOperation, NotebookFile]:
        from base64 import b64decode

        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="python",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        course.http_replay_mode = mode
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t", http_replay=mode is not None)
        py_file = tmp_path / "slides_replay.py"
        py_file.write_text("# %% [markdown]\n# Title\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)
        if with_cassette:
            (tmp_path / "slides_replay.http-cassette.yaml").write_bytes(b"interactions: []\n")
        nb = cast(NotebookFile, CourseFile.from_path(course, py_file, topic))
        # Ensure NotebookFile carries http_replay like production _from_path does
        nb.http_replay = mode is not None
        op = ProcessNotebookOperation(
            input_file=nb,
            output_file=tmp_path / "out.html",
            language="en",
            format="html",
            kind="speaker",
            prog_lang="python",
            http_replay_mode=mode,
        )
        _ = b64decode  # silence unused in this helper
        return op, nb

    def test_other_files_includes_cassette_when_mode_set(self, course_1, tmp_path):
        op, _ = self._make_operation(course_1, tmp_path, mode="replay", with_cassette=True)
        from base64 import b64decode

        other = op.compute_other_files()
        assert "slides_replay.http-cassette.yaml" in other
        assert b64decode(other["slides_replay.http-cassette.yaml"]) == b"interactions: []\n"

    def test_other_files_excludes_cassette_when_mode_none(self, course_1, tmp_path):
        op, _ = self._make_operation(course_1, tmp_path, mode=None, with_cassette=True)
        other = op.compute_other_files()
        assert "slides_replay.http-cassette.yaml" not in other

    def test_other_files_excludes_cassette_when_mode_disabled(self, course_1, tmp_path):
        op, _ = self._make_operation(course_1, tmp_path, mode="disabled", with_cassette=True)
        other = op.compute_other_files()
        assert "slides_replay.http-cassette.yaml" not in other

    def test_other_files_no_cassette_when_file_missing(self, course_1, tmp_path):
        op, _ = self._make_operation(course_1, tmp_path, mode="replay", with_cassette=False)
        other = op.compute_other_files()
        assert "slides_replay.http-cassette.yaml" not in other

    def test_resolve_cassette_name_replay_requires_existing_file(self, course_1, tmp_path):
        # ``replay`` is strict: missing cassette → None (caller emits warning
        # / CI fails). It must NOT advertise an expected path.
        op_missing, _ = self._make_operation(course_1, tmp_path, mode="replay", with_cassette=False)
        assert op_missing._resolve_cassette_name() is None

        op_present, _ = self._make_operation(course_1, tmp_path, mode="replay", with_cassette=True)
        assert op_present._resolve_cassette_name() == "slides_replay.http-cassette.yaml"

    def test_resolve_cassette_name_record_modes_use_expected_when_missing(self, course_1, tmp_path):
        # ``once`` and ``refresh`` need a write target on first run, even
        # before any cassette exists. Otherwise the bootstrap is never
        # injected and recording can never bootstrap itself.
        for mode in ("once", "refresh"):
            op, _ = self._make_operation(course_1, tmp_path, mode=mode, with_cassette=False)
            assert op._resolve_cassette_name() == "slides_replay.http-cassette.yaml", (
                f"mode={mode!r} must return the expected cassette path on first run"
            )

    def test_resolve_cassette_name_record_modes_prefer_existing(self, course_1, tmp_path):
        # When a cassette already exists, record modes use the actual
        # location (which may live under ``_cassettes/``).
        for mode in ("once", "refresh"):
            op, _ = self._make_operation(course_1, tmp_path, mode=mode, with_cassette=True)
            assert op._resolve_cassette_name() == "slides_replay.http-cassette.yaml"

    def test_resolve_cassette_name_disabled_or_none(self, course_1, tmp_path):
        for mode in (None, "disabled"):
            op, _ = self._make_operation(course_1, tmp_path, mode=mode, with_cassette=True)
            assert op._resolve_cassette_name() is None


class TestBuildScopedCassetteSnapshot:
    """Stage 3 / Stage 4 must hash the same cassette bytes within a build.

    Recording HTML (Stage 3) and Completed/Trainer/Partial HTML (Stage 4)
    construct independent ``NotebookPayload`` instances. Both fold the
    cassette into ``execution_cache_hash`` when ``http_replay`` is active.
    If vcrpy in ``new-episodes``/``once``/``refresh`` mode appends new
    interactions during Stage 3 execution, the cassette file on disk
    changes before Stage 4 constructs its payloads — and the two stages
    end up computing different hashes, missing the ``executed_notebooks``
    cache and forcing redundant kernel re-execution.

    The fix is a build-scoped snapshot of cassette bytes, captured at the
    start of ``course.process_all()``, and consulted by
    :py:meth:`ProcessNotebookOperation.compute_other_files`.
    """

    def _build_course_with_replay_notebook(self, tmp_path: Path, mode: str | None = "new-episodes"):
        """Return ``(course, notebook_file, cassette_path)`` ready to test."""
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec
        from clm.core.output_target import OutputTarget

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="python",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(
            spec=spec,
            course_root=tmp_path,
            output_root=tmp_path,
            output_targets=[OutputTarget.default_target(tmp_path)],
        )
        course.http_replay_mode = mode

        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t", http_replay=True)
        py_file = tmp_path / "slides_replay.py"
        py_file.write_text("# %% [markdown]\n# Title\n", encoding="utf-8")
        cassette = tmp_path / "slides_replay.http-cassette.yaml"
        cassette.write_bytes(b"interactions:\n- request: stage3\n")

        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)
        topic.build_file_map()
        section.topics.append(topic)
        course.sections.append(section)

        # Find the NotebookFile inside the topic.
        nb = next(f for f in topic.files if isinstance(f, NotebookFile))
        # ``http_replay`` on the file mirrors what ``_from_path`` does in
        # production when the topic opts in. Without the topic-opt-in,
        # this is a defensive belt-and-braces in tests.
        nb.http_replay = True

        return course, nb, cassette

    def _make_op(self, nb: NotebookFile, tmp_path: Path, mode: str, kind: str):
        return ProcessNotebookOperation(
            input_file=nb,
            output_file=tmp_path / f"out_{kind}.html",
            language="en",
            format="html",
            kind=kind,
            prog_lang="python",
            http_replay_mode=mode,
        )

    async def test_snapshot_keeps_hash_stable_across_mid_build_cassette_mutation(self, tmp_path):
        """Stage 3 and Stage 4 must agree on the cassette hash even when the
        cassette file on disk changes between their payload constructions.

        Without the snapshot, mutating the cassette between the two
        payload builds would change ``execution_cache_hash`` — masking
        Stage 4's ability to reuse Stage 3's executed-notebook cache.
        """
        course, nb, cassette = self._build_course_with_replay_notebook(tmp_path)

        # Start the build: take the snapshot (mirrors what
        # ``course.process_all`` does at entry).
        course._snapshot_cassettes_for_build()

        # Stage 3: build Recording HTML payload at time T1.
        stage3_op = self._make_op(nb, tmp_path, "new-episodes", "recording")
        stage3_payload = await stage3_op.payload()
        stage3_hash = stage3_payload.execution_cache_hash()

        # Simulate vcrpy appending new interactions during Stage 3
        # execution, just like ``merge_staging_into_canonical`` rewrites
        # the canonical cassette in its ``finally`` block before Stage 4.
        cassette.write_bytes(b"interactions:\n- request: stage3\n- request: stage4-new\n")

        # Stage 4: build Completed HTML payload at time T2.
        stage4_op = self._make_op(nb, tmp_path, "new-episodes", "completed")
        stage4_payload = await stage4_op.payload()
        stage4_hash = stage4_payload.execution_cache_hash()

        # The invariant: same notebook + same build → same execution hash.
        # On ``master`` (no snapshot), these differ because Stage 4 hashes
        # the post-mutation cassette bytes. With the snapshot they agree.
        assert stage3_hash == stage4_hash, (
            "Stage 3 and Stage 4 hashed different cassette bytes within "
            "the same build invocation; executed_notebooks cache lookups "
            "in Stage 4 would miss and force redundant kernel execution. "
            "The build-scoped cassette snapshot is the fix."
        )

        # Both payloads must carry the pre-mutation bytes (b64-encoded).
        from base64 import b64decode

        cassette_key = "slides_replay.http-cassette.yaml"
        assert b64decode(stage3_payload.other_files[cassette_key]) == (
            b"interactions:\n- request: stage3\n"
        )
        assert b64decode(stage4_payload.other_files[cassette_key]) == (
            b"interactions:\n- request: stage3\n"
        )

    async def test_without_snapshot_hashes_diverge_when_cassette_mutates(self, tmp_path):
        """Sanity check: when the snapshot is empty the bug reproduces.

        This pins the failure mode the fix is preventing and would be the
        baseline behavior on ``master`` (where no snapshot exists at all).
        """
        course, nb, cassette = self._build_course_with_replay_notebook(tmp_path)

        # Deliberately do NOT call _snapshot_cassettes_for_build: this
        # mirrors the pre-fix behavior where ``compute_other_files`` reads
        # cassette bytes lazily at payload-construction time.
        assert course._build_cassette_snapshots == {}

        stage3_op = self._make_op(nb, tmp_path, "new-episodes", "recording")
        stage3_payload = await stage3_op.payload()

        cassette.write_bytes(b"interactions:\n- request: stage3\n- request: stage4-new\n")

        stage4_op = self._make_op(nb, tmp_path, "new-episodes", "completed")
        stage4_payload = await stage4_op.payload()

        assert stage3_payload.execution_cache_hash() != stage4_payload.execution_cache_hash(), (
            "Without the build snapshot, mid-build cassette mutation must "
            "change the execution hash — this is the bug the snapshot fixes."
        )

    async def test_snapshot_no_op_when_replay_disabled(self, tmp_path):
        """``disabled``/``None`` modes must not populate the snapshot dict."""
        for mode in (None, "disabled"):
            course, _nb, _cassette = self._build_course_with_replay_notebook(
                tmp_path,
                mode=mode,  # type: ignore[arg-type]
            )
            course._snapshot_cassettes_for_build()
            assert course._build_cassette_snapshots == {}

    async def test_process_all_populates_snapshot(self, tmp_path):
        """``Course.process_all`` must take the snapshot at entry."""
        course, _nb, cassette = self._build_course_with_replay_notebook(tmp_path)
        assert course._build_cassette_snapshots == {}

        backend = DummyBackend()
        await course.process_all(backend)

        assert cassette.resolve() in course._build_cassette_snapshots
        assert course._build_cassette_snapshots[cassette.resolve()] == (
            b"interactions:\n- request: stage3\n"
        )

    async def test_stage3_and_stage4_payloads_agree_within_process_all(self, tmp_path, monkeypatch):
        """End-to-end behavior check.

        Drive a real ``course.process_all`` build through a recording
        backend that (a) captures every payload it sees and (b) mutates
        the cassette on disk while Stage 3 is in flight. Without the
        build-scoped snapshot, the Recording (Stage 3) and Completed
        (Stage 4) payloads would carry different cassette bytes and
        therefore different ``execution_cache_hash`` values — which is the
        exact bug this fix prevents.
        """
        from clm.infrastructure.messaging.notebook_classes import NotebookPayload

        course, _nb, cassette = self._build_course_with_replay_notebook(tmp_path)

        # Recording backend that mutates the cassette mid-Stage-3, before
        # Stage 4 ever runs. This simulates vcrpy appending new
        # interactions during Recording HTML execution.
        seen: list[NotebookPayload] = []

        class _MutatingBackend(DummyBackend):
            async def execute_operation(self, operation, payload):  # type: ignore[override]
                if isinstance(payload, NotebookPayload):
                    seen.append(payload)
                    # Mutate the cassette only when Stage 3's Recording
                    # HTML payload runs, matching the real-world failure
                    # mode (vcrpy appends interactions during Recording
                    # HTML execution; ``merge_staging_into_canonical``
                    # rewrites the cassette in a ``finally`` block before
                    # Stage 4 starts).
                    if payload.kind == "recording" and payload.format == "html":
                        cassette.write_bytes(
                            b"interactions:\n- request: stage3\n- request: stage4-new\n"
                        )
                await super().execute_operation(operation, payload)

        await course.process_all(_MutatingBackend())

        # Find the recording + completed payloads for the English HTML
        # output — both are derived from the same notebook within the same
        # build invocation.
        recording = [p for p in seen if p.kind == "recording" and p.format == "html"]
        completed = [p for p in seen if p.kind == "completed" and p.format == "html"]
        assert recording and completed, (
            "Expected the build to produce at least one Recording and one "
            "Completed payload for the .py notebook."
        )

        # Pair them by language and assert the execution hash matches.
        # Without the snapshot, the Completed payload (Stage 4, after the
        # cassette mutation) would hash different bytes and miss the
        # executed_notebooks cache — forcing redundant kernel runs.
        for rec in recording:
            for comp in completed:
                if rec.language == comp.language:
                    assert rec.execution_cache_hash() == comp.execution_cache_hash(), (
                        f"Stage 3 and Stage 4 produced different execution "
                        f"hashes for language={rec.language!r}; this would "
                        f"miss the executed_notebooks cache."
                    )


class TestGetOperationStage:
    """Stage assignment controls per-file dispatch order during a build.

    Speaker HTML must finish (populating the executed-notebook cache)
    before Completed HTML and Partial HTML can reuse it. If Partial were
    put in the Speaker stage it would race with Speaker and often hit a
    cache miss, silently falling back to a no-execute path that renders
    HTML without the pre-workshop executed outputs.
    """

    @pytest.mark.parametrize(
        "format_,kind",
        [
            ("notebook", "speaker"),
            ("notebook", "completed"),
            ("notebook", "code-along"),
            ("notebook", "partial"),
            ("code", "completed"),
        ],
    )
    def test_non_html_formats_are_stage_1(self, format_, kind):
        from clm.core.course_files.notebook_file import _get_operation_stage
        from clm.core.utils.execution_utils import FIRST_EXECUTION_STAGE

        assert _get_operation_stage(format_, kind) == FIRST_EXECUTION_STAGE

    def test_html_speaker_runs_in_speaker_stage(self):
        from clm.core.course_files.notebook_file import _get_operation_stage
        from clm.core.utils.execution_utils import HTML_SPEAKER_STAGE

        assert _get_operation_stage("html", "speaker") == HTML_SPEAKER_STAGE

    def test_html_completed_runs_in_completed_stage(self):
        from clm.core.course_files.notebook_file import _get_operation_stage
        from clm.core.utils.execution_utils import HTML_COMPLETED_STAGE

        assert _get_operation_stage("html", "completed") == HTML_COMPLETED_STAGE

    def test_html_partial_runs_in_completed_stage(self):
        """Partial HTML reuses Speaker's cache and must run AFTER the
        speaker stage, alongside completed. Running in the speaker stage
        would race with speaker and cache-miss."""
        from clm.core.course_files.notebook_file import _get_operation_stage
        from clm.core.utils.execution_utils import HTML_COMPLETED_STAGE

        assert _get_operation_stage("html", "partial") == HTML_COMPLETED_STAGE

    def test_html_code_along_runs_in_stage_1(self):
        """Code-along HTML doesn't execute and has no cache dependency."""
        from clm.core.course_files.notebook_file import _get_operation_stage
        from clm.core.utils.execution_utils import FIRST_EXECUTION_STAGE

        assert _get_operation_stage("html", "code-along") == FIRST_EXECUTION_STAGE
