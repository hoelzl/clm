"""Tests for slide validation via the unified ``clm validate`` command."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.main import cli


def _write_slide(tmp_path: Path, name: str, content: str) -> Path:
    """Write a slide file and return its path."""
    p = tmp_path / name
    p.write_text(dedent(content), encoding="utf-8")
    return p


class TestValidateSlidesCommand:
    def test_clean_file(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            #
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            #
            # ## Title

            # %% tags=["keep"]
            x = 1
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p)])

        assert result.exit_code == 0
        assert "OK" in result.output

    def test_errors_exit_nonzero(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad.py",
            """\
            # %% tags=["bogus_tag"]
            x = 1
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p)])

        assert result.exit_code == 1
        assert "ERROR" in result.output

    def test_preamble_code_warning_exit_zero(self, tmp_path):
        # A deck with code folded into the header cell (issue #253) warns but,
        # with the default --fail-on, still exits 0 (gate-safety). The DE/EN
        # pair keeps the pairing check clean so only the warning is present.
        p = _write_slide(
            tmp_path,
            "slides_preamble.py",
            """\
            # j2 from 'macros.j2' import header
            # {{ header("Regeln", "Rules") }}
            from typing import Iterable

            # %% [markdown] lang="de" tags=["slide"] slide_id="gh"
            #
            # ## Hinweise

            # %% [markdown] lang="en" tags=["slide"] slide_id="gh"
            #
            # ## Hints
            """,
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p)])
        assert result.exit_code == 0
        assert "WARN" in result.output
        assert "#253" in result.output

    def test_json_output(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            #
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            #
            # ## Title
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p), "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["files_checked"] == 1
        assert data["findings"] == []

    def test_json_output_with_errors(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad.py",
            """\
            # %% tags=["bogus_tag"]
            x = 1
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p), "--json"])

        assert result.exit_code == 0  # JSON mode doesn't set exit code
        data = json.loads(result.output)
        assert len(data["findings"]) == 1
        assert data["findings"][0]["category"] == "tags"

    def test_quick_mode(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            # ## Title
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p), "--quick"])

        assert result.exit_code == 0
        assert "OK" in result.output

    def test_quick_catches_unclosed_start(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_unclosed.py",
            """\
            # %% tags=["start"]
            # starter code
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p), "--quick"])

        assert result.exit_code == 1
        assert "start" in result.output

    def test_checks_filter(self, tmp_path):
        # File with tag error but no format error
        p = _write_slide(
            tmp_path,
            "slides_tags.py",
            """\
            # %% tags=["bogus_tag"]
            x = 1
            """,
        )

        runner = CliRunner()
        # Only run format checks — should not catch tag issue
        result = runner.invoke(cli, ["validate", str(p), "--checks", "format"])

        assert result.exit_code == 0

    def test_directory_validation(self, tmp_path):
        topic_dir = tmp_path / "topic_010_intro"
        topic_dir.mkdir()
        (topic_dir / "slides_intro.py").write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="title"\n#\n# ## Titel\n\n'
            '# %% [markdown] lang="en" tags=["slide"] slide_id="title"\n#\n# ## Title\n',
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(topic_dir)])

        assert result.exit_code == 0
        assert "OK" in result.output

    def test_course_spec_validation(self, tmp_path):
        # Set up a minimal course tree
        slides = tmp_path / "slides"
        m1 = slides / "module_100_basics"
        t1 = m1 / "topic_010_intro"
        t1.mkdir(parents=True)
        (t1 / "slides_intro.py").write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="title"\n#\n# ## Titel\n\n'
            '# %% [markdown] lang="en" tags=["slide"] slide_id="title"\n#\n# ## Title\n',
            encoding="utf-8",
        )

        specs = tmp_path / "course-specs"
        specs.mkdir()
        spec_path = specs / "test.xml"
        spec_path.write_text(
            dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <course>
                <name><de>Test</de><en>Test</en></name>
                <prog-lang>python</prog-lang>
                <description><de></de><en></en></description>
                <certificate><de></de><en></en></certificate>
                <sections><section>
                    <name><de>S</de><en>S</en></name>
                    <topics><topic>intro</topic></topics>
                </section></sections>
            </course>
            """),
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(spec_path), "--kind=slides"])

        assert result.exit_code == 0
        assert "OK" in result.output

    def test_invalid_check_name(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p), "--checks", "nonexistent"])

        assert result.exit_code != 0
        assert "Unknown check" in result.output

    def _warning_pair(self, tmp_path: Path) -> Path:
        # A balanced, id-carrying DE/EN pair whose only finding is a
        # tag-mismatch warning (extra `keep` tag on the DE half) — no
        # errors, so it exercises the warning-only paths.
        return _write_slide(
            tmp_path,
            "slides_warn.py",
            """\
            # %% [markdown] lang="de" tags=["slide", "keep"] slide_id="title"
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            # ## Title
            """,
        )

    def test_warning_without_fail_on_exits_zero(self, tmp_path):
        p = self._warning_pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p)])
        assert result.exit_code == 0
        assert "WARN" in result.output

    def test_fail_on_warning_exits_nonzero(self, tmp_path):
        p = self._warning_pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p), "--fail-on", "warning"])
        assert result.exit_code == 1
        assert "WARN" in result.output

    def test_fail_on_warning_clean_exits_zero(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="title"
            #
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"] slide_id="title"
            #
            # ## Title
            """,
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p), "--fail-on", "warning"])
        assert result.exit_code == 0

    def test_fail_on_error_with_warnings_exits_zero(self, tmp_path):
        # --fail-on error must NOT escalate on warning-only findings.
        p = self._warning_pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p), "--fail-on", "error"])
        assert result.exit_code == 0

    def test_fail_on_warning_json_exits_nonzero(self, tmp_path):
        # When --fail-on is explicit, it governs the exit code in JSON mode too.
        p = self._warning_pair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(p), "--json", "--fail-on", "warning"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert any(f["severity"] == "warning" for f in data["findings"])

    def test_fail_on_not_valid_with_spec(self, tmp_path):
        spec_path = tmp_path / "course.xml"
        spec_path.write_text("<course></course>", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(spec_path), "--fail-on", "warning"])
        assert result.exit_code != 0
        assert "slides-only" in result.output

    def test_review_material_in_json(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_print.py",
            """\
            # %% tags=["keep"]
            print(42)
            """,
        )

        runner = CliRunner()
        # Use the new canonical `validate` command — see test_json_output
        # above for rationale.
        result = runner.invoke(cli, ["validate", str(p), "--checks", "code_quality", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "review_material" in data
        assert "code_quality" in data["review_material"]
