"""Diagram workers prefer Docker when it can honestly be used (issue #1143).

The same diagram source renders to different PNGs in the Docker converter image
and on the host, so ``auto`` (the default) renders in Docker when the daemon is
reachable and the image is present locally, and otherwise falls back *visibly*.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from clm.infrastructure.config import DEFAULT_WORKER_IMAGES, WorkersManagementConfig
from clm.infrastructure.workers.diagram_mode import (
    fallback_warning_message,
    probe_docker_images,
    resolve_diagram_execution_modes,
)

BOTH = frozenset({"plantuml", "drawio"})


def _probe_ok(images):
    return dict.fromkeys(images)


def _probe_down(images):
    return dict.fromkeys(images, "the Docker daemon is not reachable (boom)")


def _modes(cfg: WorkersManagementConfig) -> tuple[str | None, str | None, str | None]:
    return (cfg.notebook.execution_mode, cfg.plantuml.execution_mode, cfg.drawio.execution_mode)


class TestConfigDefault:
    def test_default_is_auto(self):
        assert WorkersManagementConfig().diagram_mode == "auto"

    @pytest.mark.parametrize("value", ["auto", "docker", "direct"])
    def test_accepts_the_three_modes(self, value):
        assert WorkersManagementConfig(diagram_mode=value).diagram_mode == value

    def test_rejects_anything_else(self):
        with pytest.raises(ValueError, match="diagram_mode"):
            WorkersManagementConfig(diagram_mode="sometimes")

    def test_env_var_reaches_the_field(self, monkeypatch):
        from clm.infrastructure.config import ClmConfig

        monkeypatch.setenv("CLM_WORKER_MANAGEMENT__DIAGRAM_MODE", "docker")
        assert ClmConfig().worker_management.diagram_mode == "docker"


class TestAuto:
    def test_docker_up_and_image_present_picks_docker_for_diagrams_only(self):
        cfg = WorkersManagementConfig()
        decision = resolve_diagram_execution_modes(cfg, types_present=BOTH, probe=_probe_ok)
        assert _modes(cfg) == (None, "docker", "docker")
        assert cfg.get_worker_config("notebook").execution_mode == "direct"
        assert cfg.get_worker_config("drawio").image == DEFAULT_WORKER_IMAGES["drawio"]
        assert decision.auto_docker == {"plantuml", "drawio"}
        assert decision.fallbacks == {}

    def test_daemon_down_falls_back_with_a_named_reason(self):
        cfg = WorkersManagementConfig()
        decision = resolve_diagram_execution_modes(cfg, types_present=BOTH, probe=_probe_down)
        assert _modes(cfg) == (None, "direct", "direct")
        assert decision.auto_docker == set()
        assert "daemon is not reachable" in decision.fallbacks["drawio"]
        assert "daemon is not reachable" in decision.fallbacks["plantuml"]

    def test_one_missing_image_falls_back_only_that_type(self):
        def probe(images):
            return {t: ("image missing" if t == "plantuml" else None) for t in images}

        cfg = WorkersManagementConfig()
        decision = resolve_diagram_execution_modes(cfg, types_present=BOTH, probe=probe)
        assert _modes(cfg) == (None, "direct", "docker")
        assert decision.fallbacks == {"plantuml": "image missing"}

    def test_zero_diagrams_never_touches_docker(self):
        """A course without diagrams must not pay for a daemon round trip."""
        probe = MagicMock()
        cfg = WorkersManagementConfig()
        decision = resolve_diagram_execution_modes(cfg, types_present=frozenset(), probe=probe)
        probe.assert_not_called()
        assert _modes(cfg) == (None, None, None)
        assert cfg.get_worker_config("drawio").execution_mode == "direct"
        assert decision.fallbacks == {}

    def test_only_the_type_present_is_probed(self):
        seen = []

        def probe(images):
            seen.append(dict(images))
            return dict.fromkeys(images)

        cfg = WorkersManagementConfig()
        resolve_diagram_execution_modes(cfg, types_present=frozenset({"drawio"}), probe=probe)
        assert seen == [{"drawio": DEFAULT_WORKER_IMAGES["drawio"]}]
        assert _modes(cfg) == (None, None, "docker")

    def test_probes_the_override_image(self):
        seen = {}

        def probe(images):
            seen.update(images)
            return dict.fromkeys(images)

        cfg = WorkersManagementConfig()
        cfg.drawio.image = "reg.example/custom:9"
        resolve_diagram_execution_modes(cfg, types_present=frozenset({"drawio"}), probe=probe)
        assert seen == {"drawio": "reg.example/custom:9"}

    def test_zero_worker_count_keeps_the_cache_identity_of_a_normal_build(self):
        """``--drawio-workers 0`` must replay what a normal build cached, not miss it."""
        cfg = WorkersManagementConfig()
        cfg.drawio.count = 0
        resolve_diagram_execution_modes(cfg, types_present=frozenset({"drawio"}), probe=_probe_ok)
        assert cfg.drawio.execution_mode == "docker"

    def test_auto_start_disabled_keeps_the_inherited_mode(self):
        """Persistent workers were started by the operator in some mode.

        Tagging the build's jobs ``docker`` would leave them unclaimable by a
        Direct pool that the build does not manage — a stall, not a render.
        """
        probe = MagicMock()
        cfg = WorkersManagementConfig(auto_start=False)
        decision = resolve_diagram_execution_modes(cfg, types_present=BOTH, probe=probe)
        probe.assert_not_called()
        assert _modes(cfg) == (None, None, None)
        assert decision.auto_docker == set()

    def test_whole_volume_mount_root_falls_back_instead_of_failing(self, tmp_path):
        """An output root at a drive root works in Direct mode; auto must not break it."""
        probe = MagicMock()
        drive_root = Path(tmp_path.anchor)
        cfg = WorkersManagementConfig()
        decision = resolve_diagram_execution_modes(
            cfg,
            types_present=BOTH,
            mount_roots=[(tmp_path, "data directory"), (drive_root, "workspace directory")],
            probe=probe,
        )
        probe.assert_not_called()
        assert _modes(cfg) == (None, "direct", "direct")
        assert "whole volume" in decision.fallbacks["drawio"]

    def test_default_docker_mode_stays_strict(self):
        """``default_execution_mode=docker`` keeps its no-fallback meaning."""
        probe = MagicMock()
        cfg = WorkersManagementConfig(default_execution_mode="docker")
        decision = resolve_diagram_execution_modes(cfg, types_present=BOTH, probe=probe)
        probe.assert_not_called()
        assert cfg.get_worker_config("drawio").execution_mode == "docker"
        assert decision.fallbacks == {}


class TestPrecedence:
    """CLI > per-type config > --workers > diagram_mode setting (env/config) > default."""

    def test_setting_direct_is_the_opt_out(self):
        probe = MagicMock()
        cfg = WorkersManagementConfig(diagram_mode="direct")
        decision = resolve_diagram_execution_modes(cfg, types_present=BOTH, probe=probe)
        probe.assert_not_called()
        assert _modes(cfg) == (None, "direct", "direct")
        assert decision.fallbacks == {}

    def test_setting_docker_is_strict(self):
        probe = MagicMock()
        cfg = WorkersManagementConfig(diagram_mode="docker")
        decision = resolve_diagram_execution_modes(cfg, types_present=BOTH, probe=probe)
        probe.assert_not_called()
        assert _modes(cfg) == (None, "docker", "docker")
        assert decision.auto_docker == set()

    def test_workers_direct_flag_is_an_opt_out(self):
        probe = MagicMock()
        cfg = WorkersManagementConfig()
        resolve_diagram_execution_modes(cfg, types_present=BOTH, cli_workers="direct", probe=probe)
        probe.assert_not_called()
        assert _modes(cfg) == (None, "direct", "direct")

    def test_workers_flag_beats_the_setting(self):
        cfg = WorkersManagementConfig(diagram_mode="docker")
        resolve_diagram_execution_modes(cfg, types_present=BOTH, cli_workers="direct")
        assert _modes(cfg) == (None, "direct", "direct")

    def test_per_type_config_beats_workers_flag_and_setting(self):
        """The contract CppCourses' clm.toml relies on is unchanged."""
        probe = MagicMock()
        cfg = WorkersManagementConfig(diagram_mode="direct")
        cfg.drawio.execution_mode = "docker"
        resolve_diagram_execution_modes(cfg, types_present=BOTH, cli_workers="direct", probe=probe)
        assert cfg.drawio.execution_mode == "docker"
        assert cfg.plantuml.execution_mode == "direct"

    def test_per_type_config_is_not_probed_in_auto(self):
        seen = {}

        def probe(images):
            seen.update(images)
            return dict.fromkeys(images)

        cfg = WorkersManagementConfig()
        cfg.plantuml.execution_mode = "direct"
        resolve_diagram_execution_modes(cfg, types_present=BOTH, probe=probe)
        assert set(seen) == {"drawio"}
        assert cfg.plantuml.execution_mode == "direct"

    @pytest.mark.parametrize("mode", ["direct", "docker"])
    def test_cli_flag_beats_everything(self, mode):
        probe = MagicMock()
        other = "docker" if mode == "direct" else "direct"
        cfg = WorkersManagementConfig(diagram_mode=other)
        cfg.drawio.execution_mode = other
        resolve_diagram_execution_modes(
            cfg, types_present=BOTH, cli_diagram_mode=mode, cli_workers=other, probe=probe
        )
        probe.assert_not_called()
        assert _modes(cfg) == (None, mode, mode)

    def test_cli_auto_beats_a_workers_flag_and_setting(self):
        cfg = WorkersManagementConfig(diagram_mode="direct", default_execution_mode="docker")
        decision = resolve_diagram_execution_modes(
            cfg,
            types_present=BOTH,
            cli_diagram_mode="auto",
            cli_workers="docker",
            probe=_probe_down,
        )
        # Explicit auto opts back in to the fallback even with --workers docker.
        assert _modes(cfg) == (None, "direct", "direct")
        assert set(decision.fallbacks) == {"plantuml", "drawio"}


