"""Tests for OutputSpec caching properties."""

import pytest

from clm.workers.notebook.output_spec import (
    CodeAlongOutput,
    CompletedOutput,
    OutputSpec,
    SpeakerOutput,
    create_output_spec,
)


class TestOutputSpecCachingProperties:
    """Tests for should_cache_execution and can_reuse_execution properties."""

    # Speaker HTML should cache
    def test_speaker_html_should_cache(self):
        """Speaker HTML should cache its executed notebook."""
        spec = SpeakerOutput(format="html")
        assert spec.should_cache_execution is True
        assert spec.can_reuse_execution is False

    def test_speaker_non_html_should_not_cache(self):
        """Speaker notebook/code formats should not cache."""
        spec = SpeakerOutput(format="notebook")
        assert spec.should_cache_execution is False
        assert spec.can_reuse_execution is False

        spec = SpeakerOutput(format="code")
        assert spec.should_cache_execution is False
        assert spec.can_reuse_execution is False

    # Completed HTML can reuse
    def test_completed_html_can_reuse(self):
        """Completed HTML can reuse Speaker's cached notebook."""
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
    def test_create_output_spec_speaker_html(self):
        """create_output_spec preserves caching properties for speaker."""
        spec = create_output_spec(kind="speaker", format="html", language="en")
        assert spec.should_cache_execution is True
        assert spec.can_reuse_execution is False

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
        # Speaker HTML evaluates, so can cache
        spec = SpeakerOutput(format="html")
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
