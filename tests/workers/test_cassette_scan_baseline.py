"""A baseline of accepted findings, so the scan can gate a repo (#883).

``clm cassette scan`` exits non-zero on *any* finding. PythonCourses has 294
of them (#874) — every one a ``Set-Cookie`` response header, every one
non-credential, and none worth re-recording 84 decks of live teaching
material to clear. So a CI job running the scan would fail on day one and
keep failing, and **an unsatisfiable gate gets switched off**. That is the
same failure the whole S9 arc is about, one level up.

A baseline blesses what is there today; anything new fails. The design
lives or dies on its match key, and the two omissions are the point:

* **not the interaction index** — re-recording a deck shifts indices, so an
  index-keyed baseline would report every accepted finding as new the first
  time somebody does the right thing;
* **not the value** — a finding never carries one (the report must not print
  secrets) and ``__cf_bm`` rotates on every recording, so a value-keyed
  baseline would churn constantly.

The cost of that is real and tested below: the key is name-level, so
accepting ``deck / response header / set-cookie`` accepts a *different*
cookie in the same file too. Inherent — the audit only ever sees the header
name — and the docs must not imply otherwise.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
import yaml

from clm.workers.notebook.cassette_doctor import (
    BASELINE_VERSION,
    BaselineError,
    apply_baseline,
    build_baseline,
    iter_cassette_paths,
    load_baseline,
    scan_cassettes_for_secrets,
)


def _json_object(text: str) -> dict:
    """The first JSON object in *text*, ignoring anything after it.

    A run can now print the report *and then* fail with a ``ClickException``,
    so the JSON is no longer the tail of the output and slicing from the
    first brace to the end stopped parsing. ``raw_decode`` stops at the
    object's close.
    """
    decoded: dict = json.JSONDecoder().raw_decode(text[text.index("{") :])[0]
    return decoded


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


def _write(root: Path, rel: str, interactions: list[dict]) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"interactions": interactions, "version": 1}, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _scan(root: Path) -> list:
    return scan_cassettes_for_secrets(iter_cassette_paths(root))


def _cookie_deck(
    root: Path, rel: str = "m550/deck.http-cassette.yaml", cookie: str = "s=1"
) -> Path:
    return _write(root, rel, [_interaction(response_headers={"set-cookie": cookie})])


class TestBuildingABaseline:
    def test_it_blesses_every_current_finding(self, tmp_path: Path) -> None:
        _cookie_deck(tmp_path)
        document = build_baseline(_scan(tmp_path), tmp_path)
        assert document["version"] == BASELINE_VERSION
        assert document["entries"] == [
            {
                "path": "m550/deck.http-cassette.yaml",
                "location": "response header",
                "key": "set-cookie",
            }
        ]

    def test_paths_are_posix_relative_to_the_scan_root(self, tmp_path: Path) -> None:
        """A baseline written on Windows has to match on Linux CI.

        CLM is developed on Windows and its CI runs on Linux, so a native
        separator in the file would make the gate pass locally and fail in
        the very place it exists to run.
        """
        _cookie_deck(tmp_path, "a/b/c/deck.http-cassette.yaml")
        entries = build_baseline(_scan(tmp_path), tmp_path)["entries"]
        assert entries[0]["path"] == "a/b/c/deck.http-cassette.yaml"
        assert "\\" not in entries[0]["path"]

    def test_entries_are_deduplicated_and_ordered(self, tmp_path: Path) -> None:
        """Two identical findings in one file are one entry.

        The key has no index, so a deck with the same cookie on three
        interactions is one thing to accept — and a stable order keeps the
        committed file from churning in diffs.
        """
        _write(
            tmp_path,
            "m550/deck.http-cassette.yaml",
            [_interaction(response_headers={"set-cookie": f"s={i}"}) for i in range(3)],
        )
        _cookie_deck(tmp_path, "m010/other.http-cassette.yaml")
        entries = build_baseline(_scan(tmp_path), tmp_path)["entries"]
        assert [e["path"] for e in entries] == [
            "m010/other.http-cassette.yaml",
            "m550/deck.http-cassette.yaml",
        ]

    def test_the_key_is_lowercased(self, tmp_path: Path) -> None:
        """``Set-Cookie`` and ``set-cookie`` are the same secret.

        The audit matches names case-insensitively and PythonCourses holds
        both spellings, so a case-sensitive baseline would report a casing
        flip as a brand-new finding.
        """
        _write(
            tmp_path,
            "deck.http-cassette.yaml",
            [_interaction(response_headers={"Set-Cookie": "s=1"})],
        )
        assert build_baseline(_scan(tmp_path), tmp_path)["entries"][0]["key"] == "set-cookie"

    def test_an_unreadable_cassette_contributes_nothing(self, tmp_path: Path) -> None:
        """You cannot bless what you cannot read."""
        (tmp_path / "broken.http-cassette.yaml").write_text("a: b: c", encoding="utf-8")
        assert build_baseline(_scan(tmp_path), tmp_path)["entries"] == []

    def test_paths_are_normalised_on_the_write_side_too(self, monkeypatch) -> None:
        """The one line that makes a Windows-written baseline match on Linux.

        It is the *write* side, so on Linux — where CI runs — it never fires
        naturally and deleting it changes nothing observable. Forcing
        ``relpath`` to hand back a native Windows path is what makes the
        rule testable on both platforms; routing the result through ``Path``
        instead would hide the deletion again on Windows, since a backslash
        is already a separator there.
        """
        from clm.workers.notebook.cassette_doctor import _relative_posix, _to_posix

        assert _to_posix("a\\b.yaml") == "a/b.yaml"
        assert _to_posix("a/b.yaml") == "a/b.yaml"

        monkeypatch.setattr(os.path, "relpath", lambda *_a, **_k: "m550\\topic\\deck.yaml")
        assert _relative_posix(Path("ignored"), Path("ignored")) == "m550/topic/deck.yaml"


class TestTheWrittenFileDoesNotChurn:
    """It is committed, so two writes of the same tree must be byte-equal.

    In-process this is untestable: both writes share one ``PYTHONHASHSEED``,
    so the entry set iterates identically whether or not it is sorted, and
    dropping the ``sorted()`` leaves the suite green. Separate interpreters
    are what make the difference visible.
    """

    def test_two_processes_write_the_same_bytes(self, tmp_path: Path) -> None:
        import subprocess
        import sys
        import textwrap

        for i in range(12):
            _cookie_deck(tmp_path, f"m{i:03d}/deck.http-cassette.yaml")

        script = textwrap.dedent(
            """
            import json, sys
            from pathlib import Path
            from clm.workers.notebook.cassette_doctor import (
                build_baseline, iter_cassette_paths, scan_cassettes_for_secrets,
            )
            root = Path(sys.argv[1])
            reports = scan_cassettes_for_secrets(iter_cassette_paths(root))
            print(json.dumps(build_baseline(reports, root), indent=2))
            """
        )
        outputs = set()
        for _ in range(4):
            proc = subprocess.run(
                [sys.executable, "-c", script, str(tmp_path)],
                capture_output=True,
                text=True,
                check=True,
                # A fresh hash seed per run: this is what makes an unsorted
                # set iterate differently, and therefore what makes the
                # missing sort visible at all.
                env={**os.environ, "PYTHONHASHSEED": "random"},
            )
            outputs.add(proc.stdout)
        assert len(outputs) == 1, f"{len(outputs)} distinct baselines across 4 processes"


class TestTheSummaryLineSaysWhatIsTrue:
    """The sentence the report ends on, which nothing used to assert.

    The Rich console binds to the real stderr at import time and is invisible
    to ``CliRunner``, so the whole summary went untested — and it read
    "0 with secrets (0 finding(s))" directly below a finding it had just
    listed, because the counters had been redefined to mean *gating*
    findings. Counting is pure and testable; printing is not.
    """

    def test_without_a_baseline_everything_gates(self, tmp_path: Path) -> None:
        from clm.cli.commands.cassette import secret_report_summary

        _cookie_deck(tmp_path)
        dirty, total, gating, skipped = secret_report_summary(_scan(tmp_path))
        assert (dirty, total, gating, skipped) == (1, 1, 1, 0)

    def test_an_accepted_finding_is_counted_but_does_not_gate(self, tmp_path: Path) -> None:
        from clm.cli.commands.cassette import secret_report_summary

        _cookie_deck(tmp_path)
        reports = _scan(tmp_path)
        apply_baseline(reports, tmp_path, _entries(build_baseline(reports, tmp_path)))
        dirty, total, gating, skipped = secret_report_summary(reports, baselined=True)
        assert (dirty, total, gating, skipped) == (1, 1, 0, 0)

    def test_an_unreadable_cassette_is_counted_separately(self, tmp_path: Path) -> None:
        from clm.cli.commands.cassette import secret_report_summary

        _cookie_deck(tmp_path)
        (tmp_path / "broken.http-cassette.yaml").write_text("a: b: c", encoding="utf-8")
        dirty, total, gating, skipped = secret_report_summary(_scan(tmp_path))
        assert (dirty, total, gating, skipped) == (1, 1, 1, 1)


class TestApplyingABaseline:
    def test_an_unchanged_repo_has_nothing_new(self, tmp_path: Path) -> None:
        _cookie_deck(tmp_path)
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        outcome = apply_baseline(_scan(tmp_path), tmp_path, _entries(baseline))
        assert outcome.new == []
        assert len(outcome.accepted) == 1
        assert outcome.stale == []

    def test_a_new_finding_is_not_accepted(self, tmp_path: Path) -> None:
        """The whole point: a newly recorded secret must break the gate."""
        _cookie_deck(tmp_path)
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        _write(
            tmp_path,
            "m550/second.http-cassette.yaml",
            [_interaction(request_headers={"authorization": "Bearer LEAK"})],
        )
        outcome = apply_baseline(_scan(tmp_path), tmp_path, _entries(baseline))
        assert [(f.location, f.key) for f in outcome.new] == [("request header", "authorization")]

    def test_a_new_finding_kind_in_a_baselined_file_is_not_accepted(self, tmp_path: Path) -> None:
        """Accepting a cookie in a file must not accept a token in it.

        A path-only key would have made every baselined file a permanent
        blind spot, which is a far bigger hole than the name-level one this
        design does accept.
        """
        path = _cookie_deck(tmp_path)
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        path.write_text(
            yaml.safe_dump(
                {
                    "interactions": [
                        _interaction(response_headers={"set-cookie": "s=1"}),
                        _interaction(response_body=json.dumps({"access_token": "ya29.LEAK"})),
                    ],
                    "version": 1,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        outcome = apply_baseline(_scan(tmp_path), tmp_path, _entries(baseline))
        assert [(f.location, f.key) for f in outcome.new] == [("response body", "access_token")]

    def test_the_same_finding_at_a_new_index_is_still_accepted(self, tmp_path: Path) -> None:
        """The index is deliberately out of the key.

        Re-recording a deck shifts every interaction index. Keying on it
        would fail the gate the first time somebody re-records, which is
        exactly the behaviour we are asking for — so the gate would be
        punishing the fix.
        """
        path = _cookie_deck(tmp_path)
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        path.write_text(
            yaml.safe_dump(
                {
                    "interactions": [
                        _interaction(),
                        _interaction(),
                        _interaction(response_headers={"set-cookie": "s=1"}),
                    ],
                    "version": 1,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        outcome = apply_baseline(_scan(tmp_path), tmp_path, _entries(baseline))
        assert outcome.new == []
        assert outcome.accepted[0].index == 2

    def test_a_different_cookie_in_a_baselined_file_is_accepted(self, tmp_path: Path) -> None:
        """The documented cost of a name-level key, pinned so it stays known.

        A finding carries no value — the report must never print a secret —
        so once ``set-cookie`` is accepted for a file, *any* ``set-cookie``
        in it is. The audit could not tell a session cookie from ``__cf_bm``
        even if it wanted to. Narrowing "any cookie anywhere" to "a cookie
        in this file" is the improvement on offer; this test exists so the
        limit is a decision rather than a surprise.
        """
        path = _cookie_deck(tmp_path, cookie="__cf_bm=harmless")
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        path.write_text(
            yaml.safe_dump(
                {
                    "interactions": [
                        _interaction(response_headers={"set-cookie": "session=REAL-CREDENTIAL"})
                    ],
                    "version": 1,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        outcome = apply_baseline(_scan(tmp_path), tmp_path, _entries(baseline))
        assert outcome.new == []

    def test_the_same_finding_in_a_different_file_is_not_accepted(self, tmp_path: Path) -> None:
        _cookie_deck(tmp_path, "m550/deck.http-cassette.yaml")
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        _cookie_deck(tmp_path, "m550/other.http-cassette.yaml")
        outcome = apply_baseline(_scan(tmp_path), tmp_path, _entries(baseline))
        assert len(outcome.new) == 1

    def test_a_re_recorded_deck_leaves_a_stale_entry_not_a_failure(self, tmp_path: Path) -> None:
        """Doing the right thing must not fail the gate.

        A deck that gets re-recorded loses its findings, and its baseline
        entries stop matching. Failing on that would make the gate flap
        every time somebody cleans a deck up, so stale entries are reported
        and counted instead.
        """
        path = _cookie_deck(tmp_path)
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        path.write_text(
            yaml.safe_dump({"interactions": [_interaction()], "version": 1}, sort_keys=True),
            encoding="utf-8",
        )
        outcome = apply_baseline(_scan(tmp_path), tmp_path, _entries(baseline))
        assert outcome.new == []
        assert outcome.accepted == []
        assert [e.path for e in outcome.stale] == ["m550/deck.http-cassette.yaml"]

    def test_a_deleted_cassette_leaves_a_stale_entry(self, tmp_path: Path) -> None:
        path = _cookie_deck(tmp_path)
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        path.unlink()
        outcome = apply_baseline(_scan(tmp_path), tmp_path, _entries(baseline))
        assert len(outcome.stale) == 1

    def test_findings_are_marked_accepted(self, tmp_path: Path) -> None:
        """``--json`` consumers need to tell the two apart per finding."""
        _cookie_deck(tmp_path)
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        _write(
            tmp_path,
            "new.http-cassette.yaml",
            [_interaction(request_headers={"authorization": "Bearer LEAK"})],
        )
        reports = _scan(tmp_path)
        apply_baseline(reports, tmp_path, _entries(baseline))
        marks = {(f.location, f.accepted) for r in reports for f in r.findings}
        assert marks == {("response header", True), ("request header", False)}

    def test_an_unreadable_cassette_is_never_accepted(self, tmp_path: Path) -> None:
        """A file the audit cannot parse is not a file it can vouch for.

        Nothing in the baseline can bless it, so it keeps failing the gate
        — which is what ``--write-baseline`` refusing to exit zero on one
        is about.
        """
        _cookie_deck(tmp_path)
        baseline = build_baseline(_scan(tmp_path), tmp_path)
        (tmp_path / "broken.http-cassette.yaml").write_text("a: b: c", encoding="utf-8")
        outcome = apply_baseline(_scan(tmp_path), tmp_path, _entries(baseline))
        assert outcome.new == []
        assert outcome.unreadable == 1


class TestLoadingABaseline:
    def test_a_round_trip(self, tmp_path: Path) -> None:
        _cookie_deck(tmp_path)
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(build_baseline(_scan(tmp_path), tmp_path)), encoding="utf-8")
        assert len(load_baseline(path)) == 1

    def test_a_windows_separator_in_the_file_still_matches(self, tmp_path: Path) -> None:
        """Be liberal reading, strict writing.

        A hand-edited baseline, or one written by an older clm, may carry
        native separators. Refusing to match them would fail the gate for a
        cosmetic reason.
        """
        _cookie_deck(tmp_path, "a/b/deck.http-cassette.yaml")
        path = tmp_path / "baseline.json"
        path.write_text(
            json.dumps(
                {
                    "version": BASELINE_VERSION,
                    "entries": [
                        {
                            "path": "a\\b\\deck.http-cassette.yaml",
                            "location": "response header",
                            "key": "set-cookie",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        outcome = apply_baseline(_scan(tmp_path), tmp_path, load_baseline(path))
        assert outcome.new == []
        assert outcome.stale == []

    def test_the_key_is_lowercased_on_the_way_in_too(self, tmp_path: Path) -> None:
        """Liberal reading covers casing, not just separators.

        A hand-written entry naming ``Set-Cookie`` has to match the
        lowercased key the writer stores, or the gate fails for a cosmetic
        reason nobody can see.
        """
        _cookie_deck(tmp_path, "deck.http-cassette.yaml")
        path = tmp_path / "baseline.json"
        path.write_text(
            json.dumps(
                {
                    "version": BASELINE_VERSION,
                    "entries": [
                        {
                            "path": "deck.http-cassette.yaml",
                            "location": "response header",
                            "key": "Set-Cookie",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        outcome = apply_baseline(_scan(tmp_path), tmp_path, load_baseline(path))
        assert outcome.new == []
        assert outcome.stale == []

    @pytest.mark.parametrize(
        "text",
        [
            "not json at all",
            '{"version": 1}',
            '{"version": 99, "entries": []}',
            # ``isinstance(True, int)`` and ``1.0 == 1``, so a bare equality
            # check waves these through.
            '{"version": true, "entries": []}',
            '{"version": 1.0, "entries": []}',
            '{"version": "1", "entries": []}',
            '{"version": 1, "entries": "nope"}',
            '{"version": 1, "entries": [{"path": "a"}]}',
            # An entry that is not an object, and fields that are not
            # strings: without their guards these raise TypeError /
            # AttributeError, i.e. a traceback out of a CI gate.
            '{"version": 1, "entries": [["a", "b", "c"]]}',
            '{"version": 1, "entries": [{"path": 1, "location": "x", "key": "y"}]}',
            '{"version": 1, "entries": [{"path": "a", "location": "x", "key": null}]}',
            "[]",
            "",
        ],
    )
    def test_a_malformed_baseline_is_an_error_not_a_silent_pass(
        self, text: str, tmp_path: Path
    ) -> None:
        """Never degrade to "accept everything".

        A baseline that fails to parse must stop the run, not quietly
        become an empty set (which would fail the gate on everything, an
        unsatisfiable gate) and above all not become a match-all (which
        would vouch for the repo). Loud is the only safe answer.
        """
        path = tmp_path / "baseline.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(BaselineError):
            load_baseline(path)

    def test_a_missing_baseline_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(BaselineError):
            load_baseline(tmp_path / "nope.json")

    def test_a_directory_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "adir").mkdir()
        with pytest.raises(BaselineError):
            load_baseline(tmp_path / "adir")

    @pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-32"])
    def test_an_encoded_baseline_is_read_not_crashed_on(
        self, encoding: str, tmp_path: Path
    ) -> None:
        """``json.loads`` sniffs these itself; a strict UTF-8 pre-decode does not.

        PowerShell's ``Out-File`` writes UTF-16 by default, so on the
        platform CLM is developed on this is the ordinary way to end up with
        a hand-made baseline — and the pre-decode raised ``UnicodeDecodeError``
        straight out of the CLI as a traceback. Same lesson as #875, where
        the audit lost every response body carrying a BOM.
        """
        _cookie_deck(tmp_path, "deck.http-cassette.yaml")
        path = tmp_path / "baseline.json"
        path.write_bytes(json.dumps(build_baseline(_scan(tmp_path), tmp_path)).encode(encoding))
        assert len(load_baseline(path)) == 1

    def test_bytes_that_are_no_encoding_at_all_are_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        path.write_bytes(b"\xff\xfe\x00\x00not json")
        with pytest.raises(BaselineError):
            load_baseline(path)

    def test_a_pathologically_nested_baseline_is_an_error(self, tmp_path: Path) -> None:
        """``RecursionError`` must not escape either — same guard as the audit."""
        path = tmp_path / "baseline.json"
        path.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
        with pytest.raises(BaselineError):
            load_baseline(path)

    def test_a_cassette_outside_the_scan_root_is_an_error_not_a_basename(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The unsafe degradation this used to choose.

        Falling back to the file's *name* would collapse every same-named
        cassette in a course repo — 95 different ``deck.http-cassette.yaml``
        — into one entry, each accepting the others' findings. Unreachable
        through the CLI, so the raise is forced here rather than reproduced
        with a second drive letter, which only exists on one platform.
        """
        import os

        _cookie_deck(tmp_path)

        def _boom(*_args, **_kwargs):
            raise ValueError("path is on mount 'D:', start on mount 'C:'")

        monkeypatch.setattr(os.path, "relpath", _boom)
        with pytest.raises(BaselineError):
            build_baseline(_scan(tmp_path), tmp_path)