class TestProbe:
    def _docker(self, client: MagicMock) -> SimpleNamespace:
        class ImageNotFound(Exception):
            pass

        errors = SimpleNamespace(ImageNotFound=ImageNotFound)
        return SimpleNamespace(from_env=MagicMock(return_value=client), errors=errors)

    @contextmanager
    def _patched(self, module):
        with (
            patch.dict(sys.modules, {"docker": module, "docker.errors": module.errors}),
            patch("clm.infrastructure.workers.diagram_mode.ensure_docker_worker_deps"),
        ):
            yield

    def test_all_present(self):
        client = MagicMock()
        module = self._docker(client)
        with self._patched(module):
            result = probe_docker_images({"drawio": "a:1", "plantuml": "b:1"})
        assert result == {"drawio": None, "plantuml": None}
        assert module.from_env.call_args.kwargs["timeout"] <= 10
        client.ping.assert_called_once()
        client.close.assert_called_once()

    def test_missing_image_names_the_image_and_the_remedy_and_never_pulls(self):
        client = MagicMock()
        module = self._docker(client)
        client.images.get.side_effect = module.errors.ImageNotFound("nope")
        with self._patched(module):
            result = probe_docker_images({"drawio": "reg/img:1"})
        assert "reg/img:1" in result["drawio"]
        assert "docker pull reg/img:1" in result["drawio"]
        client.images.pull.assert_not_called()
        client.close.assert_called_once()

    def test_daemon_down_reports_for_every_type(self):
        module = self._docker(MagicMock())
        module.from_env.side_effect = RuntimeError("no such pipe")
        with self._patched(module):
            result = probe_docker_images({"drawio": "a:1", "plantuml": "b:1"})
        assert "not reachable" in result["drawio"]
        assert "no such pipe" in result["plantuml"]

    def test_ping_failure_is_a_daemon_failure_and_closes_the_client(self):
        client = MagicMock()
        client.ping.side_effect = RuntimeError("500")
        with self._patched(self._docker(client)):
            result = probe_docker_images({"drawio": "a:1"})
        assert "not reachable" in result["drawio"]
        client.close.assert_called_once()

    def test_unexpected_inspect_error_is_a_reason_not_a_crash(self):
        client = MagicMock()
        client.images.get.side_effect = RuntimeError("weird")
        with self._patched(self._docker(client)):
            result = probe_docker_images({"drawio": "a:1"})
        assert "a:1" in result["drawio"]
        assert "weird" in result["drawio"]

    def test_missing_extra_names_the_install_hint(self):
        with patch(
            "clm.infrastructure.workers.diagram_mode.ensure_docker_worker_deps",
            side_effect=RuntimeError("missing package(s): docker. pip install x"),
        ):
            result = probe_docker_images({"drawio": "a:1"})
        assert "missing package(s): docker" in result["drawio"]


class TestWarningMessage:
    def test_names_type_reason_consequence_and_opt_out(self):
        message = fallback_warning_message("drawio", "the Docker daemon is not reachable (x)")
        assert "Draw.io" in message
        assert "the Docker daemon is not reachable (x)" in message
        assert "img-generated" in message
        assert "--diagram-mode direct" in message
        assert "CLM_WORKER_MANAGEMENT__DIAGRAM_MODE" in message
