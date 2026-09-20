"""CLI tests for the ``clm slides coverage`` agent-toolkit verbs (#963).

``report`` frames the judgment (pending pairs + cached verdicts + the
judge's prompt, no model, no Ollama import); ``accept`` validates the
answer (shape + per-pair freshness + exact coverage) and banks the
verdicts into the existing CoverageCache rows; the in-process Ollama
judge lives behind ``autopilot``. Plus the ``coverage-report`` →
``language-coverage`` rename (#963, owner-ratified).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.infrastructure.llm.cache import CoverageCache
from clm.slides.agent_task import VALIDATORS

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

_DECK = (
    '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
    "#\n"
    "# ## Einführung\n"
    "#\n"
    "# - Erster Punkt\n"
    "# - Zweiter Punkt\n"
    "\n"
    '# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n'
    "#\n"
    "# - Wir beginnen mit dem ersten Punkt.\n"
    "\n"
    '# %% [markdown] lang="de" tags=["slide"] slide_id="demo"\n'
    "#\n"
    "# ## Demo\n"
    "#\n"
    "# - Demo Punkt\n"
    "\n"
    '# %% [markdown] lang="de" tags=["voiceover"] for_slide="demo"\n'
    "#\n"
    "# - Hier zeigen wir die Demo.\n"
    "\n"
    '# %% [markdown] lang="de" tags=["slide"] slide_id="bare"\n'
    "#\n"
    "# ## Ohne Voiceover\n"
    "#\n"
    "# - Unnarrated bullet\n"
    "\n"
)


def _write_deck(tmp_path: Path) -> Path:
    deck = tmp_path / "slides_demo.de.py"
    deck.write_text(_DECK, encoding="utf-8", newline="\n")
    return deck


def _invoke(runner: CliRunner, *args: str):
    from clm.cli.main import cli

    return runner.invoke(cli, ["slides", "coverage", *args])


def _report(runner: CliRunner, deck: Path, *extra: str) -> dict:
    result = _invoke(runner, "report", str(deck), "--json", *extra)
    assert result.exit_code in (0, 1), result.output
    return json.loads(result.output)


def _answer_for(report_payload: dict, *, covered: bool = True) -> dict:
    rows = []
    for item in report_payload["items"]:
        if item["status"] != "pending":
            continue
        rows.append(
            {
                "slide_hash": item["slide_hash"],
                "voiceover_hash": item["voiceover_hash"],
                "lang": item["lang"],
                "verdict": "covered" if covered else "gaps",
                "bullets": [
                    {"text": b, "covered": covered, "reason": "ok" if covered else "missing"}
                    for b in item["bullets"]
                ],
            }
        )
    return {
        "schema": report_payload["schema"],
        "prompt_version": report_payload["prompt_version"],
        "pairs": rows,
    }


def _accept(runner: CliRunner, deck: Path, answer: dict, *extra: str):
    answer_file = deck.parent / "answer.json"
    answer_file.write_text(json.dumps(answer), encoding="utf-8")
    return _invoke(runner, "accept", str(deck), "--answer", str(answer_file), "--json", *extra)


# --------------------------------------------------------------------------
# report (the bare default verb)
# --------------------------------------------------------------------------


class TestCoverageReport:
    def test_bare_coverage_runs_report(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        runner = CliRunner()
        result = _invoke(runner, str(deck))
        assert result.exit_code == 1, result.output  # findings + pending = work pending
        assert "pending" in result.output

    def test_report_frames_pending_and_findings(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        runner = CliRunner()
        payload = _report(runner, deck, "--cache-dir", str(tmp_path / "cache"))
        assert payload["schema"] == 1
        assert payload["tool"] == "coverage"
        assert payload["verb"] == "report"
        statuses = {i["slide_id"]: i["status"] for i in payload["items"]}
        assert statuses == {"intro": "pending", "demo": "pending", "bare": "no-voiceover"}
        intro = payload["items"][0]
        assert intro["bullets"] == ["Erster Punkt", "Zweiter Punkt"]
        assert "ersten Punkt" in intro["voiceover"]
        assert payload["counts"]["pending"] == 2
        # The framing embeds the judge's system prompt verbatim.
        assert "semantic coverage" in payload["instructions"]
        assert payload["validator"] == "coverage-verdicts"
        assert payload["prompt_version"] == "v1"

    def test_report_surfaces_cached_verdicts(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        cache_dir = tmp_path / "cache"
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(cache_dir))
        answer = _answer_for(first, covered=False)  # bank a gap
        result = _accept(runner, deck, answer, "--cache-dir", str(cache_dir))
        assert result.exit_code in (0, 1), result.output
        payload = _report(runner, deck, "--cache-dir", str(cache_dir))
        statuses = {i["slide_id"]: i["status"] for i in payload["items"]}
        assert statuses == {"intro": "cached", "demo": "cached", "bare": "no-voiceover"}
        assert payload["items"][0]["verdict"]["verdict"] == "gaps"
        # A banked gap is a finding — the same read autopilot's cache-only
        # mode performs.
        assert any("does not cover" in f["message"] for f in payload["findings"])
        # …and a fresh covered verdict does not duplicate one.
        second = _report(runner, deck, "--cache-dir", str(tmp_path / "fresh"))
        answer2 = _answer_for(second)
        assert _accept(runner, deck, answer2, "--cache-dir", str(tmp_path / "fresh")).exit_code == 1
        clean = _report(runner, deck, "--cache-dir", str(tmp_path / "fresh"))
        assert all("no voiceover" in f["message"] for f in clean["findings"])

    def test_report_dump_still_works(self, tmp_path: Path):
        runner = CliRunner()
        result = _invoke(runner, "report", "--dump", "--cache-dir", str(tmp_path))
        assert result.exit_code == 0, result.output
        assert "no cached verdicts" in result.output
        result = _invoke(runner, "report", "--dump", "--json", "--cache-dir", str(tmp_path))
        assert result.output.strip() == "[]"

    def test_report_requires_path_unless_dump(self, tmp_path: Path):
        runner = CliRunner()
        result = _invoke(runner, "report", "--cache-dir", str(tmp_path))
        assert result.exit_code != 0
        assert "PATH is required" in result.output


# --------------------------------------------------------------------------
# accept — the validated bank
# --------------------------------------------------------------------------


class TestCoverageAccept:
    def test_accept_banks_verdicts_and_emits_findings(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        cache_dir = tmp_path / "cache"
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(cache_dir))
        answer = _answer_for(first, covered=False)  # judge says: gaps
        result = _accept(runner, deck, answer, "--cache-dir", str(cache_dir))
        assert result.exit_code == 1, result.output  # banked, but the gap is a finding
        payload = json.loads(result.output)
        assert payload["tool"] == "coverage"
        assert payload["verb"] == "accept"
        assert payload["verdicts_banked"] == 2
        assert any("does not cover" in f["message"] for f in payload["findings"])

        # The banked row is readable through the plain CoverageCache API —
        # the same key/columns the embedded judge wrote.
        item = first["items"][0]
        cache = CoverageCache(cache_dir / "clm-llm.sqlite")
        try:
            row = cache.get(item["slide_hash"], item["voiceover_hash"], "v1", item["lang"])
        finally:
            cache.close()
        assert row is not None
        verdict, gap_details = row
        assert verdict == "gaps"
        assert json.loads(gap_details)["verdict"] == "gaps"

    def test_accept_clean_when_covered(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        cache_dir = tmp_path / "cache"
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(cache_dir))
        result = _accept(runner, deck, _answer_for(first), "--cache-dir", str(cache_dir))
        assert result.exit_code == 1, result.output  # the no-voiceover finding remains
        payload = json.loads(result.output)
        assert payload["verdicts_banked"] == 2
        assert all("no voiceover" in f["message"] for f in payload["findings"])

    def test_accept_stale_deck_rejected_wholesale(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        cache_dir = tmp_path / "cache"
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(cache_dir))
        deck.write_text(
            _DECK.replace("- Zweiter Punkt", "- Zweiter Punkt (geändert)"),
            encoding="utf-8",
            newline="\n",
        )
        result = _accept(runner, deck, _answer_for(first), "--cache-dir", str(cache_dir))
        assert result.exit_code == 2, result.output
        assert "exactly the pending pairs" in result.output

    def test_accept_missing_row_rejected(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(tmp_path / "cache"))
        answer = _answer_for(first)
        answer["pairs"] = answer["pairs"][:1]  # answers only one of the two pending
        result = _accept(runner, deck, answer, "--cache-dir", str(tmp_path / "cache"))
        assert result.exit_code == 2, result.output
        assert "missing answers" in result.output

    def test_accept_rewritten_bullet_text_rejected(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        cache_dir = tmp_path / "cache"
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(cache_dir))
        answer = _answer_for(first)
        answer["pairs"][0]["bullets"][0]["text"] = "A different bullet"
        result = _accept(runner, deck, answer, "--cache-dir", str(cache_dir))
        assert result.exit_code == 2, result.output
        assert "different bullet texts" in result.output

    def test_accept_contradictory_verdict_rejected(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        cache_dir = tmp_path / "cache"
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(cache_dir))
        answer = _answer_for(first, covered=False)
        answer["pairs"][0]["verdict"] = "covered"  # claims covered, bullets say gaps
        result = _accept(runner, deck, answer, "--cache-dir", str(cache_dir))
        assert result.exit_code == 2, result.output
        assert "contradicts" in result.output

    def test_accept_prompt_version_mismatch_rejected(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(tmp_path / "cache"))
        answer = _answer_for(first)
        answer["prompt_version"] = "v0"
        result = _accept(runner, deck, answer, "--cache-dir", str(tmp_path / "cache"))
        assert result.exit_code == 2, result.output
        assert "prompt_version mismatch" in result.output

    def test_accept_dry_run_banks_nothing(self, tmp_path: Path):
        deck = _write_deck(tmp_path)
        cache_dir = tmp_path / "cache"
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(cache_dir))
        result = _accept(
            runner, deck, _answer_for(first), "--cache-dir", str(cache_dir), "--dry-run"
        )
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["verdicts_banked"] == 0
        payload = _report(runner, deck, "--cache-dir", str(cache_dir))
        assert payload["counts"]["pending"] == 2  # still pending — nothing banked

    def test_accept_idempotent_reanswer_rejected(self, tmp_path: Path):
        """A pair already cached is not answerable — the second accept of the
        same answer is refused (the pair is no longer pending)."""
        deck = _write_deck(tmp_path)
        cache_dir = tmp_path / "cache"
        runner = CliRunner()
        first = _report(runner, deck, "--cache-dir", str(cache_dir))
        answer = _answer_for(first)
        assert _accept(runner, deck, answer, "--cache-dir", str(cache_dir)).exit_code == 1
        result = _accept(runner, deck, answer, "--cache-dir", str(cache_dir))
        assert result.exit_code == 2, result.output
        assert "not pending" in result.output

    def test_duplicate_content_pairs_answerable_once(self, tmp_path: Path):
        """Two pairs with identical slide + voiceover content (a recap slide
        across decks) share one cache key: the report frames the first as
        pending and the rest as duplicates — one answer row banks them all."""
        runner = CliRunner()
        for name in ("slides_a.de.py", "slides_b.de.py"):
            (tmp_path / name).write_text(_DECK, encoding="utf-8", newline="\n")
        cache_dir = tmp_path / "cache"
        payload = _report(runner, tmp_path, "--cache-dir", str(cache_dir))
        statuses = [i["status"] for i in payload["items"]]
        assert statuses.count("pending") == 2  # intro + demo, once for BOTH decks
        assert statuses.count("duplicate") == 2
        result = _accept(runner, tmp_path, _answer_for(payload), "--cache-dir", str(cache_dir))
        assert result.exit_code == 1, result.output  # the no-voiceover finding
        assert json.loads(result.output)["verdicts_banked"] == 2
        after = _report(runner, tmp_path, "--cache-dir", str(cache_dir))
        assert sum(1 for i in after["items"] if i["status"] == "cached") == 4


# --------------------------------------------------------------------------
# Model-free default path + rename
# --------------------------------------------------------------------------


def test_coverage_cli_module_never_imports_ollama_client_at_top_level():
    """#963 acceptance: the report/accept path must not import the Ollama
    client — pinned at the AST level (function-level imports only)."""
    import clm.cli.commands.slides.coverage as cli_module

    tree = ast.parse(Path(cli_module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:  # top-level statements only
        if isinstance(node, ast.ImportFrom) and node.module and "ollama" in node.module:
            raise AssertionError(f"top-level import of {node.module} in the coverage CLI module")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "ollama" not in alias.name, f"top-level import of {alias.name}"


def test_coverage_verdicts_validator_registered():
    import clm.slides.coverage_task  # noqa: F401 — registration at import

    assert "coverage-verdicts" in VALIDATORS.names()


def test_coverage_report_renamed_to_language_coverage(tmp_path: Path):
    """The #963 rename: `coverage-report` is gone, `language-coverage` answers."""
    from clm.cli.main import cli

    runner = CliRunner()
    old = runner.invoke(cli, ["slides", "coverage-report", str(tmp_path)])
    assert old.exit_code != 0

    slides = tmp_path / "slides_only_de.de.py"
    slides.write_text(
        '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n#\n# ## A\n',
        encoding="utf-8",
    )
    result = runner.invoke(cli, ["slides", "language-coverage", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decks"][0]["status"] == "de_only"
