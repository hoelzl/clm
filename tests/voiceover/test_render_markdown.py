"""Tests for clm.voiceover.compare.render_markdown + `voiceover report` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.voiceover.compare import render_markdown

_SAMPLE_REPORT = {
    "source": "/tmp/old.py",
    "target": "/tmp/new.py",
    "language": "de",
    "slide_count": 2,
    "status_totals": {
        "covered": 2,
        "rewritten": 1,
        "added": 0,
        "dropped": 1,
        "manual_review": 0,
    },
    "kind_totals": {"unchanged": 1, "modified": 1},
    "slides": [
        {
            "key": "intro",
            "kind": "unchanged",
            "target_index": 0,
            "source_index": 0,
            "content_similarity": 1.0,
            "outcomes": [
                {"status": "covered", "source": "say hi", "target": "say hi"},
                {
                    "status": "dropped",
                    "source": "history aside",
                    "target": "",
                    "note": "omitted intentionally",
                },
            ],
            "notes": None,
            "error": None,
        },
        {
            "key": "middle",
            "kind": "modified",
            "target_index": 1,
            "source_index": 1,
            "content_similarity": 0.8,
            "outcomes": [
                {
                    "status": "rewritten",
                    "source": "blah",
                    "target": "blah with polish",
                    "note": "refined",
                },
                {"status": "covered", "source": "thing", "target": "thing"},
            ],
            "notes": None,
            "error": None,
        },
    ],
}


class TestRenderMarkdown:
    def test_has_header(self):
        md = render_markdown(_SAMPLE_REPORT)
        assert md.startswith("# Voiceover Comparison")
        assert "Language: `de`" in md
        assert "Source: `/tmp/old.py`" in md

    def test_has_per_slide_table(self):
        md = render_markdown(_SAMPLE_REPORT)
        assert "## Summary per slide" in md
        assert "| intro |" in md or "`intro`" in md
        assert "| middle |" in md or "`middle`" in md

    def test_groups_dropped_items(self):
        md = render_markdown(_SAMPLE_REPORT)
        assert "## Dropped in current slides" in md
        assert "history aside" in md
        assert "omitted intentionally" in md

    def test_groups_rewritten_items(self):
        md = render_markdown(_SAMPLE_REPORT)
        assert "## Rewritten" in md
        assert "blah with polish" in md
        assert "refined" in md

    def test_omits_empty_buckets(self):
        md = render_markdown(_SAMPLE_REPORT)
        # No "added" outcomes, so the section must not appear.
        assert "## Added" not in md
        assert "## Manual review" not in md

    def test_escapes_pipes_in_slide_key(self):
        # Pipes would otherwise break the markdown table.
        report = dict(_SAMPLE_REPORT)
        report["slides"] = [
            {**_SAMPLE_REPORT["slides"][0], "key": "has|pipe"},
        ]
        md = render_markdown(report)
        assert "has\\|pipe" in md


class TestReportCommand:
    def test_markdown_output(self, tmp_path: Path):
        from clm.cli.commands.voiceover import voiceover_group

        in_json = tmp_path / "r.json"
        out_md = tmp_path / "r.md"
        in_json.write_text(json.dumps(_SAMPLE_REPORT), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["report", str(in_json), "--format", "markdown", "-o", str(out_md)],
        )
        assert result.exit_code == 0, result.output
        text = out_md.read_text(encoding="utf-8")
        assert text.startswith("# Voiceover Comparison")
        assert "## Dropped in current slides" in text

    def test_json_passthrough(self, tmp_path: Path):
        from clm.cli.commands.voiceover import voiceover_group

        in_json = tmp_path / "r.json"
        out_json = tmp_path / "out.json"
        in_json.write_text(json.dumps(_SAMPLE_REPORT), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["report", str(in_json), "--format", "json", "-o", str(out_json)],
        )
        assert result.exit_code == 0, result.output
        roundtrip = json.loads(out_json.read_text(encoding="utf-8"))
        assert roundtrip["status_totals"]["dropped"] == 1
