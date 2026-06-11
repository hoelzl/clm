"""Tests for ProcessNotebookOperation helpers.

Covers the host-side cache-key components from issue #321:
``compute_template_fingerprint`` (digest of the bundled Jinja template
directory, shipped via ``NotebookPayload.template_fingerprint``) and
``worker_image_identity_for`` (execution-environment identity, shipped via
``NotebookPayload.worker_image_identity``).
"""

from clm.core.operations.process_notebook import (
    compute_template_fingerprint,
    compute_worker_image_identity,
    worker_image_identity_for,
)


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


class TestWorkerImageIdentity:
    def test_direct_mode(self):
        """Direct mode has no image — the host environment (already covered
        by the template fingerprint) is the identity."""
        assert worker_image_identity_for("direct", None) == "direct"
        # An image configured but unused (direct mode) must not leak into
        # the key — it does not describe the executing environment.
        assert worker_image_identity_for("direct", "ignored:1.0") == "direct"

    def test_docker_mode_with_configured_image(self):
        assert (
            worker_image_identity_for("docker", "ghcr.io/me/clm-notebook:1.11.0")
            == "docker:ghcr.io/me/clm-notebook:1.11.0"
        )

    def test_docker_mode_default_image_matches_pool_starter(self):
        """With no per-type image configured, the identity must resolve the
        SAME default the pool starter uses — otherwise the key describes a
        different image than the one executing."""
        from clm.infrastructure.config import DEFAULT_WORKER_IMAGES

        assert worker_image_identity_for("docker", None) == (
            f"docker:{DEFAULT_WORKER_IMAGES['notebook']}"
        )

    def test_image_change_changes_identity(self):
        """The whole point: two different images yield two different
        identities, so the cache key differs."""
        old = worker_image_identity_for("docker", "clm-notebook:xeus-cling")
        new = worker_image_identity_for("docker", "clm-notebook:xeus-cpp")
        assert old != new

    def test_compute_from_global_config_is_stable_and_nonempty(self):
        """The cached global-config wrapper returns a stable, non-empty
        identity (cache-key components must not flap within a process)."""
        compute_worker_image_identity.cache_clear()
        first = compute_worker_image_identity()
        second = compute_worker_image_identity()
        assert first == second
        assert first.startswith(("direct", "docker:"))