def _entries(document: dict) -> frozenset:
    """Load a freshly-built document the way the CLI would."""
    from clm.workers.notebook.cassette_doctor import baseline_entries_from_document

    return baseline_entries_from_document(document)


class TestScanCliBaseline:
    """The gate as a course repo would actually run it.

    The text report goes through the shared Rich console, which binds to the
    real stderr at import time and is invisible to ``CliRunner`` — so content
    is asserted through ``--json`` and text mode is pinned on its exit code.
    """

    def _run(self, args: list[str], cwd: Path | None = None):
        from click.testing import CliRunner

        from clm.cli.commands.cassette import cassette_group

        runner = CliRunner()
        return runner.invoke(cassette_group, ["scan", *args], catch_exceptions=False)

    def _payload(self, result) -> dict:
        return _json_object(result.output)

    def test_write_then_gate_is_green(self, tmp_path: Path, monkeypatch) -> None:
        """The whole point, end to end."""
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        baseline = tmp_path / "baseline.json"

        assert self._run(["--write-baseline", str(baseline)], tmp_path).exit_code == 0
        assert baseline.exists()
        assert self._run(["--baseline", str(baseline)], tmp_path).exit_code == 0

    def test_a_bare_scan_still_fails_on_the_same_tree(self, tmp_path: Path, monkeypatch) -> None:
        """Without a baseline nothing changes — this is opt-in."""
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        assert self._run([], tmp_path).exit_code != 0

    def test_a_new_finding_breaks_the_gate(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)], tmp_path)

        _write(
            tmp_path,
            "leak.http-cassette.yaml",
            [_interaction(request_headers={"authorization": "Bearer LEAK"})],
        )
        result = self._run(["--baseline", str(baseline), "--json"], tmp_path)
        assert result.exit_code != 0
        payload = self._payload(result)
        assert payload["new_count"] == 1
        assert payload["accepted_count"] == 1
        assert payload["finding_count"] == 2

    def test_json_marks_each_finding(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)], tmp_path)
        payload = self._payload(self._run(["--baseline", str(baseline), "--json"], tmp_path))
        findings = [f for c in payload["cassettes"] for f in c["findings"]]
        assert [f["accepted"] for f in findings] == [True]

    def test_finding_count_still_means_findings(self, tmp_path: Path, monkeypatch) -> None:
        """A consumer that has always read ``finding_count`` keeps its meaning.

        Redefining it to "new findings" under a baseline would silently
        change what an existing CI script reports — the exit code and
        ``new_count`` are where the baseline shows up.
        """
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)], tmp_path)
        payload = self._payload(self._run(["--baseline", str(baseline), "--json"], tmp_path))
        assert payload["finding_count"] == 1
        assert payload["new_count"] == 0

    def test_an_unreadable_cassette_still_fails_a_baselined_run(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A baseline cannot bless a file the audit could not parse."""
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)], tmp_path)
        (tmp_path / "broken.http-cassette.yaml").write_text("a: b: c", encoding="utf-8")
        assert self._run(["--baseline", str(baseline)], tmp_path).exit_code != 0

    def test_write_baseline_refuses_to_exit_zero_on_an_unreadable_cassette(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Otherwise it promises a green gate it cannot deliver.

        The file is still written — the readable findings are worth
        blessing — but exiting zero here and non-zero on the very next
        ``--baseline`` run is exactly the confusion this feature exists to
        remove.
        """
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        (tmp_path / "broken.http-cassette.yaml").write_text("a: b: c", encoding="utf-8")
        baseline = tmp_path / "baseline.json"
        assert self._run(["--write-baseline", str(baseline)], tmp_path).exit_code != 0
        assert baseline.exists()

    def test_a_malformed_baseline_fails_loudly(self, tmp_path: Path, monkeypatch) -> None:
        """Never silently accept everything."""
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        baseline = tmp_path / "baseline.json"
        baseline.write_text("{ not json", encoding="utf-8")
        result = self._run(["--baseline", str(baseline)], tmp_path)
        assert result.exit_code != 0
        assert "baseline" in result.output.lower()

    def test_a_missing_baseline_fails_loudly(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        result = self._run(["--baseline", str(tmp_path / "nope.json")], tmp_path)
        assert result.exit_code != 0

    def test_the_two_options_are_mutually_exclusive(self, tmp_path: Path, monkeypatch) -> None:
        """Passing both is ambiguous: read it, or overwrite it?"""
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        result = self._run(
            ["--baseline", str(tmp_path / "a.json"), "--write-baseline", str(tmp_path / "b.json")],
            tmp_path,
        )
        assert result.exit_code != 0
        assert not (tmp_path / "b.json").exists()

    def test_the_written_file_is_lf(self, tmp_path: Path, monkeypatch) -> None:
        """It is committed to a course repo — it must not flap on checkout.

        CLM is Windows-first and the repo normalises to LF, so writing
        native line endings would make the file dirty on every checkout.
        """
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path, "b/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)])

        raw = baseline.read_bytes()
        assert b"\r\n" not in raw
        assert raw.endswith(b"\n")

    def test_json_mode_reports_what_was_written(self, tmp_path: Path, monkeypatch) -> None:
        """``--json`` used to be silently ignored by --write-baseline."""
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        baseline = tmp_path / "baseline.json"
        payload = self._payload(self._run(["--write-baseline", str(baseline), "--json"]))
        assert payload["entry_count"] == 1
        assert payload["finding_count"] == 1
        assert payload["baseline"] == str(baseline)

    def test_no_temp_file_is_left_behind(self, tmp_path: Path, monkeypatch) -> None:
        """The write goes through a sibling temp file and ``os.replace``.

        A crash mid-write must not leave a half-written baseline the next run
        reads as a corrupt gate — but the temp file must also not survive a
        *successful* write, since it would land next to a committed baseline
        in a course repo with nothing to clean it up.
        """
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)])
        assert baseline.exists()
        assert list(tmp_path.glob("baseline.json.tmp-*")) == []

    def test_a_failed_write_leaves_no_temp_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        target = tmp_path / "out" / "baseline.json"
        target.parent.mkdir()

        def _boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", _boom)
        from click.testing import CliRunner

        from clm.cli.commands.cassette import cassette_group

        result = CliRunner().invoke(cassette_group, ["scan", "--write-baseline", str(target)])
        assert result.exit_code != 0
        assert list(target.parent.glob("*.tmp-*")) == []

    def test_a_baseline_error_while_writing_is_a_clean_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Both CLI wrappers matter; neither was pinned through the CLI."""
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)

        def _boom(*_args, **_kwargs):
            raise ValueError("path is on mount 'D:', start on mount 'C:'")

        monkeypatch.setattr(os.path, "relpath", _boom)
        from click.testing import CliRunner

        from clm.cli.commands.cassette import cassette_group

        result = CliRunner().invoke(
            cassette_group, ["scan", "--write-baseline", str(tmp_path / "b.json")]
        )
        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_a_baseline_error_while_applying_is_a_clean_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        baseline = tmp_path / "b.json"
        self._run(["--write-baseline", str(baseline)])

        def _boom(*_args, **_kwargs):
            raise ValueError("path is on mount 'D:', start on mount 'C:'")

        monkeypatch.setattr(os.path, "relpath", _boom)
        from click.testing import CliRunner

        from clm.cli.commands.cassette import cassette_group

        result = CliRunner().invoke(cassette_group, ["scan", "--baseline", str(baseline)])
        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_an_unwritable_target_is_a_clean_error_not_a_traceback(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A CI gate must not print a stack trace at somebody."""
        monkeypatch.chdir(tmp_path)
        _cookie_deck(tmp_path)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        from click.testing import CliRunner

        from clm.cli.commands.cassette import cassette_group

        result = CliRunner().invoke(
            cassette_group, ["scan", "--write-baseline", str(blocker / "b.json")]
        )
        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)


