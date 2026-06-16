"""Tests for clm.slides.sidecar_layout (course-wide sidecar-layout default)."""

from __future__ import annotations

import pytest

from clm.slides.sidecar_layout import (
    effective_write_layout,
    resolve_course_sidecar_default,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("CLM_SIDECAR_LAYOUT", raising=False)


def test_default_none_when_unconfigured(tmp_path):
    assert resolve_course_sidecar_default(tmp_path / "slides_x.py") is None


def test_env_var_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CLM_SIDECAR_LAYOUT", "subdir")
    assert resolve_course_sidecar_default(tmp_path / "slides_x.py") == "subdir"


def test_env_invalid_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("CLM_SIDECAR_LAYOUT", "bogus")
    assert resolve_course_sidecar_default(tmp_path / "slides_x.py") is None


def test_pyproject_walk_up(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.clm]\nsidecar-layout = "subdir"\n', encoding="utf-8"
    )
    deep = tmp_path / "slides" / "module_1" / "topic_1"
    deep.mkdir(parents=True)
    assert resolve_course_sidecar_default(deep / "slides_x.py") == "subdir"


def test_env_overrides_pyproject(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.clm]\nsidecar-layout = "sibling"\n', encoding="utf-8"
    )
    monkeypatch.setenv("CLM_SIDECAR_LAYOUT", "subdir")
    assert resolve_course_sidecar_default(tmp_path / "slides_x.py") == "subdir"


def test_nearest_pyproject_without_key_stops(tmp_path):
    # An outer pyproject sets subdir; a nearer inner one lacks the key — the
    # nearer one wins and yields None (the outer value does not leak in).
    (tmp_path / "pyproject.toml").write_text(
        '[tool.clm]\nsidecar-layout = "subdir"\n', encoding="utf-8"
    )
    inner = tmp_path / "course"
    inner.mkdir()
    (inner / "pyproject.toml").write_text('[tool.clm]\ncache_dir = ".x"\n', encoding="utf-8")
    assert resolve_course_sidecar_default(inner / "slides_x.py") is None


class TestEffectiveWriteLayout:
    def test_flag_wins_over_course_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_SIDECAR_LAYOUT", "subdir")
        assert effective_write_layout(tmp_path / "slides_x.py", "sibling") == "sibling"

    def test_course_subdir_forces_subdir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_SIDECAR_LAYOUT", "subdir")
        assert effective_write_layout(tmp_path / "slides_x.py", None) == "subdir"

    def test_course_sibling_forces_sibling(self, tmp_path, monkeypatch):
        # An explicit ``sibling`` course default is now forced: the auto path
        # leans subdir for a new companion, so ``sibling`` must be honoured.
        monkeypatch.setenv("CLM_SIDECAR_LAYOUT", "sibling")
        assert effective_write_layout(tmp_path / "slides_x.py", None) == "sibling"

    def test_no_config_is_auto(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLM_SIDECAR_LAYOUT", raising=False)
        assert effective_write_layout(tmp_path / "slides_x.py", None) is None


def test_cli_extract_honors_env_default(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from clm.cli.main import cli

    monkeypatch.setenv("CLM_SIDECAR_LAYOUT", "subdir")
    slide = tmp_path / "slides_intro.py"
    slide.write_text(
        '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n'
        "# ## T\n"
        '# %% [markdown] lang="de" tags=["voiceover"]\n'
        "# vo\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli, ["voiceover", "extract", str(slide)])
    assert result.exit_code == 0, result.output
    # No --layout flag, no pre-existing voiceover/ dir, but the course default
    # steers the new companion into voiceover/.
    assert (tmp_path / "voiceover" / "voiceover_intro.py").exists()
    assert not (tmp_path / "voiceover_intro.py").exists()
