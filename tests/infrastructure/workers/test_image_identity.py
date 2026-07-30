"""Tests for :mod:`clm.infrastructure.workers.image_identity` (issue #744)."""

import pytest

from clm.infrastructure.config import DEFAULT_WORKER_IMAGES
from clm.infrastructure.messaging.drawio_classes import DrawioPayload
from clm.infrastructure.messaging.plantuml_classes import PlantUmlPayload
from clm.infrastructure.workers.image_identity import (
    effective_worker_image_identity,
    reset_effective_worker_identities,
    set_effective_worker_identities,
    worker_image_identity_for,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_effective_worker_identities()
    yield
    reset_effective_worker_identities()


class TestPerTypeIdentity:
    def test_bare_defaults_resolve_per_service(self):
        """The docker default expands against each service's OWN repo."""
        assert worker_image_identity_for("docker", None, "plantuml") == (
            f"docker:{DEFAULT_WORKER_IMAGES['plantuml']}"
        )
        assert worker_image_identity_for("docker", None, "drawio") == (
            f"docker:{DEFAULT_WORKER_IMAGES['drawio']}"
        )

    def test_direct_mode_per_type(self):
        """A configured-but-unused Docker image must not leak into the
        direct identity (which since #747 may carry a binary fingerprint)."""
        ident = worker_image_identity_for("direct", "ignored:1", "drawio")
        assert ident.startswith("direct")
        assert "ignored" not in ident


class TestEffectiveRegistry:
    def test_set_records_post_override_identities(self):
        """The #744 wiring: a build's resolved config (with CLI overrides
        applied to the copy) is what the cache keys must see."""
        from clm.infrastructure.workers.config_loader import load_worker_config

        worker_config = load_worker_config(
            {
                "workers": "docker",
                "plantuml_image": "test",
                "drawio_image": "candidate:1.2",
            }
        )
        set_effective_worker_identities(worker_config)
        assert effective_worker_image_identity("plantuml") == (
            "docker:docker.io/mhoelzl/clm-plantuml-converter:test"
        )
        assert effective_worker_image_identity("drawio") == "docker:candidate:1.2"

    def test_fallback_without_recording_matches_singleton_path(self):
        ident = effective_worker_image_identity("plantuml")
        assert ident == "" or ident.startswith(("direct", "docker:"))


class TestDiagramPayloadIdentity:
    """Issue #744 hole (a): the diagram caches key on the payload
    content_hash — a different converter image must be a cache MISS for
    the same source."""

    def _payload(self, cls, identity: str):
        return cls(
            data="@startuml\nA -> B\n@enduml",
            correlation_id="c1",
            input_file="a/x.puml",
            input_file_name="x.puml",
            output_file="out/x.png",
            output_file_name="x.png",
            worker_image_identity=identity,
        )

    def test_same_source_different_image_is_a_miss(self):
        old = self._payload(PlantUmlPayload, "docker:conv:1.0").content_hash()
        new = self._payload(PlantUmlPayload, "docker:conv:2.0").content_hash()
        assert old != new

    def test_same_source_same_image_is_stable(self):
        a = self._payload(DrawioPayload, "docker:conv:1.0").content_hash()
        b = self._payload(DrawioPayload, "docker:conv:1.0").content_hash()
        assert a == b

    def test_output_format_is_part_of_the_key(self):
        png = self._payload(PlantUmlPayload, "direct")
        svg = self._payload(PlantUmlPayload, "direct").model_copy(update={"output_format": "svg"})
        assert png.content_hash() != svg.content_hash()

    def test_identityless_payload_still_hashes(self):
        """Pre-#744 constructors (default identity) stay valid — degraded
        keying for that payload only."""
        p = PlantUmlPayload(
            data="@startuml\nA -> B\n@enduml",
            correlation_id="c1",
            input_file="a/x.puml",
            input_file_name="x.puml",
            output_file="out/x.png",
            output_file_name="x.png",
        )
        assert p.content_hash()


class TestDirectModeDiagramFingerprint:
    """Issue #747: direct-mode diagram identities fingerprint the binary a
    direct build will execute — a JAR/executable upgrade invalidates the
    diagram caches like a Docker image switch does."""

    def test_jar_change_changes_the_identity(self, tmp_path, monkeypatch):
        jar = tmp_path / "plantuml.jar"
        jar.write_bytes(b"v1")
        monkeypatch.setenv("PLANTUML_JAR", str(jar))
        first = worker_image_identity_for("direct", None, "plantuml")
        assert first.startswith("direct:") and first != "direct"

        jar.write_bytes(b"v2 bigger")
        second = worker_image_identity_for("direct", None, "plantuml")
        assert second.startswith("direct:")
        assert first != second

    def test_same_binary_is_stable(self, tmp_path, monkeypatch):
        exe = tmp_path / "drawio.exe"
        exe.write_bytes(b"binary")
        monkeypatch.setenv("DRAWIO_EXECUTABLE", str(exe))
        a = worker_image_identity_for("direct", None, "drawio")
        b = worker_image_identity_for("direct", None, "drawio")
        assert a == b and a.startswith("direct:")

    def test_unlocatable_binary_degrades_to_plain_direct(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PLANTUML_JAR", str(tmp_path / "missing.jar"))
        assert worker_image_identity_for("direct", None, "plantuml") == "direct"

    def test_notebook_direct_identity_is_unchanged(self):
        assert worker_image_identity_for("direct", None, "notebook") == "direct"

    def test_docker_mode_unaffected(self, monkeypatch):
        monkeypatch.setenv("PLANTUML_JAR", "ignored")
        assert worker_image_identity_for("docker", "img:1", "plantuml") == "docker:img:1"