class TestTheGateCannotGoGreenOverTheWrongTree:
    """The Critical from review round 1 of #883.

    Everything else here guards against accepting a secret that *is* in the
    tree. This guards the other direction: a gate that scanned the wrong
    tree, found nothing, and said so with a green tick. A CI job with the
    wrong ``working-directory``, a checkout where course content did not
    materialise, a renamed content root — each produced **exit 0** over a
    repo nothing had looked at.

    The signal was there the whole time: every baseline entry stale, none
    accepted. It was just discarded by an early ``return``.
    """

    def _run(self, args: list[str]):
        from click.testing import CliRunner

        from clm.cli.commands.cassette import cassette_group

        return CliRunner().invoke(cassette_group, ["scan", *args])

    def test_a_baselined_run_over_an_empty_tree_fails(self, tmp_path: Path, monkeypatch) -> None:
        source = tmp_path / "source"
        source.mkdir()
        _cookie_deck(source)
        baseline = tmp_path / "baseline.json"
        monkeypatch.chdir(source)
        assert self._run(["--write-baseline", str(baseline)]).exit_code == 0

        empty = tmp_path / "elsewhere"
        empty.mkdir()
        monkeypatch.chdir(empty)
        result = self._run(["--baseline", str(baseline)])
        assert result.exit_code != 0
        assert "not scanned at all" in result.output

    def test_a_baselined_run_at_the_wrong_root_fails(self, tmp_path: Path, monkeypatch) -> None:
        """Same failure, reached by scanning a sibling tree that has cassettes."""
        source = tmp_path / "source"
        _cookie_deck(source, "m550/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        monkeypatch.chdir(source)
        self._run(["--write-baseline", str(baseline)])

        other = tmp_path / "other"
        _cookie_deck(other, "m999/different.http-cassette.yaml")
        monkeypatch.chdir(other)
        assert self._run(["--baseline", str(baseline)]).exit_code != 0

    def test_a_bare_scan_of_an_empty_tree_is_still_green(self, tmp_path: Path, monkeypatch) -> None:
        """An empty tree is a legitimate zero-finding result without a baseline.

        CppCourses and CSharpCourses hold no cassettes at all, so this must
        not become an error — only a *baseline* that describes nothing does.
        """
        monkeypatch.chdir(tmp_path)
        assert self._run([]).exit_code == 0

    def test_an_empty_baseline_over_an_empty_tree_is_green(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Nothing to accept and nothing found is not evidence of a wrong root."""
        monkeypatch.chdir(tmp_path)
        baseline = tmp_path / "baseline.json"
        assert self._run(["--write-baseline", str(baseline)]).exit_code == 0
        assert self._run(["--baseline", str(baseline)]).exit_code == 0

    def test_partial_staleness_stays_green(self, tmp_path: Path, monkeypatch) -> None:
        """The check is "nothing matched", not "something went stale".

        A deck that was re-recorded is somebody doing the right thing, and
        the gate must not punish it — the single most-repeated claim in this
        feature, and nothing pinned it at the CLI level before.
        """
        monkeypatch.chdir(tmp_path)
        kept = _cookie_deck(tmp_path, "keep/deck.http-cassette.yaml")
        gone = _cookie_deck(tmp_path, "cleaned/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)])
        assert kept.exists()

        gone.write_text(
            yaml.safe_dump({"interactions": [_interaction()], "version": 1}, sort_keys=True),
            encoding="utf-8",
        )
        result = self._run(["--baseline", str(baseline), "--json"])
        payload = _json_object(result.output)
        assert payload["stale_count"] == 1
        assert payload["accepted_count"] == 1
        assert result.exit_code == 0

    def test_the_root_name_is_recorded_and_a_mismatch_does_not_fail(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A hint, deliberately not a refusal.

        Entries are relative to the scan root, so applying a baseline at a
        different root re-interprets all of them — but a repo may legitimately
        check out under a different directory name, so this warns rather than
        breaking the build.
        """
        source = tmp_path / "namedroot"
        _cookie_deck(source, "deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        monkeypatch.chdir(source)
        self._run(["--write-baseline", str(baseline)])
        assert json.loads(baseline.read_text(encoding="utf-8"))["root_name"] == "namedroot"

        renamed = tmp_path / "othername"
        _cookie_deck(renamed, "deck.http-cassette.yaml")
        monkeypatch.chdir(renamed)
        assert self._run(["--baseline", str(baseline)]).exit_code == 0

    def test_a_fully_cleared_baseline_stays_green(self, tmp_path: Path, monkeypatch) -> None:
        """Finishing the remediation must not turn the build red.

        Every baselined deck re-recorded is the audit's request carried out
        in full. An earlier version of this check keyed on "nothing matched"
        and failed here — the gate punishing its own fix, which is how gates
        get switched off. Keying on *missing files* separates it from a
        wrong scan root, because the decks are still there.
        """
        monkeypatch.chdir(tmp_path)
        deck = _cookie_deck(tmp_path)
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)])

        deck.write_text(
            yaml.safe_dump({"interactions": [_interaction()], "version": 1}, sort_keys=True),
            encoding="utf-8",
        )
        result = self._run(["--baseline", str(baseline), "--json"])
        payload = _json_object(result.output)
        assert payload["stale_cleared_count"] == 1
        assert payload["stale_missing_count"] == 0
        assert result.exit_code == 0

    def test_a_missing_baselined_file_fails_even_when_others_match(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The partial case — a sparse checkout, not a re-record.

        One entry matching was enough to call the tree "described" before
        this, so a checkout holding 1 of 50 baselined decks passed green with
        a console line reading "re-record cleanup, most likely". A file that
        is not there is not cleanup.
        """
        monkeypatch.chdir(tmp_path)
        for i in range(4):
            _cookie_deck(tmp_path, f"m{i}/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)])

        for i in range(1, 4):
            (tmp_path / f"m{i}" / "deck.http-cassette.yaml").unlink()
        result = self._run(["--baseline", str(baseline), "--json"])
        payload = _json_object(result.output)
        assert payload["accepted_count"] == 1
        assert payload["stale_missing_count"] == 3
        assert payload["stale_cleared_count"] == 0
        assert result.exit_code != 0

    def test_a_new_finding_is_reported_before_the_refusal(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The review-round-2 Critical, and the worst bug of this feature.

        When a repo re-recorded its baselined decks *and* grew a new secret,
        the refusal fired first — so the operator never saw the secret, was
        told the scan root was wrong, and following the message's own advice
        (`--write-baseline`) would have blessed it. A guided false all-clear.
        Report first, refuse second.
        """
        monkeypatch.chdir(tmp_path)
        old = _cookie_deck(tmp_path, "old/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._run(["--write-baseline", str(baseline)])
        old.unlink()
        _write(
            tmp_path,
            "new/leak.http-cassette.yaml",
            [_interaction(request_headers={"authorization": "Bearer LEAK"})],
        )

        result = self._run(["--baseline", str(baseline), "--json"])
        assert result.exit_code != 0
        payload = _json_object(result.output)
        assert payload["new_count"] == 1
        assert [
            f["key"] for c in payload["cassettes"] for f in c["findings"] if not f["accepted"]
        ] == ["authorization"]
        # And the refusal must not send them off to launder it.
        assert "regenerating accepts whatever is there" in result.output


class TestTheTextReport:
    """What the operator actually sees — asserted through a real process.

    The rest of this file works through ``CliRunner``, which cannot see the
    Rich console (it binds to the real stderr at import time), and several
    docstrings here concluded from that that the text report was
    unassertable. It is not: it is unassertable *in-process*. Running the
    CLI as a subprocess and capturing stderr pins it fine — and without
    that, deleting the whole baselined report block, or the escaping, or
    the ordering the round-2 Critical was about, all left the suite green.
    """

    def _scan(self, root: Path, *args: str) -> tuple[int, str]:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "clm", "cassette", "scan", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            env={**os.environ, "COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"},
        )
        return proc.returncode, proc.stdout + proc.stderr

    def test_a_new_finding_is_printed_before_the_refusal(self, tmp_path: Path) -> None:
        """The round-2 Critical, in the mode its narrative is about.

        It was pinned only through ``--json``. The failure it describes —
        "the operator never saw the secret" — is about the *text* report,
        which nothing asserted, so a text-mode-only regression would have
        slipped straight back in.
        """
        old = _cookie_deck(tmp_path, "old/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._scan(tmp_path, "--write-baseline", str(baseline))
        old.unlink()
        _write(
            tmp_path,
            "new/leak.http-cassette.yaml",
            [_interaction(request_headers={"authorization": "Bearer LEAK"})],
        )

        code, output = self._scan(tmp_path, "--baseline", str(baseline))
        assert code != 0
        assert "authorization" in output
        assert "Error" in output
        assert output.index("authorization") < output.index("Error"), output

    def test_cleared_and_missing_entries_are_reported_differently(self, tmp_path: Path) -> None:
        """Deleting either block left the suite green."""
        _cookie_deck(tmp_path, "kept/deck.http-cassette.yaml")
        cleared = _cookie_deck(tmp_path, "cleared/deck.http-cassette.yaml")
        gone = _cookie_deck(tmp_path, "gone/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._scan(tmp_path, "--write-baseline", str(baseline))

        cleared.write_text(
            yaml.safe_dump({"interactions": [_interaction()], "version": 1}, sort_keys=True),
            encoding="utf-8",
        )
        gone.unlink()

        code, output = self._scan(tmp_path, "--baseline", str(baseline))
        assert code != 0
        assert "those decks were re-recorded" in output
        assert "not scanned at all" in output
        # The two must not be conflated: the cleared deck is cleanup, the
        # deleted one is not.
        assert "cleared/deck.http-cassette.yaml" in output
        assert "gone/deck.http-cassette.yaml" in output

    def test_an_accepted_finding_is_marked_as_such(self, tmp_path: Path) -> None:
        """Otherwise the report lists it indistinguishably from a new one."""
        _cookie_deck(tmp_path, "m0/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._scan(tmp_path, "--write-baseline", str(baseline))

        code, output = self._scan(tmp_path, "--baseline", str(baseline))
        assert code == 0
        assert "(accepted)" in output, output

    def test_the_root_name_warning_reaches_the_console(self, tmp_path: Path) -> None:
        """The pure helper is tested; that the CLI *prints* it was not.

        Suppressing the print left the whole suite green, and this is the
        one line that tells an operator their baseline belongs to a
        different tree.
        """
        source = tmp_path / "writtenroot"
        _cookie_deck(source, "deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._scan(source, "--write-baseline", str(baseline))

        renamed = tmp_path / "otherroot"
        _cookie_deck(renamed, "deck.http-cassette.yaml")
        code, output = self._scan(renamed, "--baseline", str(baseline))
        assert code == 0
        assert "writtenroot" in output and "otherroot" in output, output

    def test_an_unreadable_cassette_is_not_called_re_recorded(self, tmp_path: Path) -> None:
        """The report used to contradict itself in consecutive lines.

        It said the file could not be read, then listed its baseline entry
        as "cleared — that deck was re-recorded". And `troubleshooting.md`
        files "cleared" under things that are *not* a problem, so the doc
        vouched for a corrupt cassette. The bracketed directory name keeps
        the skipped-file line honest too — that line prints a path.
        """
        deck = _cookie_deck(tmp_path, "m0[raw]/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._scan(tmp_path, "--write-baseline", str(baseline))
        deck.write_text("a: b: c", encoding="utf-8")

        code, output = self._scan(tmp_path, "--baseline", str(baseline))
        assert code != 0
        assert "could not be read" in output
        assert "re-recorded" not in output
        # The *skipped-file* line specifically — asserting the bracket
        # anywhere in the output was satisfied by the stale-entry list
        # below it, so dropping the escaping here went unnoticed.
        skipped_lines = [line for line in output.splitlines() if "skipped (" in line]
        assert skipped_lines, output
        assert any("m0[raw]" in line for line in skipped_lines), skipped_lines

    def test_a_bracketed_path_survives_the_report(self, tmp_path: Path) -> None:
        """``PythonCourses[old]`` is a legal directory name.

        Rich ate the bracketed part, so the report named a path that does
        not exist — and an unbalanced closing tag raised ``MarkupError``
        straight out of the CLI. The **stale-entry list** is exactly where
        such a path gets printed, so the bracket goes in a path *inside* the
        root as well as on the root itself.
        """
        root = tmp_path / "PythonCourses[old]"
        deck = _cookie_deck(root, "m0[v2]/deck.http-cassette.yaml")
        baseline = tmp_path / "baseline.json"
        self._scan(root, "--write-baseline", str(baseline))
        deck.unlink()

        code, output = self._scan(root, "--baseline", str(baseline))
        assert code != 0
        assert "PythonCourses[old]" in output, output
        assert "m0[v2]/deck.http-cassette.yaml" in output, output

    def test_an_empty_tree_names_a_bracketed_root_correctly(self, tmp_path: Path) -> None:
        """The same bug in the empty-tree branch — the round-1 scenario.

        An operator asking "why did nothing get scanned?" was shown a path
        with the brackets silently eaten, i.e. a directory that does not
        exist.
        """
        root = tmp_path / "dist[v2]"
        root.mkdir()
        code, output = self._scan(root)
        assert code == 0
        assert "dist[v2]" in output, output


class TestTheRootNameWarning:
    """Pure, because a Rich-printed warning is one nothing can assert.

    Three separate mutations of this rule survived the entire suite: never
    emitting the warning, never computing it, and emitting it for a baseline
    that records no root at all.
    """

    def test_a_mismatch_warns(self) -> None:
        from clm.cli.commands.cassette import root_name_warning

        message = root_name_warning("slides", "otherdir")
        assert message is not None
        assert "slides" in message and "otherdir" in message

    def test_the_recorded_root_is_read_back_out_of_the_document(self, tmp_path: Path) -> None:
        """Otherwise the warning is computed from ``None`` and never fires.

        Making ``baseline_root_name`` always return ``None`` left the whole
        suite green: the pure warning tests bypass it, and the CLI test only
        asserted an exit code the warning does not affect.
        """
        from clm.workers.notebook.cassette_doctor import baseline_root_name

        _cookie_deck(tmp_path, "deck.http-cassette.yaml")
        document = build_baseline(_scan(tmp_path), tmp_path)
        assert baseline_root_name(document) == tmp_path.name

    @pytest.mark.parametrize("document", [{}, {"root_name": 7}, {"root_name": None}, [], "x"])
    def test_a_document_without_a_usable_root_name_reads_as_none(self, document) -> None:
        from clm.workers.notebook.cassette_doctor import baseline_root_name

        assert baseline_root_name(document) is None

    def test_a_match_is_silent(self) -> None:
        from clm.cli.commands.cassette import root_name_warning

        assert root_name_warning("slides", "slides") is None

    @pytest.mark.parametrize("recorded", [None, ""])
    def test_a_baseline_without_a_root_name_is_silent(self, recorded) -> None:
        """An older or hand-written file records none — do not invent one.

        The mutant that dropped the ``recorded and`` guard printed
        "written for a root named 'None'" on every such run.
        """
        from clm.cli.commands.cassette import root_name_warning

        assert root_name_warning(recorded, "slides") is None

    def test_a_bracket_in_a_directory_name_is_escaped(self) -> None:
        """``PythonCourses[old]`` is a legal directory name.

        Unescaped, Rich ate the bracketed part and printed the *wrong* name
        — in the one message whose whole job is telling two roots apart —
        and an unbalanced closing tag raised ``MarkupError`` straight out of
        the CLI.
        """
        from rich.console import Console

        from clm.cli.commands.cassette import root_name_warning

        message = root_name_warning("PythonCourses[old]", "x[/notatag]y")
        assert message is not None
        console = Console(file=io.StringIO(), width=200, no_color=True)
        console.print(message)
        rendered = console.file.getvalue()
        assert "PythonCourses[old]" in rendered
        assert "x[/notatag]y" in rendered

    def test_a_bracket_in_a_finding_does_not_crash_the_report(self, tmp_path: Path) -> None:
        """Paths, header names and URIs all come from outside too."""
        from clm.cli.commands.cassette import _render_secret_report

        _write(
            tmp_path,
            "deck.http-cassette.yaml",
            [_interaction(response_headers={"set-cookie": "s=1"}, uri="https://x/[/red]")],
        )
        _render_secret_report(_scan(tmp_path))  # must not raise MarkupError
