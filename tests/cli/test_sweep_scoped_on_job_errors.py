"""``_maybe_run_sweep`` scopes, rather than skips, on per-job errors (#923).

Any recorded error used to disable the whole stray-file sweep, on the
argument that the write registry is missing the writes that never
happened. That is true only for the failed jobs' own outputs — but the
skip was all-or-nothing, so after a spec restructure one failing notebook
left the previous revision's decks duplicated across every output tier.

The orchestrator now distinguishes:

- **job-scoped** errors — every error names the output it failed to
  write (``details["output_file"]``, stamped by the backend for failed,
  orphaned and cache-replayed failures) and none is fatal, and the build
  neither aborted nor timed out: the sweep RUNS with those outputs
  protected (directory + same-named files elsewhere);
- anything else — a fatal ``build_aborted`` record, an error with no
  output attribution (course-load, cross-reference, image collisions), a
  timed-out or aborted build: the registry's gaps are unknowable, so the
  sweep still skips wholesale with the #923 notice.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from clm.build.config import BuildConfig
from clm.core.build_data_classes import BuildError


def _make_config(tmp_path: Path) -> BuildConfig:
    return BuildConfig(
        spec_file=Path("spec.xml"),
        data_dir=Path("data"),
        output_dir=tmp_path,
        log_level="INFO",
        cache_db_path=Path("cache.db"),
        jobs_db_path=Path("jobs.db"),
        ignore_cache=False,
        clear_cache=False,
        watch=False,
        print_correlation_ids=False,
        workers=None,
        notebook_workers=None,
        plantuml_workers=None,
        drawio_workers=None,
        notebook_image=None,
        plantuml_image=None,
        drawio_image=None,
        sweep=True,
    )


def _job_error(output_file: str | None, *, severity: str = "error", **details) -> BuildError:
    d = dict(details)
    if output_file is not None:
        d["output_file"] = output_file
    return BuildError(
        error_type="user",
        category="cell_execution",
        severity=severity,  # type: ignore[arg-type]
        file_path="/src/slides_010.py",
        message="NameError: x",
        actionable_guidance="fix it",
        job_id=7,
        details=d,
    )


def _reporter(errors, *, timed_out: bool = False, aborted: bool = False):
    reporter = MagicMock()
    reporter.errors = list(errors)
    reporter.timed_out = timed_out
    reporter.aborted = aborted
    return reporter


def _backend():
    from clm.core.image_registry import ImageRegistry
    from clm.core.output_write_registry import OutputWriteRegistry

    return SimpleNamespace(
        output_write_registry=OutputWriteRegistry(), image_registry=ImageRegistry()
    )


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    from clm.build import output_sweep as sweep_module

    calls: list[dict] = []

    def recorder(root_dirs, registry, image_registry=None, *, skip_reason=None, **kwargs):
        calls.append({"skip_reason": skip_reason, **kwargs})
        return sweep_module.SweepReport(skipped=skip_reason is not None, skip_reason=skip_reason)

    monkeypatch.setattr(sweep_module, "sweep_stray_files", recorder)
    return calls


def _run(config, reporter):
    from clm.build.engine import _maybe_run_sweep

    _maybe_run_sweep(
        config=config,
        root_dirs=[config.output_dir],
        backend=_backend(),
        build_reporter=reporter,
        only_sections_mode=False,
    )


def _messages(reporter) -> list[str]:
    return [c.args[0] for c in reporter.formatter.show_startup_message.call_args_list]


def test_job_scoped_errors_run_the_sweep_with_their_outputs_protected(tmp_path, spy):
    """Regression test for #923: one failed notebook no longer disables
    the sweep; its output is protected and everything else is swept."""
    errors = [
        _job_error(str(tmp_path / "en" / "week2" / "failed.ipynb")),
        _job_error(str(tmp_path / "de" / "week2" / "failed.ipynb")),
        # The same job reported twice (once per target) — deduplicated.
        _job_error(str(tmp_path / "de" / "week2" / "failed.ipynb")),
    ]
    reporter = _reporter(errors)

    _run(_make_config(tmp_path), reporter)

    assert len(spy) == 1
    assert spy[0]["skip_reason"] is None
    assert set(spy[0]["failed_outputs"]) == {
        tmp_path / "en" / "week2" / "failed.ipynb",
        tmp_path / "de" / "week2" / "failed.ipynb",
    }
    (message,) = _messages(reporter)
    assert "NOT swept" not in message
    assert "2 output" in message and "failed" in message.lower()


def test_replayed_cached_error_without_output_keeps_the_wholesale_skip(tmp_path, spy):
    """A cached-issue replay is a cache HIT whose output was written; the
    backend deliberately does not stamp ``output_file`` on it, so a replayed
    error is not job-scoped and the old conservatism stands."""
    errors = [_job_error(None, from_cache=True)]
    _run(_make_config(tmp_path), _reporter(errors))
    assert spy[0]["skip_reason"] is not None


def test_fatal_error_keeps_the_wholesale_skip(tmp_path, spy):
    errors = [
        _job_error(str(tmp_path / "x.ipynb")),
        BuildError(
            error_type="infrastructure",
            category="build_aborted",
            severity="fatal",
            file_path="",
            message="boom",
            actionable_guidance="",
        ),
    ]
    reporter = _reporter(errors)
    _run(_make_config(tmp_path), reporter)
    assert spy[0]["skip_reason"] is not None and "error" in spy[0]["skip_reason"]
    assert "failed_outputs" not in spy[0] or not spy[0]["failed_outputs"]
    assert any("NOT swept" in m for m in _messages(reporter))


def test_error_without_output_attribution_keeps_the_wholesale_skip(tmp_path, spy):
    """A course-load / xref / collision error names no output; the gaps in
    the registry are unknowable, so the old conservatism stands."""
    errors = [_job_error(str(tmp_path / "x.ipynb")), _job_error(None)]
    _run(_make_config(tmp_path), _reporter(errors))
    assert spy[0]["skip_reason"] is not None


def test_timed_out_build_keeps_the_wholesale_skip(tmp_path, spy):
    """A job timeout records only the orphaned jobs (each with an output),
    but later stages never submitted anything — their outputs would look
    stray. The timed-out flag must veto the scoped sweep."""
    errors = [_job_error(str(tmp_path / "x.ipynb"))]
    _run(_make_config(tmp_path), _reporter(errors, timed_out=True))
    assert spy[0]["skip_reason"] is not None


def test_aborted_build_keeps_the_wholesale_skip(tmp_path, spy):
    errors = [_job_error(str(tmp_path / "x.ipynb"))]
    _run(_make_config(tmp_path), _reporter(errors, aborted=True))
    assert spy[0]["skip_reason"] is not None


def test_no_errors_runs_unscoped(tmp_path, spy):
    _run(_make_config(tmp_path), _reporter([]))
    assert spy[0]["skip_reason"] is None
    assert not spy[0].get("failed_outputs")
