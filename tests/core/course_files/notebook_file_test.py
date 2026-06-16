from pathlib import Path
from typing import cast

import pytest

from clm.core.course_file import CourseFile
from clm.core.course_files.notebook_file import NotebookFile, _base_cassette_stem
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
        self,
        course_1,
        tmp_path: Path,
        *,
        with_cassette: bool = False,
        nested: bool = False,
        sidecar_layout: str | None = None,
    ) -> NotebookFile:
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="python",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
            sidecar_layout=sidecar_layout,
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

    def test_cassette_path_prefers_new_cassettes_dir(self, course_1, tmp_path):
        # The non-underscore ``cassettes/`` is the current name for the sidecar.
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=False)
        (tmp_path / "cassettes").mkdir()
        (tmp_path / "cassettes" / "slides_replay.http-cassette.yaml").write_text(
            "interactions: []\n", encoding="utf-8"
        )
        assert nb.cassette_path == tmp_path / "cassettes" / "slides_replay.http-cassette.yaml"
        assert nb.cassette_relative_name == "cassettes/slides_replay.http-cassette.yaml"

    def test_cassette_path_prefers_new_dir_over_legacy(self, course_1, tmp_path):
        # Both ``cassettes/`` and legacy ``_cassettes/`` present → new wins.
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=True, nested=True)
        (tmp_path / "cassettes").mkdir()
        (tmp_path / "cassettes" / "slides_replay.http-cassette.yaml").write_text(
            "interactions: []\n", encoding="utf-8"
        )
        assert nb.cassette_relative_name == "cassettes/slides_replay.http-cassette.yaml"

    def test_expected_cassette_path_uses_new_cassettes_dir(self, course_1, tmp_path):
        (tmp_path / "cassettes").mkdir()
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=False)
        assert (
            nb.expected_cassette_path == tmp_path / "cassettes" / "slides_replay.http-cassette.yaml"
        )
        assert nb.expected_cassette_relative_name == "cassettes/slides_replay.http-cassette.yaml"

    def test_expected_cassette_path_prefers_new_dir_over_legacy(self, course_1, tmp_path):
        (tmp_path / "_cassettes").mkdir()
        (tmp_path / "cassettes").mkdir()
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=False)
        assert (
            nb.expected_cassette_path == tmp_path / "cassettes" / "slides_replay.http-cassette.yaml"
        )

    def test_expected_cassette_path_uses_subdir_when_course_opts_in(
        self, course_1, tmp_path, monkeypatch
    ):
        # No cassettes/ dir exists yet, but the course spec asks for the
        # ``subdir`` layout, so a first-ever recording targets cassettes/.
        monkeypatch.delenv("CLM_SIDECAR_LAYOUT", raising=False)
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=False, sidecar_layout="subdir")
        assert not (tmp_path / "cassettes").exists()  # not pre-created
        assert (
            nb.expected_cassette_path == tmp_path / "cassettes" / "slides_replay.http-cassette.yaml"
        )
        assert nb.expected_cassette_relative_name == "cassettes/slides_replay.http-cassette.yaml"

    def test_expected_cassette_path_sibling_when_course_opts_sibling(
        self, course_1, tmp_path, monkeypatch
    ):
        # An explicit ``sibling`` course default keeps the historical behavior.
        monkeypatch.delenv("CLM_SIDECAR_LAYOUT", raising=False)
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=False, sidecar_layout="sibling")
        assert nb.expected_cassette_path == tmp_path / "slides_replay.http-cassette.yaml"

    def test_expected_cassette_path_env_overrides_course_sibling(
        self, course_1, tmp_path, monkeypatch
    ):
        # CLM_SIDECAR_LAYOUT wins over the spec value (precedence check).
        monkeypatch.setenv("CLM_SIDECAR_LAYOUT", "subdir")
        nb = self._make_nb_file(course_1, tmp_path, with_cassette=False, sidecar_layout="sibling")
        assert (
            nb.expected_cassette_path == tmp_path / "cassettes" / "slides_replay.http-cassette.yaml"
        )


