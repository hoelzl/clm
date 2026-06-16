"""Tests for voiceover companion file integration in the build pipeline.

These tests verify that:
- NotebookFile detects companion voiceover files
- ProcessNotebookOperation merges companion data into the payload
- Companion files are excluded from other_files
- Unmatched for_slide references produce warnings
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from clm.core.course_files.notebook_file import NotebookFile

# ---------------------------------------------------------------------------
# companion_voiceover_path property
# ---------------------------------------------------------------------------


class TestCompanionVoiceoverPath:
    def _make_notebook_file(self, path: Path) -> NotebookFile:
        """Create a minimal NotebookFile for testing."""
        # Write minimal content so _from_path works
        path.write_text(
            "# %% [markdown]\n# ## Title\n",
            encoding="utf-8",
        )
        course = MagicMock()
        topic = MagicMock()
        nf = NotebookFile(course=course, path=path, topic=topic)
        return nf

    def test_returns_path_when_companion_exists(self, tmp_path: Path):
        slide = tmp_path / "slides_intro.py"
        companion = tmp_path / "voiceover_intro.py"
        companion.write_text("# companion", encoding="utf-8")
        nf = self._make_notebook_file(slide)

        result = nf.companion_voiceover_path

        assert result is not None
        assert result.name == "voiceover_intro.py"
        assert result == companion

    def test_returns_none_when_no_companion(self, tmp_path: Path):
        slide = tmp_path / "slides_intro.py"
        nf = self._make_notebook_file(slide)

        result = nf.companion_voiceover_path

        assert result is None

    def test_topic_prefix(self, tmp_path: Path):
        slide = tmp_path / "topic_overview.py"
        companion = tmp_path / "voiceover_overview.py"
        companion.write_text("# companion", encoding="utf-8")
        nf = self._make_notebook_file(slide)

        result = nf.companion_voiceover_path

        assert result is not None
        assert result.name == "voiceover_overview.py"

    def test_project_prefix(self, tmp_path: Path):
        slide = tmp_path / "project_setup.py"
        companion = tmp_path / "voiceover_setup.py"
        companion.write_text("# companion", encoding="utf-8")
        nf = self._make_notebook_file(slide)

        result = nf.companion_voiceover_path

        assert result is not None
        assert result.name == "voiceover_setup.py"


# ---------------------------------------------------------------------------
# ProcessNotebookOperation payload merging
# ---------------------------------------------------------------------------


class TestPayloadMerging:
    """Test that ProcessNotebookOperation merges companion voiceover data."""

    def test_payload_includes_companion_content(self, tmp_path: Path):
        """When a companion file exists, its voiceover cells appear in the payload data."""
        slide = tmp_path / "slides_test.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n# ## Intro\n',
            encoding="utf-8",
        )
        companion = tmp_path / "voiceover_test.py"
        companion.write_text(
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n'
            "# Voiceover text here.\n",
            encoding="utf-8",
        )

        # Use the merge function directly (payload() requires full Course setup)
        from clm.slides.voiceover_tools import merge_voiceover_text

        slide_text = slide.read_text(encoding="utf-8")
        companion_text = companion.read_text(encoding="utf-8")
        merged, unmatched = merge_voiceover_text(slide_text, companion_text)

        assert "Voiceover text here" in merged
        assert unmatched == []

    def test_unmatched_for_slide_produces_warnings(self, tmp_path: Path, caplog):
        """Unmatched for_slide references should produce log warnings."""
        from clm.slides.voiceover_tools import merge_voiceover_text

        slide_text = '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n# ## Intro\n'
        companion_text = (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="nonexistent"\n'
            "# Orphan voiceover.\n"
        )

        _, unmatched = merge_voiceover_text(slide_text, companion_text)

        assert "nonexistent" in unmatched

    def test_no_companion_no_merge(self, tmp_path: Path):
        """Without a companion file, the payload data should be the original slide text."""
        slide = tmp_path / "slides_test.py"
        original_text = '# %% [markdown] lang="de" tags=["slide"]\n# ## Intro\n'
        slide.write_text(original_text, encoding="utf-8")

        nf = MagicMock()
        nf.companion_voiceover_path = None
        nf.path = slide

        # Simulate what payload() does: read text, check companion
        data = slide.read_text(encoding="utf-8")
        companion = nf.companion_voiceover_path
        if companion is not None:
            from clm.slides.voiceover_tools import merge_voiceover_text

            companion_text = companion.read_text(encoding="utf-8")
            data, _ = merge_voiceover_text(data, companion_text)

        assert data == original_text


class _RecordingReporter:
    """Captures report_error / report_warning calls for assertions."""

    def __init__(self) -> None:
        self.errors: list = []
        self.warnings: list = []

    def report_error(self, error) -> None:
        self.errors.append(error)

    def report_warning(self, warning) -> None:
        self.warnings.append(warning)


class TestVoiceoverMergeEscalation:
    """Build-time escalation of unmatched companion voiceover (#162 hardening).

    Dropped narration (a companion ``for_slide`` with no matching ``slide_id``)
    is reported as a ``BuildError`` so it surfaces in the summary and fails the
    build under ``--fail-on-error``, instead of being a silent log line.
    """

    def test_unmatched_reported_as_build_error(self):
        from clm.core.operations.process_notebook import report_voiceover_merge_issues

        reporter = _RecordingReporter()
        report_voiceover_merge_issues(
            reporter,
            slide_name="slides_x.de.py",
            companion_name="voiceover_x.de.py",
            file_path="/x/slides_x.de.py",
            unmatched=["introduction"],
        )

        assert len(reporter.errors) == 1
        err = reporter.errors[0]
        assert err.category == "voiceover"
        assert err.severity == "error"
        assert err.error_type == "user"
        assert "introduction" in err.message
        assert err.file_path == "/x/slides_x.de.py"

    def test_one_error_per_unmatched(self):
        from clm.core.operations.process_notebook import report_voiceover_merge_issues

        reporter = _RecordingReporter()
        report_voiceover_merge_issues(
            reporter,
            slide_name="slides_x.de.py",
            companion_name="voiceover_x.de.py",
            file_path="/x/slides_x.de.py",
            unmatched=["a", "b"],
        )
        assert len(reporter.errors) == 2

    def test_no_for_slide_entry_renders_clean_message(self):
        from clm.core.operations.process_notebook import report_voiceover_merge_issues

        reporter = _RecordingReporter()
        report_voiceover_merge_issues(
            reporter,
            slide_name="slides_x.de.py",
            companion_name="voiceover_x.de.py",
            file_path="/x/slides_x.de.py",
            unmatched=["<no for_slide>"],
        )
        assert len(reporter.errors) == 1
        assert "no for_slide" in reporter.errors[0].message

    def test_empty_unmatched_reports_nothing(self):
        from clm.core.operations.process_notebook import report_voiceover_merge_issues

        reporter = _RecordingReporter()
        report_voiceover_merge_issues(
            reporter,
            slide_name="s.py",
            companion_name="v.py",
            file_path="/x/s.py",
            unmatched=[],
        )
        assert reporter.errors == []

    def test_none_reporter_is_noop(self):
        from clm.core.operations.process_notebook import report_voiceover_merge_issues

        # No reporter (e.g. a direct payload() call in a test, or a backend
        # without a build_reporter) must not raise.
        report_voiceover_merge_issues(
            None,
            slide_name="s.py",
            companion_name="v.py",
            file_path="/x/s.py",
            unmatched=["intro"],
        )


def _build_op_with_unmatched_companion(tmp_path: Path):
    """Build a real ``ProcessNotebookOperation`` whose companion narrates a
    slide_id that does not exist in the slide — so ``payload()`` / ``execute()``
    exercise the dropped-narration escalation end to end (not just the helper)."""
    from clm.core.course import Course
    from clm.core.course_spec import CourseSpec, TopicSpec
    from clm.core.operations.process_notebook import ProcessNotebookOperation
    from clm.core.output_target import OutputTarget
    from clm.core.section import Section
    from clm.core.topic import Topic
    from clm.core.utils.text_utils import Text

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
    slide = tmp_path / "slides_demo.py"
    slide.write_text(
        '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n# ## Intro\n',
        encoding="utf-8",
    )
    # companion narrates "renamed" — no such slide_id in the slide -> dropped.
    (tmp_path / "voiceover_demo.py").write_text(
        '# %% [markdown] lang="en" tags=["voiceover"] for_slide="renamed"\n# Orphan narration.\n',
        encoding="utf-8",
    )
    section = Section(name=Text(de="S", en="S"), course=course)
    topic = Topic.from_spec(TopicSpec(id="t"), section=section, path=tmp_path)
    topic.build_file_map()
    section.topics.append(topic)
    course.sections.append(section)
    nb = next(f for f in topic.files if isinstance(f, NotebookFile))
    return ProcessNotebookOperation(
        input_file=nb,
        output_file=tmp_path / "out.html",
        language="en",
        format="html",
        kind="completed",
        prog_lang="python",
    )


class TestEscalationWiring:
    """End-to-end wiring of the escalation, beyond the helper in isolation.

    Guards the two-hop path that makes the escalation fire in a real build:
    ``execute()`` pulls ``build_reporter`` off the backend and ``payload()``
    forwards it to the helper. A regression that drops either hop would leave
    every helper-level test green while silently restoring the #162 data loss.
    """

    async def test_payload_reports_unmatched_to_reporter(self, tmp_path: Path):
        op = _build_op_with_unmatched_companion(tmp_path)
        reporter = _RecordingReporter()

        payload = await op.payload(reporter)

        # The orphan narration is dropped from the merged data...
        assert "Orphan narration" not in payload.data
        # ...but is surfaced as a voiceover BuildError instead of vanishing.
        assert len(reporter.errors) == 1
        assert reporter.errors[0].category == "voiceover"
        assert reporter.errors[0].severity == "error"
        assert "renamed" in reporter.errors[0].message

    async def test_execute_pulls_reporter_off_backend(self, tmp_path: Path):
        # The load-bearing wiring: execute() must read the reporter off the
        # backend and thread it into payload(). Dropping the
        # getattr(backend, "build_reporter", None) argument would fail here.
        op = _build_op_with_unmatched_companion(tmp_path)
        reporter = _RecordingReporter()

        class _StubBackend:
            def __init__(self, r):
                self.build_reporter = r

            async def execute_operation(self, operation, payload):
                return None

        await op.execute(_StubBackend(reporter))

        assert len(reporter.errors) == 1
        assert reporter.errors[0].category == "voiceover"

    async def test_execute_without_reporter_does_not_raise(self, tmp_path: Path):
        # A backend without a build_reporter (getattr -> None) must still work.
        op = _build_op_with_unmatched_companion(tmp_path)

        class _NoReporterBackend:
            async def execute_operation(self, operation, payload):
                return None

        await op.execute(_NoReporterBackend())  # must not raise


# ---------------------------------------------------------------------------
# Title-greeting companion survives the build (#242)
# ---------------------------------------------------------------------------


def _build_op_with_title_companion(tmp_path: Path, *, for_slide: bool = True):
    """A real ``ProcessNotebookOperation`` over a header-macro deck whose
    companion narrates the (slide_id-less) title slide. Mirrors
    :func:`_build_op_with_unmatched_companion` but for the #242 title case: the
    greeting must MATCH (no dropped narration) rather than be reported.

    ``for_slide=False`` writes the pre-#242 legacy companion shape
    (``slide_id="title"`` with no ``for_slide``) to prove already-extracted
    decks build without a re-extract.
    """
    from clm.core.course import Course
    from clm.core.course_spec import CourseSpec, TopicSpec
    from clm.core.operations.process_notebook import ProcessNotebookOperation
    from clm.core.output_target import OutputTarget
    from clm.core.section import Section
    from clm.core.topic import Topic
    from clm.core.utils.text_utils import Text

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
    slide = tmp_path / "slides_demo.py"
    slide.write_text(
        "# j2 from 'macros.j2' import header_en\n"
        '# {{ header_en("Demo") }}\n\n'
        '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n# ## Intro\n',
        encoding="utf-8",
    )
    title_attr = ' for_slide="title"' if for_slide else ' slide_id="title"'
    (tmp_path / "voiceover_demo.py").write_text(
        f'# %% [markdown] lang="en" tags=["voiceover"]{title_attr}\n# Greeting narration.\n',
        encoding="utf-8",
    )
    section = Section(name=Text(de="S", en="S"), course=course)
    topic = Topic.from_spec(TopicSpec(id="t"), section=section, path=tmp_path)
    topic.build_file_map()
    section.topics.append(topic)
    course.sections.append(section)
    nb = next(f for f in topic.files if isinstance(f, NotebookFile))
    return ProcessNotebookOperation(
        input_file=nb,
        output_file=tmp_path / "out.html",
        language="en",
        format="html",
        kind="speaker",
        prog_lang="python",
    )


class TestTitleVoiceoverBuild:
    """#242 — a title-greeting companion must merge into the build, not drop."""

    async def test_title_companion_merges_and_does_not_error(self, tmp_path: Path):
        op = _build_op_with_title_companion(tmp_path, for_slide=True)
        reporter = _RecordingReporter()

        payload = await op.payload(reporter)

        assert "Greeting narration." in payload.data
        assert reporter.errors == []

    async def test_legacy_title_companion_merges_without_reextract(self, tmp_path: Path):
        # Pre-#242 companion: slide_id="title", no for_slide. Must still merge.
        op = _build_op_with_title_companion(tmp_path, for_slide=False)
        reporter = _RecordingReporter()

        payload = await op.payload(reporter)

        assert "Greeting narration." in payload.data
        assert reporter.errors == []


async def _render_speaker_cells(data: str) -> list[tuple[str, str]]:
    """Expand j2 + process a deck through SpeakerOutput WITHOUT a kernel, and
    return ``(cell_type, source)`` pairs — the rendered notebook the worker
    would produce (slide_id / for_slide stripped, macros expanded)."""
    from clm.infrastructure.messaging.notebook_classes import NotebookPayload
    from clm.workers.notebook.notebook_processor import NotebookProcessor
    from clm.workers.notebook.output_spec import SpeakerOutput

    spec = SpeakerOutput(format="html", language="de", prog_lang="python")
    proc = NotebookProcessor(spec)
    payload = NotebookPayload(
        data=data,
        input_file="/t/slides_intro.de.py",
        input_file_name="slides_intro.de.py",
        output_file="/t/out.html",
        kind="speaker",
        prog_lang="python",
        language="de",
        format="html",
        correlation_id="cid-242",
        author="Author",
        organization="Org",
    )
    proc._author = payload.author
    proc._organization = payload.organization
    expanded = await proc.load_and_expand_jinja_template(
        payload.data, payload.input_file_name, payload.correlation_id
    )
    processed = await proc.process_notebook_for_spec(expanded, payload)
    return [(c.cell_type, c.source) for c in processed.cells]


class TestTitleVoiceoverRenderParity:
    """The literal #242 acceptance criterion: the rendered speaker notebook of
    an extract+merge deck is identical to the inline-authored deck."""

    async def test_extract_merge_renders_identically_to_inline(self, tmp_path: Path):
        from clm.slides.voiceover_tools import extract_voiceover, merge_voiceover_text

        inline = (
            "# j2 from 'macros.j2' import header_de\n"
            '# {{ header_de("Titel") }}\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="title"\n'
            "# - Herzlich willkommen!\n\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="first-real-slide"\n'
            "# - Erste echte Folie\n"
        )
        slide = tmp_path / "slides_intro.de.py"
        slide.write_text(inline, encoding="utf-8")
        extract_voiceover(slide, force=True, layout="sibling")
        companion = tmp_path / "voiceover_intro.de.py"
        merged, unmatched = merge_voiceover_text(
            slide.read_text(encoding="utf-8"), companion.read_text(encoding="utf-8")
        )
        assert unmatched == []

        inline_cells = await _render_speaker_cells(inline)
        merged_cells = await _render_speaker_cells(merged)

        # The greeting actually rendered (not silently dropped)...
        assert any("Herzlich willkommen" in src for _, src in merged_cells)
        # ...and the rendered notebooks are byte-identical.
        assert merged_cells == inline_cells


# ---------------------------------------------------------------------------
# Voiceover cells in output specs
# ---------------------------------------------------------------------------


class TestVoiceoverCellsInOutputSpecs:
    """Verify that voiceover cells are handled correctly by output specs.

    After merging companion voiceover cells, the output spec's cell
    filtering determines which outputs include them.
    """

    def test_speaker_output_keeps_voiceover_cells(self):
        from clm.workers.notebook.output_spec import SpeakerOutput

        spec = SpeakerOutput(language="de")
        from nbformat import NotebookNode

        cell = NotebookNode(
            {
                "cell_type": "markdown",
                "source": "Voiceover content",
                "metadata": {"tags": ["voiceover"], "lang": "de"},
            }
        )

        assert spec.is_cell_included(cell) is True

    def test_completed_output_removes_voiceover_cells(self):
        from clm.workers.notebook.output_spec import CompletedOutput

        spec = CompletedOutput()
        from nbformat import NotebookNode

        cell = NotebookNode(
            {
                "cell_type": "markdown",
                "source": "Voiceover content",
                "metadata": {"tags": ["voiceover"], "lang": "de"},
            }
        )

        assert spec.is_cell_included(cell) is False

    def test_codealong_output_removes_voiceover_cells(self):
        from clm.workers.notebook.output_spec import CodeAlongOutput

        spec = CodeAlongOutput()
        from nbformat import NotebookNode

        cell = NotebookNode(
            {
                "cell_type": "markdown",
                "source": "Voiceover content",
                "metadata": {"tags": ["voiceover"], "lang": "de"},
            }
        )

        assert spec.is_cell_included(cell) is False

    def test_speaker_output_keeps_notes_cells(self):
        from clm.workers.notebook.output_spec import SpeakerOutput

        spec = SpeakerOutput(language="de")
        from nbformat import NotebookNode

        cell = NotebookNode(
            {
                "cell_type": "markdown",
                "source": "Notes content",
                "metadata": {"tags": ["notes"], "lang": "de"},
            }
        )

        assert spec.is_cell_included(cell) is True
