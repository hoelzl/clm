"""Tests for notebook messaging classes.

Tests cover NotebookPayload, NotebookResult, and helper functions.
"""

import pytest

from clm.infrastructure.messaging.notebook_classes import (
    NotebookPayload,
    NotebookResult,
    notebook_metadata,
    notebook_metadata_tags,
)


class TestNotebookMetadataFunctions:
    """Test notebook metadata helper functions."""

    def test_notebook_metadata(self):
        """Should format metadata as colon-separated string."""
        result = notebook_metadata("completed", "python", "en", "html")
        assert result == "completed:python:en:html"

    def test_notebook_metadata_tags(self):
        """Should return tuple of metadata tags."""
        result = notebook_metadata_tags("completed", "python", "en", "html")

        assert result == ("completed", "python", "en", "html")
        assert isinstance(result, tuple)


class TestNotebookPayload:
    """Test NotebookPayload class."""

    @pytest.fixture
    def sample_payload(self):
        """Create a sample NotebookPayload for testing."""
        return NotebookPayload(
            correlation_id="test-123",
            input_file="/path/to/input.ipynb",
            input_file_name="input.ipynb",
            output_file="/path/to/output.html",
            data="notebook content here",
            kind="completed",
            prog_lang="python",
            language="en",
            format="html",
        )

    def test_payload_creation(self, sample_payload):
        """Should create NotebookPayload with all fields."""
        assert sample_payload.correlation_id == "test-123"
        assert sample_payload.kind == "completed"
        assert sample_payload.prog_lang == "python"
        assert sample_payload.language == "en"
        assert sample_payload.format == "html"

    def test_payload_default_values(self, sample_payload):
        """Should have default values for optional fields."""
        assert sample_payload.template_dir == ""
        assert sample_payload.other_files == {}
        assert sample_payload.fallback_execute is False

    def test_from_job_payload_round_trips_every_field(self):
        """Structural regression guard for issue #17.

        The worker reconstructs the payload from the job's serialized JSON via
        ``NotebookPayload.from_job_payload`` (which deserializes the whole dict
        through ``model_validate``). It must preserve *every* field the host
        sets — a hand-listed reconstruction previously dropped
        ``cross_references`` (and ``svg_available_stems`` / ``inline_images``),
        silently disabling the cross-reference rewrite so every ``clm:`` link
        shipped unrewritten.

        Every field is given a distinctive non-default value so a *dropped*
        field is always caught (a dropped field would fall back to its default
        and the dump comparison would differ). The ``model_fields`` set-equality
        check ties this test to the model: adding a field makes it fail until
        the new field is covered here, which then proves it survives the
        round-trip too.
        """
        # A non-default value for every NotebookPayload field.
        values = {
            "correlation_id": "cid-x",
            "input_file": "/in/notebook.py",
            "input_file_name": "notebook.py",
            "output_file": "/out/nb.html",
            "data": "SOURCE",
            "kind": "trainer",
            "prog_lang": "cpp",
            "language": "de",
            "format": "html",
            "template_dir": "tmpl",
            "other_files": {"helper.py": b"x = 1"},
            "fallback_execute": True,
            "skip_evaluation": True,
            "skip_errors": True,
            "http_replay_mode": "once",
            "http_replay_cassette_name": "cassette.yaml",
            "http_replay_trace_dir": "/trace",
            "img_path_prefix": "../../img/",
            "source_topic_dir": "module_100/topic_010_intro",
            "svg_available_stems": ["diagram"],
            "inline_images": True,
            "author": "Ada Lovelace",
            "organization": "Coding Academy",
            "cross_references": {"workshop": "../Workshops/02%20Workshop.html"},
        }
        assert set(values) == set(NotebookPayload.model_fields), (
            "NotebookPayload fields changed — add the new field above with a "
            "non-default value so the round-trip guard covers it."
        )

        original = NotebookPayload(**values)
        # Same serialization the SqliteBackend uses to enqueue the job.
        dumped = original.model_dump(mode="json")

        # Reconstruct exactly as NotebookWorker does. The file-bound overrides
        # are set to the original's values so the whole model must round-trip.
        restored = NotebookPayload.from_job_payload(
            dumped,
            content=original.data,
            input_file=original.input_file,
            output_file=original.output_file,
            fallback_correlation_id="unused-correlation-id-present",
        )

        assert restored.model_dump() == original.model_dump()

    def test_from_job_payload_raises_on_missing_required_field(self):
        """A malformed job payload (missing a required descriptor) fails loudly
        rather than being silently defaulted — the host always sets these, so a
        gap is a real bug worth surfacing."""
        import pytest as _pytest
        from pydantic import ValidationError

        with _pytest.raises(ValidationError):
            NotebookPayload.from_job_payload(
                {"kind": "completed"},  # missing prog_lang / language / format
                content="src",
                input_file="/in.py",
                output_file="/out.html",
                fallback_correlation_id="cid",
            )

    def test_notebook_text_property(self, sample_payload):
        """Should return data as notebook_text property."""
        assert sample_payload.notebook_text == "notebook content here"

    def test_content_hash(self, sample_payload):
        """Should compute content hash including metadata."""
        hash1 = sample_payload.content_hash()
        hash2 = sample_payload.content_hash()

        # Same payload should produce same hash
        assert hash1 == hash2
        # Hash should be SHA256 hex digest (64 chars)
        assert len(hash1) == 64

    def test_content_hash_differs_with_metadata(self):
        """Different metadata should produce different hash."""
        payload1 = NotebookPayload(
            correlation_id="test-1",
            input_file="/input.ipynb",
            input_file_name="input.ipynb",
            output_file="/output.html",
            data="same content",
            kind="completed",
            prog_lang="python",
            language="en",
            format="html",
        )
        payload2 = NotebookPayload(
            correlation_id="test-2",
            input_file="/input.ipynb",
            input_file_name="input.ipynb",
            output_file="/output.html",
            data="same content",  # Same content but different format
            kind="completed",
            prog_lang="python",
            language="en",
            format="slides",  # Different format
        )

        # Different metadata should produce different hash
        assert payload1.content_hash() != payload2.content_hash()

    def test_output_metadata(self, sample_payload):
        """Should return formatted metadata string."""
        assert sample_payload.output_metadata() == "completed:python:en:html"