class TestCompanionVoiceoverResolution:
    """NotebookFile.companion_voiceover_path resolves either layout.

    The build probes this property host-side and merges the companion's
    narration at payload time. It must find a companion whether it sits as a
    sibling or has been relocated into the ``voiceover/`` subdirectory."""

    def _make_nb_file(self, tmp_path: Path) -> NotebookFile:
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
        py_file = tmp_path / "slides_intro.py"
        py_file.write_text("# %% [markdown]\n# Title\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)
        return cast(NotebookFile, CourseFile.from_path(course, py_file, topic))

    def test_none_when_absent(self, tmp_path):
        nb = self._make_nb_file(tmp_path)
        assert nb.companion_voiceover_path is None

    def test_finds_sibling(self, tmp_path):
        nb = self._make_nb_file(tmp_path)
        sibling = tmp_path / "voiceover_intro.py"
        sibling.write_text('# %% [markdown] tags=["voiceover"]\n# hi\n', encoding="utf-8")
        assert nb.companion_voiceover_path == sibling

    def test_prefers_voiceover_subdir(self, tmp_path):
        nb = self._make_nb_file(tmp_path)
        (tmp_path / "voiceover").mkdir()
        nested = tmp_path / "voiceover" / "voiceover_intro.py"
        nested.write_text('# %% [markdown] tags=["voiceover"]\n# hi\n', encoding="utf-8")
        assert nb.companion_voiceover_path == nested


class TestReplayCassetteLanguageFallback:
    """Issue #159: split ``.de``/``.en`` decks fall back to the base cassette
    on the *replay* path, while the strict properties stay language-specific."""

    @pytest.mark.parametrize(
        ("stem", "expected"),
        [
            ("slides_replay.de", "slides_replay"),
            ("slides_replay.en", "slides_replay"),
            ("slides_replay", None),  # no language token
            ("slides_v1.2", None),  # dotted but not a language token
            ("slides_replay.fr", None),  # unrecognised language token
            ("slides_replay.de.de", "slides_replay.de"),  # strip once only
        ],
    )
    def test_base_cassette_stem(self, stem, expected):
        assert _base_cassette_stem(stem) == expected

    def _make_split_nb_file(self, tmp_path: Path, lang: str) -> NotebookFile:
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
        py_file = tmp_path / f"slides_replay.{lang}.py"
        py_file.write_text("# %% [markdown]\n# Title\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)
        return cast(NotebookFile, CourseFile.from_path(course, py_file, topic))

    def test_prefers_language_specific(self, tmp_path):
        nb = self._make_split_nb_file(tmp_path, "de")
        (tmp_path / "slides_replay.de.http-cassette.yaml").write_text(
            "interactions: []\n", encoding="utf-8"
        )
        (tmp_path / "slides_replay.http-cassette.yaml").write_text(
            "interactions: []\n", encoding="utf-8"
        )
        assert nb.replay_cassette_path == tmp_path / "slides_replay.de.http-cassette.yaml"

    def test_falls_back_to_base_sibling(self, tmp_path):
        nb = self._make_split_nb_file(tmp_path, "de")
        base = tmp_path / "slides_replay.http-cassette.yaml"
        base.write_text("interactions: []\n", encoding="utf-8")
        assert nb.cassette_path is None  # strict stays language-specific
        assert nb.replay_cassette_path == base
        assert nb.replay_cassette_relative_name == "slides_replay.http-cassette.yaml"

    def test_falls_back_to_base_nested(self, tmp_path):
        nb = self._make_split_nb_file(tmp_path, "en")
        (tmp_path / "_cassettes").mkdir()
        base = tmp_path / "_cassettes" / "slides_replay.http-cassette.yaml"
        base.write_text("interactions: []\n", encoding="utf-8")
        assert nb.cassette_path is None
        assert nb.replay_cassette_path == base
        assert nb.replay_cassette_relative_name == "_cassettes/slides_replay.http-cassette.yaml"

    def test_none_when_neither(self, tmp_path):
        nb = self._make_split_nb_file(tmp_path, "de")
        assert nb.replay_cassette_path is None
        assert nb.replay_cassette_relative_name is None

    def test_strict_properties_do_not_fall_back(self, tmp_path):
        """Record/seed/sweep rely on the strict, language-specific names."""
        nb = self._make_split_nb_file(tmp_path, "de")
        (tmp_path / "slides_replay.http-cassette.yaml").write_text(
            "interactions: []\n", encoding="utf-8"
        )
        assert nb.cassette_path is None
        assert nb.cassette_relative_name is None
        assert nb.expected_cassette_relative_name == "slides_replay.de.http-cassette.yaml"

    def test_no_fallback_for_non_split_deck(self, tmp_path):
        """A non-split deck has no language token → replay == strict lookup."""
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
        nb = cast(NotebookFile, CourseFile.from_path(course, py_file, topic))
        assert nb.replay_cassette_path is None
        base = tmp_path / "slides_replay.http-cassette.yaml"
        base.write_text("interactions: []\n", encoding="utf-8")
        assert nb.replay_cassette_path == base  # == cassette_path, no stripping


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

    def test_other_files_excludes_cassette_even_when_mode_set(self, course_1, tmp_path):
        # Since #355 nothing in the worker or kernel reads the cassette file
        # (the replay proxy reads/writes the canonical on the host), so the
        # bytes are no longer shipped in the payload - for any mode.
        op, _ = self._make_operation(course_1, tmp_path, mode="replay", with_cassette=True)
        other = op.compute_other_files()
        assert "slides_replay.http-cassette.yaml" not in other

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

    def test_other_files_excludes_orphan_staging_cassette(self, course_1, tmp_path):
        """Per-worker ``.staging-*`` cassettes must never enter ``other_files``.

        Regression for the "orphan staging cassette crashes the next build"
        failure mode: a concurrent worker may run
        :func:`merge_staging_into_canonical` and ``unlink`` the staging
        file between glob enumeration and ``read_bytes()`` inside
        ``compute_other_files``, surfacing as ``FileNotFoundError`` during
        b64 encoding. Defense-in-depth: even with the eager pre-build
        sweep, a *new* orphan can appear mid-build (race with a
        concurrent worker), so payload construction must filter
        ``*.http-cassette.yaml.staging-*`` itself.
        """
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
        course.http_replay_mode = "replay"
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t", http_replay=True)

        # Build a DirectoryTopic so ``add_files_in_dir`` enumerates the
        # whole topic directory — this is the path that picks up the
        # canonical cassette *and* the orphan staging file as ordinary
        # course files. A FileTopic-only topic would miss both.
        topic_dir = tmp_path / "topic_dir"
        topic_dir.mkdir()
        py_file = topic_dir / "slides_replay.py"
        py_file.write_text("# %% [markdown]\n# Title\n", encoding="utf-8")
        canonical = topic_dir / "slides_replay.http-cassette.yaml"
        canonical.write_bytes(b"interactions: []\n")
        orphan = topic_dir / "slides_replay.http-cassette.yaml.staging-99-abc"
        orphan.write_bytes(b"interactions: []\n")

        topic = Topic.from_spec(topic_spec, section=section, path=topic_dir)
        topic.build_file_map()

        # Sanity: the staging file landed in topic.files via add_files_in_dir.
        topic_file_names = {f.path.name for f in topic.files}
        assert orphan.name in topic_file_names, (
            "test setup precondition: orphan staging file should be in topic.files"
        )

        nb = next(
            f
            for f in topic.files
            if isinstance(f, NotebookFile) and f.path.name == "slides_replay.py"
        )
        nb.http_replay = True

        op = ProcessNotebookOperation(
            input_file=nb,
            output_file=tmp_path / "out.html",
            language="en",
            format="html",
            kind="speaker",
            prog_lang="python",
            http_replay_mode="replay",
        )
        other = op.compute_other_files()

        # The orphan staging file must NOT be in other_files (any key
        # form: relative path, basename, or with a leading ``./``).
        for key in other:
            assert "staging-" not in key, (
                f"orphan staging file leaked into other_files under key {key!r}"
            )

        # The canonical cassette is not shipped either (since #355 nothing
        # in the worker reads it; the proxy handles it host-side).
        assert "slides_replay.http-cassette.yaml" not in other

    # --- Issue #159: split-deck language fallback through the operation ---

    def _make_split_operation(
        self,
        tmp_path: Path,
        *,
        lang: str,
        mode: str | None,
        base: bool = False,
        lang_specific: bool = False,
    ) -> tuple[ProcessNotebookOperation, NotebookFile]:
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
        py_file = tmp_path / f"slides_replay.{lang}.py"
        py_file.write_text("# %% [markdown]\n# Title\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)
        if base:
            (tmp_path / "slides_replay.http-cassette.yaml").write_bytes(b"interactions: []\n")
        if lang_specific:
            (tmp_path / f"slides_replay.{lang}.http-cassette.yaml").write_bytes(
                b"interactions: []\n"
            )
        nb = cast(NotebookFile, CourseFile.from_path(course, py_file, topic))
        nb.http_replay = mode is not None
        op = ProcessNotebookOperation(
            input_file=nb,
            output_file=tmp_path / "out.html",
            language=lang,
            format="html",
            kind="speaker",
            prog_lang="python",
            http_replay_mode=mode,
        )
        return op, nb

    def test_resolve_replay_falls_back_to_base_for_split(self, tmp_path):
        op, _ = self._make_split_operation(tmp_path, lang="de", mode="replay", base=True)
        assert op._resolve_cassette_name() == "slides_replay.http-cassette.yaml"

    def test_resolve_replay_prefers_language_specific_for_split(self, tmp_path):
        op, _ = self._make_split_operation(
            tmp_path, lang="de", mode="replay", base=True, lang_specific=True
        )
        assert op._resolve_cassette_name() == "slides_replay.de.http-cassette.yaml"

    def test_resolve_record_modes_ignore_base_fallback_for_split(self, tmp_path):
        # ``once``/``refresh`` must target the language-specific name even
        # when only the base cassette exists — never the shared base (which a
        # full re-record would overwrite / seed the other language from).
        for mode in ("once", "refresh"):
            op, _ = self._make_split_operation(tmp_path, lang="de", mode=mode, base=True)
            assert op._resolve_cassette_name() == "slides_replay.de.http-cassette.yaml", (
                f"mode={mode!r} must keep the language-specific record target"
            )

    def test_other_files_ships_no_cassette_on_replay_for_split(self, tmp_path):
        # The base-cassette language fallback (issue #159) lives in
        # ``_resolve_cassette_name``/the routing tag; the bytes themselves
        # are never shipped (#355).
        op, _ = self._make_split_operation(tmp_path, lang="de", mode="replay", base=True)
        other = op.compute_other_files()
        assert "slides_replay.http-cassette.yaml" not in other
        assert "slides_replay.de.http-cassette.yaml" not in other

    def test_other_files_ignores_base_on_record_for_split(self, tmp_path):
        op, _ = self._make_split_operation(tmp_path, lang="de", mode="once", base=True)
        other = op.compute_other_files()
        assert "slides_replay.http-cassette.yaml" not in other
        assert "slides_replay.de.http-cassette.yaml" not in other


class TestExecutionCacheHashCassetteIndependence:
    """``execution_cache_hash`` must not depend on cassette bytes.

    Folding cassette content into the hash produces an unfixable
    cache-miss loop: build 1's payload is built before the kernel writes
    the cassette, so build 1 stores under hash(pre-execution cassette);
    build 2's payload is built after, so build 2 looks up under
    hash(post-execution cassette) and misses. The same disagreement
    fires the first time a cassette transitions from missing to
    populated, and whenever ``.gitattributes`` normalizes CRLF↔LF
    between builds.

    These tests pin the across-build cache-hit invariant.
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

    async def test_hash_survives_mid_build_cassette_mutation(self, tmp_path):
        """Stage 3 → Stage 4: cassette bytes may change but hash must not."""
        course, nb, cassette = self._build_course_with_replay_notebook(tmp_path)

        stage3_op = self._make_op(nb, tmp_path, "new-episodes", "recording")
        stage3_payload = await stage3_op.payload()
        stage3_hash = stage3_payload.execution_cache_hash()

        # Simulate vcrpy appending new interactions during Stage 3
        # execution.
        cassette.write_bytes(b"interactions:\n- request: stage3\n- request: stage4-new\n")

        stage4_op = self._make_op(nb, tmp_path, "new-episodes", "completed")
        stage4_payload = await stage4_op.payload()
        stage4_hash = stage4_payload.execution_cache_hash()

        assert stage3_hash == stage4_hash

    async def test_hash_survives_cassette_missing_to_present(self, tmp_path):
        """Build 1 (cassette missing) vs build 2 (cassette present): same hash.

        This is the failure mode the user hit: build 1 creates the
        cassette from scratch, commits it, and build 2 must read it from
        cache. Before this fix, build 1 stored under hash(empty
        cassette) and build 2 looked up under hash(committed cassette)
        — never matching.
        """
        course, nb, cassette = self._build_course_with_replay_notebook(tmp_path)

        # Build 1: cassette does not exist on disk yet.
        cassette.unlink()
        op1 = self._make_op(nb, tmp_path, "new-episodes", "recording")
        hash_build1 = (await op1.payload()).execution_cache_hash()

        # Simulate build 1 finishing: cassette is now on disk.
        cassette.write_bytes(b"interactions:\n- request: recorded\n")

        # Build 2: same notebook, cassette present.
        op2 = self._make_op(nb, tmp_path, "new-episodes", "recording")
        hash_build2 = (await op2.payload()).execution_cache_hash()

        assert hash_build1 == hash_build2, (
            "execution_cache_hash must not depend on whether the "
            "cassette existed at payload-construction time."
        )

    async def test_hash_survives_crlf_lf_flip(self, tmp_path):
        """LF↔CRLF rewrites of the cassette must not change the cache key.

        Git's ``eol=lf`` smudge filter rewrites cassettes on checkout;
        before this fix that silently invalidated the executed-notebook
        cache.
        """
        course, nb, cassette = self._build_course_with_replay_notebook(tmp_path)

        cassette.write_bytes(b"interactions:\n- request: one\n- request: two\n")
        op_lf = self._make_op(nb, tmp_path, "new-episodes", "recording")
        hash_lf = (await op_lf.payload()).execution_cache_hash()

        cassette.write_bytes(b"interactions:\r\n- request: one\r\n- request: two\r\n")
        op_crlf = self._make_op(nb, tmp_path, "new-episodes", "recording")
        hash_crlf = (await op_crlf.payload()).execution_cache_hash()

        assert hash_lf == hash_crlf

    async def test_hash_survives_replay_mode_toggle(self, tmp_path):
        """Whether replay is on or off, the cache key over source data
        is the same."""
        course, nb, _cassette = self._build_course_with_replay_notebook(tmp_path)

        op_replay = self._make_op(nb, tmp_path, "replay", "recording")
        op_disabled = self._make_op(nb, tmp_path, "disabled", "recording")

        hash_replay = (await op_replay.payload()).execution_cache_hash()
        hash_disabled = (await op_disabled.payload()).execution_cache_hash()

        assert hash_replay == hash_disabled


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
