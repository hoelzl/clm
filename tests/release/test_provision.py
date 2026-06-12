"""Tests for ``clm release provision`` and the GitLab share helper (issue #294)."""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from clm.cli.commands.release import release_group
from clm.core.course_spec import CourseSpec
from clm.infrastructure import gitlab_api
from clm.infrastructure.gitlab_api import (
    GitLabApiError,
    parse_gitlab_remote,
    share_project_with_group,
)

# ---------------------------------------------------------------------------
# Spec parsing: <share-with>
# ---------------------------------------------------------------------------

SPEC_WITH_SHARES = """<?xml version="1.0"?>
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
    <channel name="2026-04" path="release/2026-04" ledger="release/2026-04.txt">
      <share-with>students/azav-ml/ml-2026-04</share-with>
    </channel>
    <channel name="2026-10" path="release/2026-10" ledger="release/2026-10.txt">
      <share-with access="guest">trainers</share-with>
    </channel>
  </release-channels>
</course>
"""


def _spec() -> CourseSpec:
    return CourseSpec.from_file(io.StringIO(SPEC_WITH_SHARES))


class TestShareWithParsing:
    def test_channel_inherits_block_shares_and_adds_its_own(self):
        block = _spec().release_channel_blocks[0]
        ch = block.channel("2026-04")
        assert [(s.group, s.access) for s in ch.share_with] == [
            ("trainers", "maintainer"),
            ("students/azav-ml/ml-2026-04", "reporter"),  # default access
        ]

    def test_channel_level_entry_overrides_inherited_access(self):
        block = _spec().release_channel_blocks[0]
        ch = block.channel("2026-10")
        assert [(s.group, s.access) for s in ch.share_with] == [("trainers", "guest")]

    def test_invalid_access_is_a_validation_error(self):
        bad = SPEC_WITH_SHARES.replace('access="maintainer"', 'access="owner"')
        errors = CourseSpec.from_file(io.StringIO(bad)).validate()
        assert any("invalid share-with access 'owner'" in e for e in errors)

    def test_valid_spec_validates_clean(self):
        assert _spec().validate() == []


# ---------------------------------------------------------------------------
# parse_gitlab_remote
# ---------------------------------------------------------------------------


class TestParseGitlabRemote:
    def test_https_with_nested_groups(self):
        assert parse_gitlab_remote("https://gitlab.example.com/ca/sub/ml-2026-04") == (
            "https://gitlab.example.com",
            "ca/sub/ml-2026-04",
        )

    def test_https_strips_dot_git_and_trailing_slash(self):
        assert parse_gitlab_remote("https://gitlab.example.com/ca/repo.git/") == (
            "https://gitlab.example.com",
            "ca/repo",
        )

    def test_ssh_form_maps_to_https_api_base(self):
        assert parse_gitlab_remote("git@gitlab.example.com:ca/repo.git") == (
            "https://gitlab.example.com",
            "ca/repo",
        )

    def test_unparseable_remotes_return_none(self):
        assert parse_gitlab_remote("") is None
        assert parse_gitlab_remote("C:/local/path/repo") is None
        assert parse_gitlab_remote("https://host/repo-without-group") is None


# ---------------------------------------------------------------------------
# share_project_with_group (mocked HTTP)
# ---------------------------------------------------------------------------


def _response(status_code: int, payload=None, text: str = "") -> httpx.Response:
    if payload is not None:
        return httpx.Response(status_code, json=payload)
    return httpx.Response(status_code, text=text)


class TestShareProjectWithGroup:
    def _run(self, responses):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs.get("data")))
            return responses.pop(0)

        with patch.object(gitlab_api.httpx, "request", side_effect=fake_request):
            result = share_project_with_group(
                "https://gitlab.example.com",
                "ca/ml-2026-04",
                "students/azav-ml/ml-2026-04",
                "reporter",
                "tok",
            )
        return result, calls

    def test_happy_path_resolves_group_then_shares(self):
        result, calls = self._run([_response(200, {"id": 42}), _response(201, {})])
        assert result == "shared"
        assert calls[0][0] == "GET"
        assert "groups/students%2Fazav-ml%2Fml-2026-04" in calls[0][1]
        assert calls[1][0] == "POST"
        assert "projects/ca%2Fml-2026-04/share" in calls[1][1]
        assert calls[1][2] == {"group_id": 42, "group_access": 20}

    def test_existing_share_is_idempotent(self):
        result, _ = self._run(
            [_response(200, {"id": 42}), _response(409, {"message": "already shared"})]
        )
        assert result == "already-shared"

    def test_400_already_taken_is_idempotent_too(self):
        result, _ = self._run(
            [
                _response(200, {"id": 42}),
                _response(400, {"message": "group_id has already been taken"}),
            ]
        )
        assert result == "already-shared"

    def test_unknown_group_is_actionable(self):
        with pytest.raises(GitLabApiError, match="not found"):
            self._run([_response(404, {})])

    def test_missing_project_advises_creating_the_repo(self):
        with pytest.raises(GitLabApiError, match="Create the repo"):
            self._run([_response(200, {"id": 42}), _response(404, {})])


