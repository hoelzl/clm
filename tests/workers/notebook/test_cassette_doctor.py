"""Tests for the cassette-doctor diagnostics + repair (issue #125).

The doctor flags *chain-orphan* interactions: chat-completion responses whose
extracted text is long enough to be a chain edge yet appears in no other
interaction's request body (a chain-opener whose closer was never recorded).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from clm.workers.notebook.cassette_doctor import (
    DEFAULT_MIN_TEXT_LEN,
    diagnose_cassette,
    extract_response_contents,
    find_orphans,
    iter_cassette_paths,
)

# vcr is an optional extra; skip the whole module when it isn't installed.
pytest.importorskip("yaml")  # cassette I/O needs PyYAML ([replay] extra)


def _chat_completion_body(content: str) -> str:
    """A minimal non-streaming OpenAI chat-completion JSON response body."""
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }
    )


def _streaming_body(content: str) -> str:
    """An SSE chat-completion stream that emits ``content`` in two deltas."""
    half = len(content) // 2
    chunks = [content[:half], content[half:]]
    lines = []
    for fragment in chunks:
        lines.append(
            "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": fragment}}]})
        )
    lines.append("data: [DONE]")
    return "\n".join(lines) + "\n"


def _chat_request_body(prompt: str) -> str:
    """A chat-completions request body embedding ``prompt`` in a user message."""
    return json.dumps(
        {
            "model": "gpt-test",
            "messages": [{"role": "user", "content": prompt}],
        }
    )


def _write_cassette(path: Path, interactions: list[tuple[str, str]]) -> None:
    """Write a cassette from (request_body, response_body) pairs.

    Each interaction is a POST to the chat-completions endpoint with the given
    request and response bodies, serialized with the same persister CLM uses.
    """
    from clm.infrastructure.http_replay_mitm.vcr_format import Request, serialize_cassette

    requests = []
    responses = []
    for req_body, resp_body in interactions:
        requests.append(
            Request(
                method="POST",
                uri="https://api.openai.com/v1/chat/completions",
                body=req_body,
                headers={"content-type": "application/json"},
            )
        )
        responses.append(
            {
                "status": {"code": 200, "message": "OK"},
                "headers": {"content-type": ["application/json"]},
                "body": {"string": resp_body},
            }
        )
    payload = serialize_cassette({"requests": requests, "responses": responses})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")


# --- content extraction ------------------------------------------------------


class TestExtractResponseContents:
    def test_extracts_nonstreaming_message_content(self):
        resp = {"body": {"string": _chat_completion_body("hello world")}}
        assert extract_response_contents(resp) == ["hello world"]

    def test_extracts_streaming_delta_content(self):
        text = "the quick brown fox jumps"
        resp = {"body": {"string": _streaming_body(text)}}
        assert extract_response_contents(resp) == [text]

    def test_non_chat_completion_body_yields_nothing(self):
        resp = {"body": {"string": json.dumps({"data": [{"embedding": [0.1]}]})}}
        assert extract_response_contents(resp) == []

    def test_unparseable_body_yields_nothing(self):
        resp = {"body": {"string": "<html>not json</html>"}}
        assert extract_response_contents(resp) == []

    def test_missing_body_yields_nothing(self):
        assert extract_response_contents({}) == []
        assert extract_response_contents("not a dict") == []


# --- orphan detection (pure) -------------------------------------------------

_LONG_A = "Clarified question A: " + "x" * 60
_LONG_B = "Clarified question B: " + "y" * 60


class TestFindOrphans:
    def test_complete_chain_has_no_orphan(self):
        # Opener's response text is embedded in the closer's request body.
        requests_responses = [
            (_chat_request_body("vague A"), _chat_completion_body(_LONG_A)),
            (_chat_request_body(f"answer this: {_LONG_A}"), _chat_completion_body("done")),
        ]
        from clm.infrastructure.http_replay_mitm.vcr_format import Request

        reqs = [
            Request(method="POST", uri="u", body=rb, headers={}) for rb, _ in requests_responses
        ]
        resps = [{"body": {"string": resp}} for _, resp in requests_responses]
        assert find_orphans(reqs, resps) == []

    def test_orphan_opener_is_flagged(self):
        # Opener's response text appears in NO other request body.
        from clm.infrastructure.http_replay_mitm.vcr_format import Request

        reqs = [
            Request(method="POST", uri="open", body=_chat_request_body("vague A"), headers={}),
            Request(method="POST", uri="other", body=_chat_request_body("unrelated"), headers={}),
        ]
        resps = [
            {"body": {"string": _chat_completion_body(_LONG_A)}},
            {"body": {"string": _chat_completion_body("short")}},
        ]
        orphans = find_orphans(reqs, resps)
        assert len(orphans) == 1
        assert orphans[0].index == 0
        assert orphans[0].uri == "open"
        assert orphans[0].text_len == len(_LONG_A)

    def test_min_text_len_boundary(self):
        from clm.infrastructure.http_replay_mitm.vcr_format import Request

        content = "z" * DEFAULT_MIN_TEXT_LEN  # exactly the default threshold
        reqs = [Request(method="POST", uri="u", body="{}", headers={})]
        resps = [{"body": {"string": _chat_completion_body(content)}}]

        # At default len: candidate -> flagged (no other request embeds it).
        assert len(find_orphans(reqs, resps)) == 1
        # One char shorter: below threshold -> not a candidate.
        short = "z" * (DEFAULT_MIN_TEXT_LEN - 1)
        resps_short = [{"body": {"string": _chat_completion_body(short)}}]
        assert find_orphans(reqs, resps_short) == []
        # Raising the threshold above the content length also clears it.
        assert find_orphans(reqs, resps, min_text_len=DEFAULT_MIN_TEXT_LEN + 1) == []


# --- end-to-end on real cassette files ---------------------------------------


class TestDiagnoseCassette:
    def test_complete_chain_reports_no_orphans(self, tmp_path):
        path = tmp_path / "slides.http-cassette.yaml"
        _write_cassette(
            path,
            [
                (_chat_request_body("vague A"), _chat_completion_body(_LONG_A)),
                (_chat_request_body(f"refine: {_LONG_A}"), _chat_completion_body("answer")),
            ],
        )
        report = diagnose_cassette(path)
        assert report.interaction_count == 2
        assert report.orphans == []
        assert report.fixed is False

    def test_orphan_opener_reported_not_fixed_by_default(self, tmp_path):
        path = tmp_path / "slides.http-cassette.yaml"
        _write_cassette(
            path,
            [
                (_chat_request_body("vague A"), _chat_completion_body(_LONG_A)),
                (_chat_request_body("unrelated"), _chat_completion_body("short")),
            ],
        )
        report = diagnose_cassette(path)
        assert len(report.orphans) == 1
        assert report.orphans[0].index == 0
        assert report.fixed is False
        # Default (no --fix) must not rewrite the file.
        before = path.read_text(encoding="utf-8")
        diagnose_cassette(path)
        assert path.read_text(encoding="utf-8") == before

    def test_fix_removes_only_the_orphan(self, tmp_path):
        from clm.infrastructure.http_replay_mitm.vcr_format import load_cassette

        path = tmp_path / "slides.http-cassette.yaml"
        _write_cassette(
            path,
            [
                (_chat_request_body("vague A"), _chat_completion_body(_LONG_A)),  # orphan
                (_chat_request_body("vague B"), _chat_completion_body(_LONG_B)),  # opener (paired)
                (_chat_request_body(f"refine: {_LONG_B}"), _chat_completion_body("ok")),  # closer
            ],
        )
        report = diagnose_cassette(path, fix=True)
        assert report.fixed is True
        assert len(report.orphans) == 1
        assert report.orphans[0].index == 0

        # The complete B-chain survives; only the orphan A interaction is gone.
        reqs, _ = load_cassette(path)
        bodies = [r.body.decode("utf-8") if isinstance(r.body, bytes) else r.body for r in reqs]
        assert len(reqs) == 2
        assert _chat_request_body("vague A") not in bodies
        assert _chat_request_body("vague B") in bodies
        assert _chat_request_body(f"refine: {_LONG_B}") in bodies

    def test_unreadable_cassette_is_skipped(self, tmp_path):
        path = tmp_path / "broken.http-cassette.yaml"
        path.write_text("this: is: not: valid: cassette\n", encoding="utf-8")
        report = diagnose_cassette(path)
        assert report.error is not None
        assert report.orphans == []
        assert report.fixed is False


# --- cassette walking + JSON shape -------------------------------------------


class TestIterCassettePaths:
    def test_finds_only_canonical_cassettes(self, tmp_path):
        canonical = tmp_path / "sub" / "slides.http-cassette.yaml"
        canonical.parent.mkdir(parents=True)
        canonical.write_text("version: 1\n", encoding="utf-8")
        # Staging/partial siblings must NOT be walked.
        (tmp_path / "sub" / "slides.http-cassette.yaml.staging-x").write_text("x", encoding="utf-8")
        (tmp_path / "sub" / "slides.http-cassette.yaml.partial-y").write_text("y", encoding="utf-8")
        (tmp_path / "unrelated.txt").write_text("z", encoding="utf-8")

        found = list(iter_cassette_paths(tmp_path))
        assert found == [canonical]

    # --- symlinked directories (issue #886) --------------------------------
    #
    # ``Path.rglob`` does not follow directory symlinks (``recurse_symlinks``
    # only exists on 3.13+), so a cassette behind one was invisible to both
    # ``clm cassette doctor`` and ``clm cassette scan`` — a silent coverage
    # hole once #883 made the scan a CI gate.  Skipped, not failed, where
    # the process cannot create a symlink (unprivileged Windows).

    @staticmethod
    def _symlink_dir(target: Path, link: Path) -> None:
        try:
            os.symlink(target, link, target_is_directory=True)
            return
        except (OSError, NotImplementedError) as exc:
            reason = str(exc)
        if sys.platform == "win32":
            # No symlink privilege: a junction needs none, is followed by
            # ``is_dir`` and resolved by ``realpath`` just like a symlink,
            # so the walk's link handling stays exercised on the platform
            # CLM is developed on rather than skipping (review of #886).
            import _winapi

            try:
                _winapi.CreateJunction(str(target), str(link))
                return
            except OSError as exc:
                reason = f"{reason}; junction fallback failed too: {exc}"
        pytest.skip(f"cannot create a directory link here: {reason}")

    @staticmethod
    def _cassette(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("version: 1\n", encoding="utf-8")
        return path

    def test_follows_a_symlinked_directory(self, tmp_path):
        """Regression test for #886: the walk must look behind a directory link."""
        elsewhere = tmp_path / "elsewhere"
        self._cassette(elsewhere / "leak.http-cassette.yaml")
        root = tmp_path / "root"
        root.mkdir()
        self._symlink_dir(elsewhere, root / "linked")

        found = list(iter_cassette_paths(root))
        assert found == [root / "linked" / "leak.http-cassette.yaml"]

    def test_a_symlink_cycle_terminates_and_yields_each_cassette_once(self, tmp_path):
        """A link back up the tree must not recurse forever (#886)."""
        root = tmp_path / "root"
        cassette = self._cassette(root / "a" / "x.http-cassette.yaml")
        self._symlink_dir(root, root / "a" / "loop")

        found = list(iter_cassette_paths(root))
        assert found == [cassette]

    def test_a_directory_reachable_twice_is_walked_once(self, tmp_path):
        """Two routes to one directory yield its cassettes once, by the first route (#886)."""
        root = tmp_path / "root"
        cassette = self._cassette(root / "a" / "x.http-cassette.yaml")
        self._symlink_dir(root / "a", root / "b")

        found = list(iter_cassette_paths(root))
        assert found == [cassette]

    def test_a_symlinked_cassette_file_is_still_found(self, tmp_path):
        """File links were always followed; the directory fix must not regress that."""
        real = self._cassette(tmp_path / "elsewhere" / "leak.http-cassette.yaml")
        root = tmp_path / "root"
        root.mkdir()
        link = root / "leak.http-cassette.yaml"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"cannot create a file symlink here: {exc}")

        assert list(iter_cassette_paths(root)) == [link]


class TestJsonReportShape:
    def test_doctor_json_report_isolated(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from clm.cli.main import cli

        path = tmp_path / "slides.http-cassette.yaml"
        _write_cassette(
            path,
            [
                (_chat_request_body("vague A"), _chat_completion_body(_LONG_A)),
                (_chat_request_body("unrelated"), _chat_completion_body("short")),
            ],
        )
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(cli, ["cassette", "doctor", "--json"], catch_exceptions=False)
        assert result.exit_code == 0

        # JSON is emitted on stdout; locate the object by braces in case any
        # stray logging merges into the captured output.
        out = result.output
        start = out.index("{")
        end = out.rindex("}") + 1
        data = json.loads(out[start:end])

        assert data["min_text_len"] == DEFAULT_MIN_TEXT_LEN
        assert data["fix"] is False
        assert data["cassette_count"] == 1
        assert data["orphan_count"] == 1
        cassette = data["cassettes"][0]
        assert cassette["interaction_count"] == 2
        assert cassette["orphan_count"] == 1
        orphan = cassette["orphans"][0]
        assert orphan["index"] == 0
        assert orphan["method"] == "POST"
        assert "request_fingerprint" in orphan
        assert orphan["text_len"] == len(_LONG_A)
