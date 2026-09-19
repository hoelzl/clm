"""Regression tests for #960: historical artifacts without embedded judgment."""

from __future__ import annotations

import ast
import builtins
import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.harvest import harvest_group


@pytest.fixture(autouse=True)
def block_embedded_imports(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name not in {
            "clm.voiceover.merge",
            "clm.voiceover.port",
            "clm.voiceover.compare",
            "clm.infrastructure.llm.client",
        }, f"embedded import: {name}"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


def test_legacy_verbs_only_available_under_autopilot() -> None:
    retired = {"port", "compare", "backfill", "sync-at-rev", "compare-from-inventory"}
    assert not retired.intersection(harvest_group.commands)
    assert "export-at-rev" in harvest_group.commands
    result = CliRunner().invoke(harvest_group, ["autopilot", "port", "--help"])
    assert result.exit_code == 0, result.output
    assert "SOURCE" in result.output


def test_embedded_imports_are_confined_to_autopilot_functions() -> None:
    """#960: diagnostic/agent modules must not acquire a legacy-model dependency."""
    src = Path(__file__).resolve().parents[2] / "src" / "clm"
    allowed = {
        "voiceover/autopilot.py": None,
        "voiceover/merge.py": None,
        "voiceover/port.py": None,
        "voiceover/compare.py": None,
        "cli/commands/voiceover.py": {"sync", "_port_voiceover_notes", "_run_compare"},
    }
    forbidden = {
        "clm.infrastructure.llm.client",
        "clm.voiceover.merge",
        "clm.voiceover.port",
        "clm.voiceover.compare",
        "clm.voiceover.autopilot",
    }
    paths = [
        *(src / "voiceover").rglob("*.py"),
        *(src / "mcp").rglob("*.py"),
        src / "cli/commands/harvest.py",
        src / "cli/commands/voiceover.py",
    ]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [
                    node.module or "",
                    *[f"{node.module}.{alias.name}" for alias in node.names],
                ]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            if not forbidden.intersection(names):
                continue
            rel = path.relative_to(src).as_posix()
            assert rel in allowed, f"embedded import in {rel}:{node.lineno}"
            if allowed[rel] is not None:
                parent = node
                while parent in parents and not isinstance(
                    parent, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    parent = parents[parent]
                assert isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                assert parent.name in allowed[rel], f"embedded import in {rel}:{parent.name}"


def test_original_autopilot_spelling_still_dispatches_to_run(tmp_path: Path, monkeypatch) -> None:
    from clm.cli.commands.voiceover import sync

    deck = tmp_path / "slides.py"
    deck.write_text("# deck\n")
    captured = {}
    monkeypatch.setattr(sync, "callback", lambda **kwargs: captured.update(kwargs))
    result = CliRunner().invoke(harvest_group, ["autopilot", str(deck), "v.mp4", "--lang", "de"])
    assert result.exit_code == 0, result.output
    assert captured["slides"] == deck
    assert captured["videos"] == ("v.mp4",)
    assert captured["lang"] == "de"


@pytest.mark.parametrize("fmt", ["markdown", "json", "table"])
def test_report_rendering_cannot_import_embedded_modules(tmp_path: Path, fmt: str) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"slides": [], "source": "old.py", "target": "new.py"}))
    code = """
import sys
class BlockEmbedded:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'clm.voiceover.merge', 'clm.voiceover.port',
                        'clm.voiceover.compare', 'clm.infrastructure.llm.client'}:
            raise AssertionError('embedded import: ' + fullname)
sys.meta_path.insert(0, BlockEmbedded())
from click.testing import CliRunner
from clm.cli.commands.harvest import harvest_group
r = CliRunner().invoke(harvest_group, ['compare-report', sys.argv[1], '--format', sys.argv[2]])
assert r.exit_code == 0, (r.output, r.exception)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(report), fmt], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, encoding="utf-8", stderr=subprocess.PIPE
    ).strip()


@pytest.fixture
def historical_deck(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "course with spaces"
    repo.mkdir()
    _git(repo, "init", "-q")
    for lang in ("de", "en"):
        (repo / f"slides_t.{lang}.py").write_text(f"# historical {lang}: ü\n", encoding="utf-8")
        (repo / f"voiceover_t.{lang}.py").write_text(f"# narration {lang}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "old")
    rev = _git(repo, "rev-parse", "HEAD")
    (repo / "slides_t.de.py").write_text("# working copy\n", encoding="utf-8")
    (repo / "slides_t.en.py").unlink()
    (repo / "voiceover_t.de.py").unlink()
    return repo / "slides_t.de.py", rev


def test_export_reads_historical_twin_and_companions(historical_deck, tmp_path: Path) -> None:
    deck, rev = historical_deck
    output = tmp_path / "export"
    before = _git(deck.parent, "status", "--porcelain")
    result = CliRunner().invoke(
        harvest_group, ["export-at-rev", str(deck), "--rev", rev, "-o", str(output), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["revision"] == rev
    assert Path(payload["deck"]) == output / deck.name
    for lang in ("de", "en"):
        assert (output / f"slides_t.{lang}.py").read_text(
            encoding="utf-8"
        ) == f"# historical {lang}: ü\n"
        assert (output / f"voiceover_t.{lang}.py").read_text(
            encoding="utf-8"
        ) == f"# narration {lang}\n"
    assert _git(deck.parent, "status", "--porcelain") == before


def test_export_refuses_existing_destination(historical_deck) -> None:
    deck, rev = historical_deck
    result = CliRunner().invoke(
        harvest_group, ["export-at-rev", str(deck), "--rev", rev, "-o", str(deck.parent)]
    )
    assert result.exit_code == 2, result.output
    assert "exist" in result.output.lower()
    assert deck.read_text(encoding="utf-8") == "# working copy\n"


@pytest.mark.parametrize("rev", ["nonexistent-revision", "--help"])
def test_export_invalid_revision_leaves_no_output(
    historical_deck, tmp_path: Path, rev: str
) -> None:
    deck, _ = historical_deck
    output = tmp_path / "export"
    result = CliRunner().invoke(
        harvest_group, ["export-at-rev", str(deck), "--rev", rev, "-o", str(output)]
    )
    assert result.exit_code == 2, result.output
    assert not output.exists()


def test_export_nested_historical_companion_precedence_and_bytes(
    historical_deck, tmp_path: Path
) -> None:
    deck, _ = historical_deck
    repo = deck.parent
    (repo / "voiceover").mkdir()
    companion = repo / "voiceover" / "voiceover_t.de.py"
    content = b"# historical CRLF\r\n"
    # Bypass checkout line-ending conversion: commit the exact blob.
    blob = (
        subprocess.check_output(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input=content
        )
        .decode()
        .strip()
    )
    _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},voiceover/voiceover_t.de.py")
    _git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "nested"
    )
    companion.write_text("# working copy differs\n")
    output = tmp_path / "snapshot"
    result = CliRunner().invoke(
        harvest_group, ["export-at-rev", str(deck), "--rev", "HEAD", "-o", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert (output / "voiceover" / companion.name).read_bytes() == content
    assert not (output / companion.name).exists()


def test_export_missing_at_revision_leaves_no_output(historical_deck, tmp_path: Path) -> None:
    deck, rev = historical_deck
    missing = deck.with_name("missing.py")
    output = tmp_path / "snapshot"
    result = CliRunner().invoke(
        harvest_group, ["export-at-rev", str(missing), "--rev", rev, "-o", str(output)]
    )
    assert result.exit_code == 2
    assert "does not exist at revision" in result.output
    assert not output.exists()


def test_export_deleted_directory_with_literal_path(historical_deck, tmp_path: Path) -> None:
    deck, _ = historical_deck
    parent = deck.parent / "old [topic]"
    parent.mkdir()
    old = parent / "slides_[old].py"
    old.write_text("# old deck\n", encoding="utf-8")
    _git(deck.parent, "--literal-pathspecs", "add", str(old))
    _git(
        deck.parent,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.org",
        "commit",
        "-qm",
        "old path",
    )
    old.unlink()
    parent.rmdir()
    output = tmp_path / "snapshot"
    result = CliRunner().invoke(
        harvest_group, ["export-at-rev", str(old), "--rev", "HEAD", "-o", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert (output / old.name).read_text(encoding="utf-8") == "# old deck\n"


def test_export_combined_cpp_deck_deleted_from_working_copy(
    historical_deck, tmp_path: Path
) -> None:
    deck, _ = historical_deck
    cpp = deck.with_name("slides_old.cpp")
    cpp.write_text("// %% [markdown]\n// Deutsch / English\n", encoding="utf-8")
    _git(deck.parent, "add", cpp.name)
    _git(
        deck.parent,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.org",
        "commit",
        "-qm",
        "cpp",
    )
    cpp.unlink()
    output = tmp_path / "snapshot"
    result = CliRunner().invoke(
        harvest_group, ["export-at-rev", str(cpp), "--rev", "HEAD", "-o", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert (output / cpp.name).read_text(
        encoding="utf-8"
    ) == "// %% [markdown]\n// Deutsch / English\n"


def test_export_to_port_accept_loop_with_companions(tmp_path: Path) -> None:
    from tests.cli.test_harvest_cli import _write_alignment, _write_fixture
    from tests.cli.test_harvest_task_accept import _accept, _single_update_answer, _task_for

    repo = tmp_path / "repo"
    repo.mkdir()
    deck, video = _write_fixture(repo)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "old")
    output = tmp_path / "old"
    runner = CliRunner()
    result = runner.invoke(
        harvest_group, ["export-at-rev", str(deck), "--rev", "HEAD", "-o", str(output)]
    )
    assert result.exit_code == 0, result.output
    old = output / deck.name
    alignment = _write_alignment(output, old)
    report = runner.invoke(
        harvest_group,
        ["report", str(old), str(video), "--lang", "de", "--alignment", str(alignment), "--json"],
    )
    assert report.exit_code == 1, report.output
    task = _task_for(output, old, video, "s1")
    answer = _single_update_answer(task, {"de": ["Recovered narration."]}, None)
    accepted = _accept(output, old, answer)
    assert accepted.exit_code == 0, accepted.output
    framed = runner.invoke(
        harvest_group,
        [
            "task",
            str(deck),
            "--lang",
            "de",
            "--kind",
            "port",
            "--source",
            str(old),
            "--slide",
            "s1",
        ],
    )
    assert framed.exit_code == 0, framed.output
    port = json.loads(framed.output)["tasks"][0]
    assert "Recovered narration." in port["inputs"]["prior_bullets"]
    answer = {
        "item": port["item"],
        "kind": "port",
        "baseline_fingerprints": port["baseline_fingerprints"],
        "updates": [{"member": None, "bullets": {"de": ["Ported narration."]}}],
        "dropped": [],
    }
    accepted = _accept(repo, deck, answer)
    assert accepted.exit_code == 0, accepted.output
    assert "Ported narration." in (repo / "voiceover" / "voiceover_t.de.py").read_text()
    verified = runner.invoke(harvest_group, ["verify", str(deck)])
    assert verified.exit_code == 0, verified.output


def test_compare_companion_freshness_and_model_free_accept(tmp_path: Path) -> None:
    from clm.voiceover.harvest_compare import file_fingerprint
    from tests.cli.test_harvest_cli import _write_fixture

    deck, _ = _write_fixture(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        harvest_group,
        ["task", str(deck), "--lang", "de", "--kind", "compare", "--source", str(deck)],
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["tasks"], "companion-only narration must be compared"
    answer = {
        "kind": "compare",
        "source_fingerprint": envelope["source_fingerprint"],
        "target_fingerprint": envelope["target_fingerprint"],
        "verdicts": [{"item": t["item"], "outcomes": []} for t in envelope["tasks"]],
    }
    answer_path = tmp_path / "answer.json"
    answer_path.write_text(json.dumps(answer))
    args = ["compare-accept", str(deck), str(deck), "--lang", "de", "--answer", str(answer_path)]
    result = runner.invoke(harvest_group, args)
    assert result.exit_code == 0, result.output
    companion = tmp_path / "voiceover" / "voiceover_t.de.py"
    companion.write_text(companion.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    assert file_fingerprint(deck) != envelope["source_fingerprint"]
    result = runner.invoke(harvest_group, args)
    assert result.exit_code == 2, result.output
    assert "changed since" in result.output
