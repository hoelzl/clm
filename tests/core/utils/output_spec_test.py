"""Tests for OutputSpec caching properties."""

import pytest

from clm.workers.notebook.output_spec import (
    CodeAlongOutput,
    CompletedOutput,
    OutputSpec,
    RecordingOutput,
    SpeakerOutput,
    TrainerOutput,
    create_output_spec,
)


class TestOutputSpecCachingProperties:
    """Tests for should_cache_execution and can_reuse_execution properties."""

    # Recording HTML should cache
    def test_recording_html_should_cache(self):
        """Recording HTML should cache its executed notebook."""
        spec = RecordingOutput(format="html")
        assert spec.should_cache_execution is True
        assert spec.can_reuse_execution is False

    def test_recording_non_html_should_not_cache(self):
        """Recording notebook/code formats should not cache."""
        spec = RecordingOutput(format="notebook")
        assert spec.should_cache_execution is False
        assert spec.can_reuse_execution is False

        spec = RecordingOutput(format="code")
        assert spec.should_cache_execution is False
        assert spec.can_reuse_execution is False

    # Trainer reuses Recording's cache
    def test_trainer_html_can_reuse(self):
        """Trainer HTML can reuse Recording's cached notebook (drops voiceover)."""
        spec = TrainerOutput(format="html")
        assert spec.can_reuse_execution is True
        assert spec.should_cache_execution is False

    # Completed HTML can reuse
    def test_completed_html_can_reuse(self):
        """Completed HTML can reuse Recording's cached notebook."""
        spec = CompletedOutput(format="html")
        assert spec.can_reuse_execution is True
        assert spec.should_cache_execution is False

    def test_completed_non_html_cannot_reuse(self):
        """Completed notebook/code formats cannot reuse cache."""
        spec = CompletedOutput(format="notebook")
        assert spec.can_reuse_execution is False
        assert spec.should_cache_execution is False

        spec = CompletedOutput(format="code")
        assert spec.can_reuse_execution is False
        assert spec.should_cache_execution is False

    # CodeAlong should not cache or reuse
    def test_code_along_no_caching(self):
        """CodeAlong should not cache or reuse (different cell filtering)."""
        for fmt in ["html", "notebook", "code"]:
            spec = CodeAlongOutput(format=fmt)
            assert spec.should_cache_execution is False
            assert spec.can_reuse_execution is False

    # Base OutputSpec defaults to False
    def test_base_defaults_false(self):
        """Base OutputSpec defaults to False for both properties."""
        # Using CodeAlongOutput as a concrete subclass that doesn't override
        spec = CodeAlongOutput(format="html")
        # CodeAlongOutput doesn't evaluate for HTML, so defaults apply
        assert spec.evaluate_for_html is False
        assert spec.should_cache_execution is False
        assert spec.can_reuse_execution is False

    # create_output_spec preserves properties
    def test_create_output_spec_recording_html(self):
        """create_output_spec preserves caching properties for recording."""
        spec = create_output_spec(kind="recording", format="html", language="en")
        assert spec.should_cache_execution is True
        assert spec.can_reuse_execution is False

    def test_create_output_spec_trainer_html(self):
        """create_output_spec preserves caching properties for trainer (cache reuser)."""
        spec = create_output_spec(kind="trainer", format="html", language="en")
        assert spec.should_cache_execution is False
        assert spec.can_reuse_execution is True

    def test_create_output_spec_completed_html(self):
        """create_output_spec preserves caching properties for completed."""
        spec = create_output_spec(kind="completed", format="html", language="en")
        assert spec.should_cache_execution is False
        assert spec.can_reuse_execution is True

    def test_create_output_spec_code_along_html(self):
        """create_output_spec preserves caching properties for code-along."""
        spec = create_output_spec(kind="code-along", format="html", language="de")
        assert spec.should_cache_execution is False
        assert spec.can_reuse_execution is False

    # Verify caching only applies when evaluate_for_html is True
    def test_caching_requires_evaluate_for_html(self):
        """Caching should only apply when evaluate_for_html is True."""
        # Recording HTML evaluates, so can cache
        spec = RecordingOutput(format="html")
        assert spec.evaluate_for_html is True
        assert spec.should_cache_execution is True

        # Completed HTML evaluates, so can reuse
        spec = CompletedOutput(format="html")
        assert spec.evaluate_for_html is True
        assert spec.can_reuse_execution is True

        # CodeAlong HTML doesn't evaluate, so no caching
        spec = CodeAlongOutput(format="html")
        assert spec.evaluate_for_html is False
        assert spec.should_cache_execution is False
        assert spec.can_reuse_execution is False
