"""Tests for the normalize-slides CLI command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.slides.normalize import normalize_slides_cmd


def _write_slide(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestNormalizeSlidesCmd:
    def test_no_changes_exit_0(self, tmp_path):
        # Shared code cell: no operation has anything to do — it's not a
        # slide-start, has no alt/start tags, no workshop heading, and no
        # DE/EN pair to interleave.
        path = _write_slide(
            tmp_path / "slides_test.py",
            "# %%\nx = 1\n",
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path)])
        assert result.exit_code == 0
        assert "No changes needed" in result.output

    def test_explicit_interleaving_on_split_half_reports_skip(self, tmp_path):
        # Regression test for #631: `--operations interleaving` on a split half
        # is intentionally skipped (#611) but must SAY so — exit 0 either way.
        path = _write_slide(
            tmp_path / "slides_test.de.py",
            '# %% [markdown] lang="de" tags=["slide"]\n# # Folie\n',
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path), "--operations", "interleaving"])
        assert result.exit_code == 0, result.output
        assert "[SKIPPED]" in result.output
        assert "split half" in result.output

        as_json = runner.invoke(
            normalize_slides_cmd, [str(path), "--operations", "interleaving", "--json"]
        )
        assert as_json.exit_code == 0, as_json.output
        data = json.loads(as_json.output)
        assert data["status"] == "clean"
        notices = data["notices"]
        assert len(notices) == 1
        assert notices[0]["operation"] == "interleaving"
        assert "split half" in notices[0]["message"]

    def test_confirm_pairs_applies_interleave_and_converges(self, tmp_path):
        # #236 agent flow over the CLI: --json worklist → --confirm-pairs (stdin) →
        # reordered + exit 0 → a plain re-run is clean.
        path = _write_slide(
            tmp_path / "slides_test.py",
            '# %% lang="de"\ndef begruessung():\n    return "Hallo"\n\n'
            '# %% lang="de"\ndef abschied():\n    return "Tschuess"\n\n'
            '# %% lang="en"\ndef greeting():\n    return "Hello"\n\n'
            '# %% lang="en"\ndef farewell():\n    return "Goodbye"\n',
        )
        runner = CliRunner()
        # 1. Worklist.
        wl = runner.invoke(
            normalize_slides_cmd,
            [str(path), "--operations", "interleaving", "--json", "--dry-run"],
        )
        assert wl.exit_code == 2, wl.output  # review items, no mechanical change → blocked
        items = json.loads(wl.output)["review_items"]
        fails = [it for it in items if it["issue"] == "similarity_failure"]
        assert len(fails) == 2
        confirm = json.dumps(
            [{"de_line": it["de_cell"]["line"], "en_line": it["en_cell"]["line"]} for it in fails]
        )
        # 2. Confirm via stdin → reorder, exit 0.
        applied = runner.invoke(
            normalize_slides_cmd,
            [str(path), "--operations", "interleaving", "--confirm-pairs", "-"],
            input=confirm,
        )
        assert applied.exit_code == 0, applied.output
        out = path.read_text(encoding="utf-8")
        assert out.index("greeting") < out.index("abschied")  # EN1 now follows DE1
        # 3. Verify: plain re-run is clean.
        verify = runner.invoke(normalize_slides_cmd, [str(path), "--operations", "interleaving"])
        assert verify.exit_code == 0, verify.output
        assert "No changes needed" in verify.output

    def test_confirm_pairs_rejects_a_directory(self, tmp_path):
        (tmp_path / "d").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            normalize_slides_cmd, [str(tmp_path / "d"), "--confirm-pairs", "-"], input="[]"
        )
        assert result.exit_code == 2
        assert "single slide" in result.output.lower()

    def test_tag_migration(self, tmp_path):
        path = _write_slide(
            tmp_path / "slides_test.py",
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n',
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path)])
        assert result.exit_code == 0
        assert "tag_migration" in result.output

    def test_placeholder_start(self, tmp_path):
        path = _write_slide(
            tmp_path / "slides_test.py",
            '# %% tags=["start"]\n# Your solution here\n\n'
            '# %% [markdown] tags=["completed"]\n# Discussion.\n',
        )
        runner = CliRunner()
        result = runner.invoke(
            normalize_slides_cmd, [str(path), "--operations", "placeholder_start"]
        )
        assert result.exit_code == 0
        assert "placeholder_start" in result.output
        new_text = path.read_text(encoding="utf-8")
        assert '"start"' not in new_text
        assert 'tags=["alt"]' in new_text

    def test_dry_run(self, tmp_path):
        text = '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path), "--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert path.read_text(encoding="utf-8") == text

    def test_json_output(self, tmp_path):
        path = _write_slide(
            tmp_path / "slides_test.py",
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n',
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "applied"
        assert len(data["changes"]) == 1

    def test_preamble_code_operation(self, tmp_path):
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Regeln", "Rules") }}\n'
            "from typing import Iterable\n\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n#\n# ## A\n'
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path), "--operations", "preamble_code"])
        assert result.exit_code == 0
        assert "preamble_code" in result.output
        assert "# %%\nfrom typing import Iterable" in path.read_text(encoding="utf-8")

    def test_preamble_code_dry_run_reports_without_writing(self, tmp_path):
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Regeln", "Rules") }}\n'
            "from typing import Iterable\n\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n#\n# ## A\n'
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        runner = CliRunner()
        result = runner.invoke(
            normalize_slides_cmd, [str(path), "--operations", "preamble_code", "--dry-run"]
        )
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert "preamble_code" in result.output
        assert path.read_text(encoding="utf-8") == text

    def test_review_items_exit_2(self, tmp_path):
        # Count mismatch produces review items → exit 2 when no other changes
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n# Folie 1\n\n'
            '# %% [markdown] lang="de" tags=["subslide"]\n# Extra\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# Slide 1\n'
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path), "--operations", "interleaving"])
        # Has review items but no changes → exit 2
        assert result.exit_code == 2

    def test_review_items_with_changes_exit_1(self, tmp_path):
        # Count mismatch (2 DE markdown vs 1 EN) → 1 review item; the
        # cells carry real markdown headings, so slide_ids still assigns
        # ids → exit 1 (partial: changes + review items).
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n# # Folie 1\n\n'
            '# %% [markdown] lang="de" tags=["subslide"]\n# # Extra\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# # Slide 1\n'
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path)])
        # Has review items AND changes → exit 1
        assert result.exit_code == 1

    def test_operations_filter(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# ## Workshop: Test\n"
            "\n"
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n'
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        runner = CliRunner()
        result = runner.invoke(
            normalize_slides_cmd,
            [str(path), "--operations", "tag_migration", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        ops = {c["operation"] for c in data["changes"]}
        assert "tag_migration" in ops
        assert "workshop_tags" not in ops

    def test_invalid_operation(self, tmp_path):
        path = _write_slide(tmp_path / "slides_test.py", "# %%\nx = 1\n")
        runner = CliRunner()
        result = runner.invoke(
            normalize_slides_cmd,
            [str(path), "--operations", "bogus"],
        )
        assert result.exit_code != 0
        assert "Unknown operation" in result.output

    def test_directory_input(self, tmp_path):
        topic = tmp_path / "topic_010_test"
        topic.mkdir()
        _write_slide(
            topic / "slides_test.py",
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n',
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(topic)])
        assert result.exit_code == 0
        assert "tag_migration" in result.output


_STAMP_DE = (
    '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
    "# ## Einführung\n"
    "\n"
    '# %% [markdown] lang="de" tags=["voiceover"] slide_id="intro"\n'
    "# Willkommen zur Einführung in dieses Thema.\n"
    "\n"
    '# %% [markdown] lang="de"\n'
    "# Ein lokalisierter Hinweis ohne Bezeichner.\n"
)
_STAMP_EN = (
    '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n'
    "# ## Introduction\n"
    "\n"
    '# %% [markdown] lang="en" tags=["voiceover"] slide_id="intro"\n'
    "# Welcome to the introduction of this topic.\n"
    "\n"
    '# %% [markdown] lang="en"\n'
    "# A localized note without an identifier.\n"
)


class TestNormalizeStampIds:
    """`normalize --stamp-ids` — sync-v3 Phase 0 (#520)."""

    def _pair(self, tmp_path: Path) -> tuple[Path, Path]:
        topic = tmp_path / "topic_010_test"
        de = _write_slide(topic / "slides_test.de.py", _STAMP_DE)
        en = _write_slide(topic / "slides_test.en.py", _STAMP_EN)
        return de, en

    def test_stamps_split_pair_in_directory(self, tmp_path):
        de, en = self._pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(de.parent), "--stamp-ids"])
        assert result.exit_code == 0, result.output
        assert "stamp_ids" in result.output
        de_text = de.read_text(encoding="utf-8")
        en_text = en.read_text(encoding="utf-8")
        # The voiceover no longer shares the slide's id; both halves agree.
        assert de_text.count('slide_id="intro"') == 1
        assert de_text.splitlines()[3] == en_text.splitlines()[3].replace('lang="en"', 'lang="de"')

    def test_dry_run_writes_nothing(self, tmp_path):
        de, en = self._pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(de.parent), "--stamp-ids", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert de.read_text(encoding="utf-8") == _STAMP_DE
        assert en.read_text(encoding="utf-8") == _STAMP_EN

    def test_json_report_shape(self, tmp_path):
        de, _en = self._pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(de.parent), "--stamp-ids", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["files_modified"] == 2
        assert all(c["operation"] == "stamp_ids" for c in payload["changes"])
        assert any("narrative" in c["description"] for c in payload["changes"])

    def test_single_split_half_expands_to_pair(self, tmp_path):
        de, en = self._pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(de), "--stamp-ids"])
        assert result.exit_code == 0, result.output
        # Both halves were stamped even though only one was named.
        assert de.read_text(encoding="utf-8") != _STAMP_DE
        assert en.read_text(encoding="utf-8") != _STAMP_EN

    def test_rejects_operations_combination(self, tmp_path):
        de, _en = self._pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            normalize_slides_cmd,
            [str(de.parent), "--stamp-ids", "--operations", "cell_spacing"],
        )
        assert result.exit_code != 0
        assert "replaces the regular operations" in result.output

    def test_rejects_canonicalize_combination(self, tmp_path):
        de, _en = self._pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            normalize_slides_cmd,
            [str(de.parent), "--stamp-ids", "--canonicalize-start-completed"],
        )
        assert result.exit_code != 0
        assert "interleaving operation" in result.output

    def test_prefixless_deck_discovered_and_stamped(self, tmp_path):
        # The sync surface supports prefix-less split decks (apis.de.py);
        # the one-time Phase-0 migration must reach them too.
        topic = tmp_path / "topic_030_apis"
        de = _write_slide(topic / "apis.de.py", _STAMP_DE)
        en = _write_slide(topic / "apis.en.py", _STAMP_EN)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(topic), "--stamp-ids"])
        assert result.exit_code == 0, result.output
        assert de.read_text(encoding="utf-8") != _STAMP_DE
        assert en.read_text(encoding="utf-8") != _STAMP_EN

    def test_changes_name_both_half_files(self, tmp_path):
        de, en = self._pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(de.parent), "--stamp-ids", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output[result.output.index("{") :])
        files = {Path(c["file"]).name for c in payload["changes"]}
        assert files == {de.name, en.name}

    def test_summary_grammar(self, tmp_path):
        de, _en = self._pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(de.parent), "--stamp-ids"])
        assert result.exit_code == 0, result.output
        assert "stamp ids." in result.output
        assert "stamp idss" not in result.output

    def test_rejects_spec_input(self, tmp_path):
        spec = tmp_path / "course.xml"
        spec.write_text("<course/>", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(spec), "--stamp-ids"])
        assert result.exit_code != 0
        assert "not a course spec" in result.output

    def test_refusals_surface_as_review_items(self, tmp_path):
        topic = tmp_path / "topic_020_solo"
        # A bilingual deck whose only stamp candidate has no adjacent twin.
        bilingual = _write_slide(
            topic / "slides_solo.py",
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "# ## Einführung\n"
            "\n"
            '# %% [markdown] lang="de"\n'
            "# Ein Hinweis nur auf Deutsch.\n",
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(bilingual), "--stamp-ids", "--json"])
        assert result.exit_code == 2, result.output  # review items, no changes
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["review_items"]
        assert payload["review_items"][0]["issue"] == "stamp_id_soft_refusal"