# ---------------------------------------------------------------------------
# clm release provision CLI
# ---------------------------------------------------------------------------


def _write_spec(tmp_path: Path) -> Path:
    specs_dir = tmp_path / "course-specs"
    specs_dir.mkdir(exist_ok=True)
    spec_file = specs_dir / "course.xml"
    spec_file.write_text(SPEC_WITH_SHARES, encoding="utf-8")
    return spec_file


@pytest.fixture(autouse=True)
def _clean_git_config():
    """Neutralize user/env git config so remote-URL derivation is deterministic."""
    from clm.infrastructure.config import GitConfig

    mock_config = MagicMock()
    mock_config.git = GitConfig()
    with patch("clm.cli.commands.git.get_config", return_value=mock_config):
        yield


class TestProvisionCli:
    def test_dry_run_lists_every_share_without_a_token(self, tmp_path, monkeypatch):
        for var in ("CLM_GITLAB_TOKEN", "GITLAB_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        spec_file = _write_spec(tmp_path)
        result = CliRunner().invoke(release_group, ["provision", str(spec_file), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "DRY RUN" in result.output
        # Inherited trainers share + cohort share for 2026-04; override for 2026-10.
        assert "ml-2026-04-solutions -> trainers (maintainer)" in result.output
        assert "-> students/azav-ml/ml-2026-04 (reporter)" in result.output
        assert "-> trainers (guest)" in result.output

    def test_missing_token_is_a_clear_error(self, tmp_path, monkeypatch):
        for var in ("CLM_GITLAB_TOKEN", "GITLAB_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        spec_file = _write_spec(tmp_path)
        result = CliRunner().invoke(release_group, ["provision", str(spec_file)])
        assert result.exit_code != 0
        assert "CLM_GITLAB_TOKEN" in result.output

    def test_shares_are_applied_via_the_api(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_GITLAB_TOKEN", "tok")
        spec_file = _write_spec(tmp_path)
        with patch(
            "clm.infrastructure.gitlab_api.share_project_with_group",
            side_effect=["shared", "already-shared", "shared"],
        ) as mock_share:
            result = CliRunner().invoke(release_group, ["provision", str(spec_file)])
        assert result.exit_code == 0, result.output
        assert mock_share.call_count == 3
        # The first share targets the trainers group at maintainer level.
        args = mock_share.call_args_list[0][0]
        assert args[0] == "https://gitlab.example.com"
        assert args[1] == "ca/ml-2026-04-solutions"
        assert args[2] == "trainers"
        assert args[3] == "maintainer"
        assert "already shared" in result.output

    def test_single_channel_without_shares_reports_nothing_to_do(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_GITLAB_TOKEN", "tok")
        body = (
            SPEC_WITH_SHARES.replace('<share-with access="maintainer">trainers</share-with>', "")
            .replace("<share-with>students/azav-ml/ml-2026-04</share-with>", "")
            .replace('<share-with access="guest">trainers</share-with>', "")
        )
        specs_dir = tmp_path / "course-specs"
        specs_dir.mkdir()
        spec_file = specs_dir / "course.xml"
        spec_file.write_text(body, encoding="utf-8")
        result = CliRunner().invoke(
            release_group, ["provision", str(spec_file), "--channel", "solutions/2026-04"]
        )
        assert result.exit_code == 0, result.output
        assert "nothing to provision" in result.output.lower()

    def test_shared_destination_shares_are_applied_once_per_repo(self, tmp_path, monkeypatch):
        """Channels of different streams releasing into one repo (issue #325)
        collapse identical shares; all of them target the first channel's
        project, since that is the repository they all are."""
        for var in ("CLM_GITLAB_TOKEN", "GITLAB_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        spec = SPEC_WITH_SHARES.replace(
            '<channel name="2026-10" path="release/2026-10" ledger="release/2026-10.txt">\n'
            '      <share-with access="guest">trainers</share-with>\n'
            "    </channel>\n"
            "  </release-channels>",
            "</release-channels>\n"
            '  <release-channels name="materials" source-target="completed">\n'
            '    <channel name="2026-04" path="release/2026-04" '
            'ledger="release/materials-2026-04.txt">\n'
            "      <share-with>students/azav-ml/ml-2026-04</share-with>\n"
            '      <share-with access="developer">trainers</share-with>\n'
            "    </channel>\n"
            "  </release-channels>",
        )
        specs_dir = tmp_path / "course-specs"
        specs_dir.mkdir()
        spec_file = specs_dir / "course.xml"
        spec_file.write_text(spec, encoding="utf-8")

        result = CliRunner().invoke(release_group, ["provision", str(spec_file), "--dry-run"])
        assert result.exit_code == 0, result.output
        # The student share appears once, against the first stream's project.
        assert result.output.count("-> students/azav-ml/ml-2026-04") == 1
        assert result.output.count("ca/ml-2026-04-solutions") >= 1
        assert "ca/ml-2026-04-materials" not in result.output
        # The conflicting trainers access level is reported, not re-applied.
        assert "collapsed" in result.output
        assert result.output.count("-> trainers") == 1

    def test_channel_repo_attribute_overrides_derived_name(self, tmp_path, monkeypatch):
        """`<channel repo="...">` makes provision target the real project (#322)."""
        for var in ("CLM_GITLAB_TOKEN", "GITLAB_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        spec = SPEC_WITH_SHARES.replace(
            '<channel name="2026-04" ',
            '<channel name="2026-04" repo="ml-azav-2026-04-solutions" ',
        )
        specs_dir = tmp_path / "course-specs"
        specs_dir.mkdir()
        spec_file = specs_dir / "course.xml"
        spec_file.write_text(spec, encoding="utf-8")

        result = CliRunner().invoke(release_group, ["provision", str(spec_file), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "ca/ml-azav-2026-04-solutions -> trainers (maintainer)" in result.output
        assert "ca/ml-2026-04-solutions ->" not in result.output
        # The channel without an override keeps the derived name.
        assert "ca/ml-2026-10-solutions -> trainers (guest)" in result.output

    def test_working_tree_origin_wins_over_derived_url(self, tmp_path, monkeypatch):
        """Provision targets the repo's actual origin when one is set (#322)."""
        import subprocess

        from clm.cli.commands.git import _dry_run_mode

        for var in ("CLM_GITLAB_TOKEN", "GITLAB_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        spec_file = _write_spec(tmp_path)
        dest = tmp_path / "release" / "2026-04"
        dest.mkdir(parents=True)
        subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(dest),
                "remote",
                "add",
                "origin",
                "https://gitlab.example.com/ca/the-actual-repo.git",
            ],
            check=True,
        )

        # The origin lookup must really run even when the `clm git` dry-run
        # ContextVar is set (it leaks across CliRunner invocations in one
        # test process, and provision's own --dry-run must still name the
        # project that would actually be shared).
        token = _dry_run_mode.set(True)
        try:
            result = CliRunner().invoke(release_group, ["provision", str(spec_file), "--dry-run"])
        finally:
            _dry_run_mode.reset(token)
        assert result.exit_code == 0, result.output
        assert "ca/the-actual-repo -> trainers (maintainer)" in result.output
        assert "ca/ml-2026-04-solutions ->" not in result.output
        # 2026-10 has no working tree; the derived URL remains its fallback.
        assert "ca/ml-2026-10-solutions -> trainers (guest)" in result.output

    def test_api_error_exits_nonzero_but_continues(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_GITLAB_TOKEN", "tok")
        spec_file = _write_spec(tmp_path)
        with patch(
            "clm.infrastructure.gitlab_api.share_project_with_group",
            side_effect=[GitLabApiError("boom"), "shared", "shared"],
        ) as mock_share:
            result = CliRunner().invoke(release_group, ["provision", str(spec_file)])
        assert result.exit_code == 1
        assert mock_share.call_count == 3  # later shares still attempted
        assert "ERROR sharing" in result.output
