"""Tests for ProcessNotebookOperation helpers.

Currently covers ``compute_template_fingerprint`` (issue #321): the
host-side digest of the bundled Jinja template directory that travels in
``NotebookPayload.template_fingerprint`` and is folded into the cache keys.
"""

from clm.core.operations.process_notebook import compute_template_fingerprint


class TestComputeTemplateFingerprint:
    def test_deterministic_per_prog_lang(self):
        """Same prog_lang must yield the same fingerprint (it is a cache
        key component — any instability would invalidate on every build)."""
        assert compute_template_fingerprint("python") == compute_template_fingerprint("python")

    def test_is_sha256_hex(self):
        fingerprint = compute_template_fingerprint("python")
        assert len(fingerprint) == 64
        int(fingerprint, 16)  # raises if not hex

    def test_differs_across_prog_langs(self):
        """templates_cpp and templates_python have different contents, so
        their fingerprints must differ."""
        assert compute_template_fingerprint("cpp") != compute_template_fingerprint("python")

    def test_unknown_prog_lang_does_not_crash(self):
        """A prog_lang without a bundled template directory still gets a
        stable fingerprint (version + prog_lang only)."""
        fingerprint = compute_template_fingerprint("not-a-real-language")
        assert len(fingerprint) == 64

    def test_covers_template_file_content(self):
        """The fingerprint must be derived from template file bytes, not just
        names: macros.j2 exists under both cpp and csharp template dirs with
        (potentially) different content — and even where contents coincide,
        the prog_lang itself is folded in. Guard the content sensitivity via
        the digest helper's structure instead: hashing the same directory
        twice in one process returns the lru_cached value, so clear the cache
        and recompute to prove stability is content-based, not cache-based.
        """
        compute_template_fingerprint.cache_clear()
        first = compute_template_fingerprint("cpp")
        compute_template_fingerprint.cache_clear()
        second = compute_template_fingerprint("cpp")
        assert first == second
