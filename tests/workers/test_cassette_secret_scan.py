"""The committed-cassette secret scanner (finding S9, #798).

Tightening the record-time filters does nothing for cassettes that are
*already committed* — several thousand of them across the course repos,
recorded before the response side was filtered at all. Re-recording them
blindly is not an option: each one needs a live service, and most contain
nothing sensitive.

So the fix ships an audit instead: a read-only scan that reports which
cassette, which interaction, and which key, so a repo owner can re-record
exactly the decks that need it. It never rewrites — the repair path
(``clm cassette doctor --fix``) is a different, narrower thing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.infrastructure.http_replay_mitm import cassette_format as cf
from clm.workers.notebook.cassette_doctor import (
    scan_cassette_secrets,
    scan_cassettes_for_secrets,
)


def _cassette(tmp_path: Path, interactions: list[dict], name: str = "deck") -> Path:
    """Write a cassette in the committed vcrpy-v1 YAML shape."""
    import yaml

    path = tmp_path / f"{name}.http-cassette.yaml"
    path.write_text(
        yaml.safe_dump({"interactions": interactions, "version": 1}, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _interaction(
    *,
    uri: str = "https://api.example.com/v1/chat",
    request_headers: dict | None = None,
    request_body: str = "{}",
    response_headers: dict | None = None,
    response_body: str = "{}",
) -> dict:
    return {
        "request": {
            "method": "POST",
            "uri": uri,
            "body": request_body,
            "headers": {k: [v] for k, v in (request_headers or {}).items()},
        },
        "response": {
            "status": {"code": 200, "message": "OK"},
            "headers": {
                k: [v]
                for k, v in (response_headers or {"content-type": "application/json"}).items()
            },
            "body": {"string": response_body},
        },
    }


class TestCleanCassettesPass:
    def test_a_cassette_with_no_secrets_reports_nothing(self, tmp_path: Path) -> None:
        path = _cassette(
            tmp_path,
            [
                _interaction(
                    request_body='{"model": "x"}',
                    response_body='{"choices": [{"text": "hi"}], "usage": {"total_tokens": 7}}',
                )
            ],
        )
        report = scan_cassette_secrets(path)
        assert report.findings == []
        assert report.interaction_count == 1
        assert report.error is None

    def test_llm_usage_counters_are_not_secrets(self, tmp_path: Path) -> None:
        """The substring trap again, this time in the scanner.

        A scanner that flagged ``completion_tokens`` would mark every LLM
        cassette in every course repo as dirty — an audit nobody can act
        on is worse than no audit.
        """
        path = _cassette(
            tmp_path,
            [
                _interaction(
                    response_body=json.dumps(
                        {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
                    )
                )
            ],
        )
        assert scan_cassette_secrets(path).findings == []


class TestDirtyCassettesAreFlagged:
    def test_secret_request_header(self, tmp_path: Path) -> None:
        path = _cassette(
            tmp_path, [_interaction(request_headers={"authorization": "Bearer SECRET"})]
        )
        findings = scan_cassette_secrets(path).findings
        assert [f.key for f in findings] == ["authorization"]
        assert findings[0].index == 0
        assert "request" in findings[0].location

    def test_secret_query_parameter(self, tmp_path: Path) -> None:
        path = _cassette(tmp_path, [_interaction(uri="https://g.example/v1?key=SECRET")])
        findings = scan_cassette_secrets(path).findings
        assert [f.key for f in findings] == ["key"]

    def test_secret_request_body_parameter(self, tmp_path: Path) -> None:
        path = _cassette(
            tmp_path,
            [
                _interaction(
                    request_headers={"content-type": "application/json"},
                    request_body='{"password": "hunter2"}',
                )
            ],
        )
        assert [f.key for f in scan_cassette_secrets(path).findings] == ["password"]

    def test_set_cookie_response_header(self, tmp_path: Path) -> None:
        path = _cassette(tmp_path, [_interaction(response_headers={"set-cookie": "s=1"})])
        findings = scan_cassette_secrets(path).findings
        assert [f.key for f in findings] == ["set-cookie"]
        assert "response" in findings[0].location

    def test_token_in_a_nested_response_body(self, tmp_path: Path) -> None:
        path = _cassette(
            tmp_path,
            [
                _interaction(
                    response_body=json.dumps({"data": {"items": [{"refresh_token": "ya29.S"}]}})
                )
            ],
        )
        assert [f.key for f in scan_cassette_secrets(path).findings] == ["refresh_token"]

    def test_an_already_redacted_value_is_not_a_finding(self, tmp_path: Path) -> None:
        """A cassette recorded *after* the fix carries the placeholder.

        Flagging it would tell repo owners to re-record cassettes that
        are already clean.
        """
        path = _cassette(
            tmp_path,
            [_interaction(response_body=json.dumps({"access_token": cf.SECRET_PLACEHOLDER}))],
        )
        assert scan_cassette_secrets(path).findings == []

    def test_findings_name_the_interaction_index(self, tmp_path: Path) -> None:
        path = _cassette(
            tmp_path,
            [
                _interaction(),
                _interaction(response_headers={"set-cookie": "s=1"}),
            ],
        )
        findings = scan_cassette_secrets(path).findings
        assert [f.index for f in findings] == [1]


class TestScannerRobustness:
    def test_an_unloadable_cassette_is_reported_not_raised(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.http-cassette.yaml"
        path.write_text("this: is: not: a cassette", encoding="utf-8")
        report = scan_cassette_secrets(path)
        assert report.error is not None
        assert report.findings == []

    def test_the_scan_never_rewrites(self, tmp_path: Path) -> None:
        path = _cassette(tmp_path, [_interaction(response_headers={"set-cookie": "s=1"})])
        before = path.read_bytes()
        scan_cassette_secrets(path)
        assert path.read_bytes() == before

    def test_many_cassettes_are_reported_per_file(self, tmp_path: Path) -> None:
        clean = _cassette(tmp_path, [_interaction()], name="clean")
        dirty = _cassette(
            tmp_path, [_interaction(response_headers={"set-cookie": "s=1"})], name="dirty"
        )
        reports = {r.path: r for r in scan_cassettes_for_secrets([clean, dirty])}
        assert reports[clean].findings == []
        assert len(reports[dirty].findings) == 1


class TestScanCli:
    """The text report goes through the shared Rich console, which binds to
    the real stderr at import time and is therefore not visible to
    ``CliRunner`` — so content is asserted through ``--json`` (plain
    ``click.echo``) and the text mode is pinned on its exit code."""

    def _run(self, args, cwd: Path, *, dirty: bool = True):
        from click.testing import CliRunner

        from clm.cli.commands.cassette import cassette_group

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=cwd) as fs:
            target = Path(fs)
            interaction = (
                _interaction(response_headers={"set-cookie": "s=1"}) if dirty else _interaction()
            )
            _cassette(target, [interaction], name="deck")
            return runner.invoke(cassette_group, ["scan", *args])

    def test_findings_exit_nonzero(self, tmp_path: Path) -> None:
        """Non-zero so the scan can gate a repo audit in CI."""
        assert self._run([], tmp_path).exit_code != 0

    def test_a_clean_tree_exits_zero(self, tmp_path: Path) -> None:
        assert self._run([], tmp_path, dirty=False).exit_code == 0

    def test_json_mode_is_machine_readable(self, tmp_path: Path) -> None:
        result = self._run(["--json"], tmp_path)
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["finding_count"] == 1
        finding = payload["cassettes"][0]["findings"][0]
        assert finding["key"] == "set-cookie"
        assert finding["location"] == "response header"
        assert finding["index"] == 0

    def test_json_mode_still_exits_nonzero(self, tmp_path: Path) -> None:
        """The report is printed *and* the exit code is set."""
        result = self._run(["--json"], tmp_path)
        assert result.exit_code != 0
        assert '"finding_count": 1' in result.output


@pytest.mark.parametrize(
    "cassette",
    [
        "tests/infrastructure/fixtures/golden.http-cassette.yaml",
        "tests/test-data/slides/module_060_http_replay/topic_100_replay_shapes/"
        ".clm/cassettes/slides_replay_shapes.http-cassette.yaml",
    ],
)
def test_committed_cassettes_in_this_repo(cassette: str) -> None:
    """What the scanner says about clm's own committed cassettes.

    The golden fixture deliberately contains a ``set-cookie`` response
    header — it pins that the *format* can round-trip multi-value headers,
    which stays true for reading cassettes recorded before the filter
    existed. It is hand-built, not recorded, so the record-time filter
    does not touch it; this test states which of the two files the audit
    is expected to flag, so a future accidental leak stands out.
    """
    report = scan_cassette_secrets(Path(cassette))
    keys = {f.key for f in report.findings}
    if "golden" in cassette:
        assert keys == {"set-cookie"}
    else:
        assert keys == set(), f"unexpected secrets in a recorded cassette: {report.findings}"
