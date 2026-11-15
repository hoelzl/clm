"""Default-branch reconcile in ``clm release provision`` (regression tests for #955).

A fresh GitLab project may advertise ``default_branch = main`` while
``clm git init`` pushes ``master``; provision now points the default at the
branch the channel working tree has checked out, or warns naming the setting.
All HTTP is mocked: ``httpx.request`` is patched, never called for real.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from clm.cli.commands.release import release_group
from clm.infrastructure import gitlab_api
from clm.infrastructure.gitlab_api import GitLabApiError, reconcile_default_branch

BASE = "https://gitlab.example.com"
PROJECT = "ca/ml-2026-04-solutions"

SPEC = """<?xml version="1.0"?>
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <project-slug>ml</project-slug>
  <github>
    <repository-base>https://gitlab.example.com/ca</repository-base>
  </github>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
  <output-targets>
    <output-target name="completed"><path>output/completed</path></output-target>
  </output-targets>
  <release-channels name="solutions" source-target="completed">
    <share-with access="maintainer">trainers</share-with>
    <channel name="2026-04" path="release/2026-04" ledger="release/2026-04.txt"/>
  </release-channels>
</course>
"""


def _response(status_code: int, payload=None) -> httpx.Response:
    if payload is not None:
        return httpx.Response(status_code, json=payload)
    return httpx.Response(status_code, text="")


def _run(responses):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("data")))
        return responses.pop(0)

    with patch.object(gitlab_api.httpx, "request", side_effect=fake_request):
        result = reconcile_default_branch(BASE, PROJECT, "master", "tok")
    return result, calls


class TestReconcileDefaultBranch:
    def test_already_matching_is_unchanged_and_read_only(self):
        result, calls = _run([_response(200, {"default_branch": "master"})])
        assert result == ("unchanged", "master")
        assert [c[0] for c in calls] == ["GET"]
        assert "projects/ca%2Fml-2026-04-solutions" in calls[0][1]

    def test_mismatch_is_updated_via_put(self):
        result, calls = _run(
            [
                _response(200, {"default_branch": "main"}),
                _response(200, {"name": "master"}),
                _response(200, {"default_branch": "master"}),
            ]
        )
        assert result == ("updated", "main")
        assert [c[0] for c in calls] == ["GET", "GET", "PUT"]
        assert calls[1][1].endswith("/repository/branches/master")
        assert calls[2][2] == {"default_branch": "master"}

    def test_branch_not_on_remote_is_reported_not_set(self):
        result, calls = _run([_response(200, {"default_branch": "main"}), _response(404, {})])
        assert result == ("not-pushed", "main")
        assert "PUT" not in [c[0] for c in calls]

    def test_refused_update_names_the_setting(self):
        with pytest.raises(GitLabApiError, match="Default branch") as excinfo:
            _run(
                [
                    _response(200, {"default_branch": "main"}),
                    _response(200, {"name": "master"}),
                    _response(403, {"message": "403 Forbidden"}),
                ]
            )
        assert "Maintainer" in str(excinfo.value)

    def test_missing_project_is_an_error(self):
        with pytest.raises(GitLabApiError, match="not found"):
            _run([_response(404, {})])


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_git_config():
    from clm.infrastructure.config import GitConfig

    mock_config = MagicMock()
    mock_config.git = GitConfig()
    with patch("clm.cli.commands.git.get_config", return_value=mock_config):
        yield


def _git(dest: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(dest), *args], check=True, capture_output=True)


def _course(tmp_path: Path, *, commit: bool = True) -> Path:
    specs = tmp_path / "course-specs"
    specs.mkdir()
    spec_file = specs / "course.xml"
    spec_file.write_text(SPEC, encoding="utf-8")
    dest = tmp_path / "release" / "2026-04"
    dest.mkdir(parents=True)
    _git(dest, "init", "-q", "-b", "master")
    _git(dest, "remote", "add", "origin", f"{BASE}/{PROJECT}.git")
    if commit:
        (dest / "README.md").write_text("x\n", encoding="utf-8")
        _git(dest, "add", "README.md")
        _git(
            dest,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "init",
        )
    return spec_file


class TestProvisionReconcilesDefaultBranch:
    def test_dry_run_previews_the_reconcile(self, tmp_path, monkeypatch):
        for var in ("CLM_GITLAB_TOKEN", "GITLAB_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        spec_file = _course(tmp_path)
        with patch.object(gitlab_api.httpx, "request") as no_network:
            result = CliRunner().invoke(release_group, ["provision", str(spec_file), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert f"{PROJECT} default_branch -> master" in result.output
        no_network.assert_not_called()

    def test_real_run_updates_a_mismatched_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_GITLAB_TOKEN", "tok")
        spec_file = _course(tmp_path)
        with (
            patch("clm.infrastructure.gitlab_api.share_project_with_group", return_value="shared"),
            patch(
                "clm.infrastructure.gitlab_api.reconcile_default_branch",
                return_value=("updated", "main"),
            ) as mock_reconcile,
        ):
            result = CliRunner().invoke(release_group, ["provision", str(spec_file)])
        assert result.exit_code == 0, result.output
        mock_reconcile.assert_called_once_with(BASE, PROJECT, "master", "tok")
        assert "set to 'master' (was 'main')" in result.output

    def test_refusal_warns_with_the_setting_but_exits_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_GITLAB_TOKEN", "tok")
        spec_file = _course(tmp_path)
        with (
            patch("clm.infrastructure.gitlab_api.share_project_with_group", return_value="shared"),
            patch(
                "clm.infrastructure.gitlab_api.reconcile_default_branch",
                side_effect=GitLabApiError(
                    f"refused. Change it by hand in {gitlab_api.DEFAULT_BRANCH_SETTING}"
                ),
            ),
        ):
            result = CliRunner().invoke(release_group, ["provision", str(spec_file)])
        assert result.exit_code == 0, result.output
        assert "WARNING: default branch not reconciled" in result.output
        assert "Default branch" in result.output

    def test_not_pushed_warns_with_the_setting(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_GITLAB_TOKEN", "tok")
        spec_file = _course(tmp_path)
        with (
            patch("clm.infrastructure.gitlab_api.share_project_with_group", return_value="shared"),
            patch(
                "clm.infrastructure.gitlab_api.reconcile_default_branch",
                return_value=("not-pushed", "main"),
            ),
        ):
            result = CliRunner().invoke(release_group, ["provision", str(spec_file)])
        assert result.exit_code == 0, result.output
        assert "not on the remote yet" in result.output
        assert gitlab_api.DEFAULT_BRANCH_SETTING in result.output

    def test_unborn_branch_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_GITLAB_TOKEN", "tok")
        spec_file = _course(tmp_path, commit=False)
        with (
            patch("clm.infrastructure.gitlab_api.share_project_with_group", return_value="shared"),
            patch("clm.infrastructure.gitlab_api.reconcile_default_branch") as mock_reconcile,
        ):
            result = CliRunner().invoke(release_group, ["provision", str(spec_file)])
        assert result.exit_code == 0, result.output
        mock_reconcile.assert_not_called()
