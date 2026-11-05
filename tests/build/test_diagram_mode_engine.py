"""Engine wiring of the diagram worker default (issue #1143)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from clm.build.config import BuildConfig
from clm.build.engine import configure_workers, resolve_diagram_worker_modes
from clm.core.course import Course
from clm.core.course_spec import CourseSpec
from clm.core.worker_identity import effective_worker_image_identity
from clm.infrastructure.config import DEFAULT_WORKER_IMAGES
from clm.infrastructure.database.db_operations import DatabaseManager

PROBE = "clm.infrastructure.workers.diagram_mode.probe_docker_images"

SPEC = """<course>
  <github><de>https://github.com/x/y-de</de><en>https://github.com/x/y-en</en></github>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <description><de>d</de><en>d</en></description>
  <certificate><de>c</de><en>c</en></certificate>
  <sections><section><name><de>A</de><en>A</en></name><topics>
    <topic>demo</topic>
  </topics></section></sections>
</course>
"""


@pytest.fixture(autouse=True)
def _auto_is_the_setting(monkeypatch):
    """The suite pins ``direct`` for hermeticity; these tests exercise the default."""
    from clm.infrastructure import config as config_module

    monkeypatch.delenv("CLM_WORKER_MANAGEMENT__DIAGRAM_MODE", raising=False)
    config_module.get_config(reload=True)
    yield
    monkeypatch.undo()
    config_module.get_config(reload=True)


def _course(tmp_path: Path, *, drawio: bool = True, plantuml: bool = False) -> Course:
    root = tmp_path / "course"
    topic = root / "slides" / "module_010_x" / "topic_010_demo"
    topic.mkdir(parents=True)
    if drawio:
        (topic / "drawio").mkdir()
        (topic / "drawio" / "d.drawio").write_text("<mxfile/>", encoding="utf-8")
    if plantuml:
        (topic / "pu").mkdir()
        (topic / "pu" / "p.pu").write_text("@startuml\n@enduml\n", encoding="utf-8")
    spec_file = root / "spec.xml"
    spec_file.write_text(SPEC, encoding="utf-8")
    return Course.from_spec(CourseSpec.from_file(spec_file), root, tmp_path / "out")


def _config(tmp_path: Path, **overrides) -> BuildConfig:
    defaults: dict = {
        "spec_file": tmp_path / "course" / "spec.xml",
        "data_dir": tmp_path / "course",
        "output_dir": tmp_path / "out",
        "log_level": "INFO",
        "cache_db_path": tmp_path / "cache.db",
        "jobs_db_path": tmp_path / "jobs.db",
        "ignore_cache": False,
        "clear_cache": False,
        "watch": False,
        "print_correlation_ids": False,
        "workers": None,
        "notebook_workers": None,
        "plantuml_workers": None,
        "drawio_workers": None,
        "notebook_image": None,
        "plantuml_image": None,
        "drawio_image": None,
    }
    defaults.update(overrides)
    return BuildConfig(**defaults)


def _resolve(tmp_path: Path, course: Course, **overrides):
    config = _config(tmp_path, **overrides)
    worker_config = configure_workers(config)
    decision = asyncio.run(
        resolve_diagram_worker_modes(config, course, tmp_path / "course", worker_config)
    )
    return worker_config, decision


def _ok(images):
    return dict.fromkeys(images)


class TestResolveDiagramWorkerModes:
    def test_docker_available_records_the_docker_cache_identity(self, tmp_path):
        """The cache key must describe where the diagram will really render."""
        course = _course(tmp_path)
        with patch(PROBE, side_effect=_ok):
            worker_config, _ = _resolve(tmp_path, course)
        assert worker_config.drawio.execution_mode == "docker"
        assert effective_worker_image_identity("drawio") == (
            f"docker:{DEFAULT_WORKER_IMAGES['drawio']}"
        )
        assert effective_worker_image_identity("notebook") == "direct"
        assert worker_config.get_worker_config("notebook").execution_mode == "direct"

    def test_fallback_records_the_direct_identity_and_a_visible_warning(self, tmp_path):
        course = _course(tmp_path)
        with patch(PROBE, side_effect=lambda images: dict.fromkeys(images, "daemon gone")):
            worker_config, _ = _resolve(tmp_path, course)
        assert worker_config.drawio.execution_mode == "direct"
        assert effective_worker_image_identity("drawio").startswith("direct")
        warnings = [
            w for w in course.loading_warnings if w["category"] == "diagram_worker_fallback"
        ]
        assert len(warnings) == 1
        assert "daemon gone" in warnings[0]["message"]

    def test_fallback_with_everything_cached_does_not_warn(self, tmp_path):
        """Nothing renders locally, so there is nothing to warn about."""
        course = _course(tmp_path)
        with (
            patch(PROBE, side_effect=lambda images: dict.fromkeys(images, "daemon gone")),
            patch.object(DatabaseManager, "get_result", return_value=object()),
        ):
            worker_config, _ = _resolve(tmp_path, course)
        assert worker_config.drawio.execution_mode == "direct"
        assert not course.loading_warnings

    def test_no_diagrams_in_course_never_probes(self, tmp_path):
        course = _course(tmp_path, drawio=False)
        with patch(PROBE) as probe:
            worker_config, _ = _resolve(tmp_path, course)
        probe.assert_not_called()
        assert worker_config.get_worker_config("drawio").execution_mode == "direct"

    def test_watch_probes_even_without_diagrams(self, tmp_path):
        """A diagram added during --watch must not silently render directly."""
        course = _course(tmp_path, drawio=False)
        with patch(PROBE, side_effect=_ok) as probe:
            worker_config, _ = _resolve(tmp_path, course, watch=True)
        probe.assert_called_once()
        assert worker_config.drawio.execution_mode == "docker"
        assert worker_config.plantuml.execution_mode == "docker"

    def test_no_diagrams_flag_never_probes_even_in_watch(self, tmp_path):
        course = _course(tmp_path, drawio=False)
        with patch(PROBE) as probe:
            _resolve(tmp_path, course, no_diagrams=True, watch=True)
        probe.assert_not_called()

    def test_workers_direct_flag_opts_out(self, tmp_path):
        course = _course(tmp_path)
        with patch(PROBE) as probe:
            worker_config, _ = _resolve(tmp_path, course, workers="direct")
        probe.assert_not_called()
        assert worker_config.drawio.execution_mode == "direct"
        assert not course.loading_warnings

    def test_diagram_mode_flag_opts_out(self, tmp_path):
        course = _course(tmp_path)
        with patch(PROBE) as probe:
            worker_config, _ = _resolve(tmp_path, course, diagram_mode="direct")
        probe.assert_not_called()
        assert worker_config.drawio.execution_mode == "direct"
        assert not course.loading_warnings


class TestNoContainerWithoutDiagramWork:
    """The container starts only if a diagram actually has to be rendered."""

    def test_uncached_diagram_keeps_the_worker(self, tmp_path):
        course = _course(tmp_path)
        with patch(PROBE, side_effect=_ok):
            worker_config, _ = _resolve(tmp_path, course)
        assert worker_config.drawio.count != 0

    def test_all_cached_diagrams_start_no_container(self, tmp_path):
        course = _course(tmp_path)
        with (
            patch(PROBE, side_effect=_ok),
            patch.object(DatabaseManager, "get_result", return_value=object()),
        ):
            worker_config, _ = _resolve(tmp_path, course)
        assert worker_config.drawio.execution_mode == "docker"
        assert worker_config.drawio.count == 0
        assert worker_config.get_worker_config("drawio").count == 0

    def test_one_uncached_type_keeps_only_its_worker(self, tmp_path):
        course = _course(tmp_path, plantuml=True)

        def get_result(_self, file_path, _hash, _meta):
            return None if file_path.endswith(".pu") else object()

        with (
            patch(PROBE, side_effect=_ok),
            patch.object(DatabaseManager, "get_result", new=get_result),
        ):
            worker_config, _ = _resolve(tmp_path, course)
        assert worker_config.drawio.count == 0
        assert worker_config.plantuml.count != 0

    @pytest.mark.parametrize("flag", ["ignore_cache", "clear_cache", "watch"])
    def test_flags_that_can_create_work_keep_the_worker(self, tmp_path, flag):
        course = _course(tmp_path)
        with (
            patch(PROBE, side_effect=_ok),
            patch.object(DatabaseManager, "get_result", return_value=object()),
        ):
            worker_config, _ = _resolve(tmp_path, course, **{flag: True})
        assert worker_config.drawio.count != 0

    def test_unreadable_cache_keeps_the_worker(self, tmp_path):
        """A failed check must never strand a real job with zero workers."""
        course = _course(tmp_path)
        with (
            patch(PROBE, side_effect=_ok),
            patch.object(DatabaseManager, "get_result", side_effect=RuntimeError("locked")),
        ):
            worker_config, _ = _resolve(tmp_path, course)
        assert worker_config.drawio.count != 0

    def test_explicit_docker_keeps_starting_its_worker(self, tmp_path):
        """Only the auto choice is gated: explicit modes behave as they always did."""
        course = _course(tmp_path)
        with patch.object(DatabaseManager, "get_result", return_value=object()):
            worker_config, _ = _resolve(tmp_path, course, diagram_mode="docker")
        assert worker_config.drawio.execution_mode == "docker"
        assert worker_config.drawio.count != 0

    def test_check_leaves_no_correlation_ids_behind(self, tmp_path):
        from clm.core.messaging.correlation_ids import active_correlation_ids

        course = _course(tmp_path)
        before = set(active_correlation_ids)
        with (
            patch(PROBE, side_effect=_ok),
            patch.object(DatabaseManager, "get_result", return_value=object()),
        ):
            _resolve(tmp_path, course)
        assert set(active_correlation_ids) == before