class TestExecutionCacheHash:
    """Cassette bytes are intentionally NOT folded into the hash.

    Folding them caused an unfixable cache-miss loop:
    ``compute_other_files`` reads the cassette at payload construction
    (pre-execution), while record-capable modes
    (``once``/``new-episodes``/``refresh``) rewrite the cassette
    post-execution. The next build's lookup hash uses the post-execution
    cassette and never matches the prior build's stored hash. The same
    issue surfaces the first time a cassette is created (missing →
    populated) and whenever ``.gitattributes`` normalizes CRLF↔LF
    between builds. Users who want re-execution after manual cassette
    edits should use ``--ignore-cache``.
    """

    def _payload(self, **overrides):
        defaults = {
            "correlation_id": "cid",
            "input_file": "/slides.py",
            "input_file_name": "slides.py",
            "output_file": "/slides.html",
            "data": "cell contents",
            "kind": "speaker",
            "prog_lang": "python",
            "language": "en",
            "format": "html",
        }
        defaults.update(overrides)
        return NotebookPayload(**defaults)

    def test_hash_stable_without_replay(self):
        """Identical payloads without replay must produce identical hashes."""
        p1 = self._payload()
        p2 = self._payload()
        assert p1.execution_cache_hash() == p2.execution_cache_hash()

    def test_hash_invariant_under_cassette_bytes_change(self):
        """Cassette growth must NOT change the cache key.

        Pins the across-build cache-hit invariant: build 1 hashes (and
        stores) under cassette state A; vcrpy then writes state B;
        build 2 hashes under state B; both must yield the same hash so
        the lookup hits.
        """
        p_old = self._payload(
            http_replay_mode="new-episodes",
            http_replay_cassette_name="slides.http-cassette.yaml",
            other_files={"slides.http-cassette.yaml": b"old-cassette-bytes"},
        )
        p_new = self._payload(
            http_replay_mode="new-episodes",
            http_replay_cassette_name="slides.http-cassette.yaml",
            other_files={"slides.http-cassette.yaml": b"new-cassette-bytes"},
        )
        assert p_old.execution_cache_hash() == p_new.execution_cache_hash()

    def test_hash_invariant_under_missing_to_present_cassette(self):
        """First build (cassette missing) must produce the same hash as
        the second build (cassette present)."""
        p_first = self._payload(
            http_replay_mode="new-episodes",
            http_replay_cassette_name="slides.http-cassette.yaml",
            other_files={},  # cassette not on disk yet
        )
        p_second = self._payload(
            http_replay_mode="new-episodes",
            http_replay_cassette_name="slides.http-cassette.yaml",
            other_files={"slides.http-cassette.yaml": b"recorded-interactions"},
        )
        assert p_first.execution_cache_hash() == p_second.execution_cache_hash()

    def test_hash_invariant_across_replay_mode(self):
        """The hash must not depend on whether replay is on, since the
        cache key is over source data only."""
        p_plain = self._payload()
        p_replay = self._payload(
            http_replay_mode="replay",
            http_replay_cassette_name="slides.http-cassette.yaml",
            other_files={"slides.http-cassette.yaml": b"cassette"},
        )
        assert p_plain.execution_cache_hash() == p_replay.execution_cache_hash()


class TestNotebookResult:
    """Test NotebookResult class."""

    @pytest.fixture
    def sample_result(self):
        """Create a sample NotebookResult for testing."""
        return NotebookResult(
            correlation_id="test-123",
            output_file="/path/to/output.html",
            input_file="/path/to/input.ipynb",
            content_hash="abc123def456",
            result="<html><body>Notebook content</body></html>",
            output_metadata_tags=("completed", "python", "en", "html"),
        )

    def test_result_creation(self, sample_result):
        """Should create NotebookResult with all fields."""
        assert sample_result.correlation_id == "test-123"
        assert sample_result.result_type == "result"
        assert sample_result.content_hash == "abc123def456"

    def test_result_bytes(self, sample_result):
        """Should return result as UTF-8 encoded bytes."""
        result_bytes = sample_result.result_bytes()

        assert isinstance(result_bytes, bytes)
        assert result_bytes == b"<html><body>Notebook content</body></html>"

    def test_output_metadata(self, sample_result):
        """Should return metadata tags joined by colon."""
        assert sample_result.output_metadata() == "completed:python:en:html"

    def test_output_metadata_tags_tuple(self, sample_result):
        """Should have output_metadata_tags as tuple."""
        tags = sample_result.output_metadata_tags

        assert isinstance(tags, tuple)
        assert len(tags) == 4
        assert tags == ("completed", "python", "en", "html")
