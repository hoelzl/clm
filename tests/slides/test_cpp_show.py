"""Tests for ``clm slides cpp-show`` (#928): bare display expressions →
``SHOW(expr);`` with the display header included."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.cpp_show import cpp_show_cmd
from clm.slides.cpp_show import (
    INCLUDE_LINE,
    rewrite_deck_file,
    rewrite_deck_text,
    wrap_in_show,
)

HEADER = "// j2 from 'macros.j2' import header_en\n// {{ header_en('Title') }}\n\n"


def deck(*cells: str) -> str:
    return HEADER + "\n\n".join(cells) + "\n"


def code(body: str, header: str = "// %%") -> str:
    return f"{header}\n{body}"


def md(body: str, header: str = '// %% [markdown] lang="en" tags=["slide"]') -> str:
    return f"{header}\n// {body}"


class TestWrapInShow:
    def test_plain_expression(self):
        assert wrap_in_show("i1") == "SHOW(i1);"

    def test_trailing_line_comment_moves_after_the_call(self):
        assert wrap_in_show("i1  // the value") == "SHOW(i1);  // the value"

    def test_leading_comment_lines_stay_in_front(self):
        assert wrap_in_show("// what is it?\nx + 1") == "// what is it?\nSHOW(x + 1);"

    def test_multi_line_expression(self):
        assert wrap_in_show("sum(1,\n    2)") == "SHOW(sum(1,\n    2));"

    def test_string_literal_with_slashes_is_not_a_comment(self):
        assert wrap_in_show('std::string("a//b")') == 'SHOW(std::string("a//b"));'


class TestRewriteDeckText:
    def test_bare_expression_and_bare_call_become_show(self):
        result = rewrite_deck_text(
            deck(
                md("# Intro"),
                code("#include <iostream>"),
                code("int x{42};"),
                code("x"),
                code("print(x)"),
            )
        )
        assert [r.after for r in result.rewrites] == ["SHOW(x);", "SHOW(print(x));"]
        assert "// %%\nSHOW(x);\n" in result.text
        assert "// %%\nSHOW(print(x));\n" in result.text

    def test_statements_and_definitions_are_untouched(self):
        text = deck(
            code("#include <iostream>"),
            code("int x{42};"),
            code('std::cout << x << "\\n";'),
            code("void f() { x++; }"),
            code("f();"),
            code("struct P { int a; };"),
        )
        result = rewrite_deck_text(text)
        assert result.rewrites == []
        assert not result.changed
        assert result.text == text

    def test_macro_defined_test_body_is_not_a_display(self):
        # gtest's TEST_P(...) { ... } is a call followed by a block: a definition.
        text = deck(
            code("#include <gtest/gtest.h>"),
            code("TEST_P(Suite, Name)\n{\n    EXPECT_EQ(1, 1);\n}"),
            code("RUN_ALL_TESTS()"),
        )
        result = rewrite_deck_text(text)
        assert [r.after for r in result.rewrites] == ["SHOW(RUN_ALL_TESTS());"]
        assert "TEST_P(Suite, Name)\n{\n    EXPECT_EQ(1, 1);\n}" in result.text

    def test_include_is_appended_to_the_first_include_only_cell(self):
        result = rewrite_deck_text(
            deck(
                md("# T"),
                code("#include <iostream>\n#include <vector>", '// %% tags=["keep"]'),
                code("1 + 1"),
            )
        )
        assert result.include_added
        assert (
            '// %% tags=["keep"]\n#include <iostream>\n#include <vector>\n#include <clm/display.hpp>\n'
            in result.text
        )

    def test_include_gets_its_own_cell_when_the_first_code_cell_is_not_includes(self):
        result = rewrite_deck_text(deck(md("# T"), code("int x{1};"), code("x")))
        assert result.include_added
        assert f'// %% tags=["keep"]\n{INCLUDE_LINE}\n\n// %%\nint x{{1}};\n' in result.text

    def test_existing_include_is_not_duplicated(self):
        text = deck(code("#include <clm/display.hpp>"), code("int x{1};"), code("x"))
        result = rewrite_deck_text(text)
        assert not result.include_added
        assert result.text.count(INCLUDE_LINE) == 1

    def test_no_include_without_rewrites(self):
        text = deck(code("#include <iostream>"), code("int x{1};"))
        assert rewrite_deck_text(text).text == text

    def test_global_cell_is_skipped_and_reported(self):
        text = deck(
            code("#include <iostream>"), code("int g{1};\ng", '// %% tags=["global"]'), code("g")
        )
        result = rewrite_deck_text(text)
        assert [r.after for r in result.rewrites] == ["SHOW(g);"]
        assert result.skipped_global == [(7, "g")]
        assert 'tags=["global"]\nint g{1};\ng\n' in result.text

    def test_markdown_and_j2_cells_are_untouched(self):
        text = deck(md("x"), md("y + 1", "// %% [markdown]"), code("int x{1};"))
        assert rewrite_deck_text(text).text == text

    def test_comments_around_the_expression_survive(self):
        result = rewrite_deck_text(deck(code("int x{1};"), code("// Look at x:\nx  // still 1")))
        assert "// %%\n// Look at x:\nSHOW(x);  // still 1\n" in result.text

    def test_idempotent(self):
        first = rewrite_deck_text(
            deck(md("# T"), code("#include <iostream>"), code("int x{1};"), code("x"))
        )
        second = rewrite_deck_text(first.text)
        assert not second.changed
        assert second.text == first.text

    def test_several_display_cells(self):
        result = rewrite_deck_text(deck(code("int a{1}, b{2};"), code("a"), code("a + b")))
        assert "// %%\nSHOW(a);\n\n// %%\nSHOW(a + b);\n" in result.text

    def test_line_numbers_point_at_the_cell_header(self):
        text = deck(code("int x{1};"), code("x"))
        result = rewrite_deck_text(text)
        assert text.split("\n")[result.rewrites[0].line_number - 1] == "// %%"


class TestRewriteDeckFile:
    def test_writes_in_place_and_keeps_crlf(self, tmp_path: Path):
        path = tmp_path / "slides_x.en.cpp"
        path.write_bytes(deck(code("int x{1};"), code("x")).replace("\n", "\r\n").encode("utf-8"))
        result = rewrite_deck_file(path)
        assert result.changed
        raw = path.read_bytes()
        assert b"\r\nSHOW(x);\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")

    def test_dry_run_leaves_the_file(self, tmp_path: Path):
        path = tmp_path / "slides_x.en.cpp"
        text = deck(code("int x{1};"), code("x"))
        path.write_text(text, encoding="utf-8")
        result = rewrite_deck_file(path, dry_run=True)
        assert result.changed
        assert path.read_text(encoding="utf-8") == text


@pytest.fixture
def course(tmp_path: Path) -> Path:
    topic = tmp_path / "slides" / "module_100_basics" / "topic_110_vars"
    topic.mkdir(parents=True)
    for lang in ("en", "de"):
        (topic / f"slides_vars.{lang}.cpp").write_text(
            deck(md("# Vars"), code("#include <iostream>"), code("int x{1};"), code("x")),
            encoding="utf-8",
        )
    (topic / ".ipynb_checkpoints").mkdir()
    (topic / ".ipynb_checkpoints" / "slides_vars.en-checkpoint.cpp").write_text(
        "x", encoding="utf-8"
    )
    (topic / "helper.cpp").write_text("int helper() { return 1; }\n", encoding="utf-8")
    return tmp_path / "slides"


class TestCli:
    def test_rewrites_every_deck_and_reports(self, course: Path):
        result = CliRunner().invoke(cpp_show_cmd, [str(course)])
        assert result.exit_code == 0, result.output
        assert "2 display(s) in 2 file(s) changed; 0 warning(s)" in result.output
        for lang in ("en", "de"):
            text = (
                course / "module_100_basics" / "topic_110_vars" / f"slides_vars.{lang}.cpp"
            ).read_text(encoding="utf-8")
            assert "SHOW(x);" in text
            assert INCLUDE_LINE in text
        checkpoint = course / "module_100_basics" / "topic_110_vars" / ".ipynb_checkpoints"
        assert (checkpoint / "slides_vars.en-checkpoint.cpp").read_text(encoding="utf-8") == "x"

    def test_dry_run_and_json(self, course: Path):
        result = CliRunner().invoke(cpp_show_cmd, ["--dry-run", "--json", str(course)])
        assert result.exit_code == 0, result.output
        report = json.loads(result.output[result.output.index("{") :])
        assert report["dry_run"] is True
        assert len(report["files"]) == 2
        assert report["files"][0]["rewrites"][0]["after"] == "SHOW(x);"
        text = (course / "module_100_basics" / "topic_110_vars" / "slides_vars.en.cpp").read_text(
            encoding="utf-8"
        )
        assert "SHOW" not in text

    def test_global_display_is_a_warning_and_exit_1(self, course: Path):
        path = course / "module_100_basics" / "topic_110_vars" / "slides_vars.en.cpp"
        path.write_text(
            deck(code("#include <iostream>"), code("int g{1};\ng", '// %% tags=["global"]')),
            encoding="utf-8",
        )
        result = CliRunner().invoke(cpp_show_cmd, [str(path)])
        assert result.exit_code == 1
        assert "WARNING" in result.output and "global" in result.output
