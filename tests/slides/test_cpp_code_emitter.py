"""Tests for the C++ translation-unit emitter (#333 phase 1).

Covers the span-aware classification layer in ``cpp_code_analysis``
(length-preserving masking, original-text recovery) and the per-item
dispatch of :func:`emit_cpp_translation_unit`.
"""

import shutil
import subprocess
import textwrap

import pytest

from clm.slides.cpp_code_analysis import (
    classify_source,
    classify_source_spans,
    mask_comments_and_strings,
    split_top_level,
    split_top_level_spans,
)
from clm.slides.cpp_code_emitter import emit_cpp_translation_unit


def _dedent(text: str) -> str:
    return textwrap.dedent(text).strip("\n")


# ---------------------------------------------------------------------------
# mask_comments_and_strings
# ---------------------------------------------------------------------------


class TestMaskCommentsAndStrings:
    def test_is_length_preserving(self):
        src = 'int x = 1; // note\n/* block\ncomment */ std::string s{"a\\"b"};\n'
        assert len(mask_comments_and_strings(src)) == len(src)

    def test_preserves_newlines_in_block_comments(self):
        src = "/* one\ntwo */\nint x;"
        masked = mask_comments_and_strings(src)
        assert masked.count("\n") == src.count("\n")
        assert "int x;" in masked

    def test_blanks_string_contents_but_keeps_quotes(self):
        masked = mask_comments_and_strings('f("hi{};")')
        assert masked == 'f("     ")'

    def test_blanks_char_literal_contents(self):
        masked = mask_comments_and_strings("char c = '{';")
        assert "{" not in masked
        assert masked == "char c = ' ';"

    def test_keeps_raw_string_delimiters_balanced(self):
        src = 'auto s = R"(a)b})";'
        masked = mask_comments_and_strings(src)
        assert len(masked) == len(src)
        assert masked.count("(") == masked.count(")")
        assert "}" not in masked

    def test_blanks_line_comments(self):
        masked = mask_comments_and_strings("int x; // {;}\nint y;")
        assert masked == "int x; " + " " * len("// {;}") + "\nint y;"


# ---------------------------------------------------------------------------
# split_top_level_spans
# ---------------------------------------------------------------------------


class TestSplitTopLevelSpans:
    def test_spans_reconstruct_split_top_level(self):
        src = _dedent(
            """
            int x = 1;
            void f() { if (x) { g(); } }
            do { h(); } while (x);
            struct S { int a; };
            """
        )
        items = [src[a:b].strip() for a, b in split_top_level_spans(src)]
        assert items == split_top_level(src)
        assert len(items) == 4


# ---------------------------------------------------------------------------
# classify_source_spans
# ---------------------------------------------------------------------------


class TestClassifySourceSpans:
    def test_categories_match_classify_source(self):
        src = _dedent(
            """
            #include <iostream>
            #define ANSWER 42

            // helper for later slides
            int add(int x, int y) { return x + y; }

            template <typename T>
            struct Box { T value; };

            std::string greeting{"Hello, world!"};
            std::cout << greeting << "\\n";
            add(1, 2)
            """
        )
        spans = classify_source_spans(src)
        plain = classify_source(src)
        assert [item.category for item in spans] == [item.category for item in plain]
        assert [item.name for item in spans] == [item.name for item in plain]

    def test_original_preserves_strings_and_comments(self):
        src = '// say hello\nstd::cout << "Hello, world!" << "\\n";'
        (item,) = classify_source_spans(src)
        assert item.category == "output_stmt"
        assert '"Hello, world!"' in item.original
        assert "// say hello" in item.original

    def test_include_original_is_verbatim(self):
        src = '#include "lifetime_observer.hpp"  // local header'
        (item,) = classify_source_spans(src)
        assert item.category == "include"
        assert item.original == src

    def test_bare_string_literal_is_still_expr_display(self):
        (item,) = classify_source_spans('"hello"')
        assert item.category == "expr_display"
        assert item.original == '"hello"'

    def test_preprocessor_lines_removed_from_item_originals(self):
        src = "#include <vector>\nstd::vector<int> v{1, 2, 3};"
        items = classify_source_spans(src)
        assert [i.category for i in items] == ["include", "var_decl"]
        assert "#include" not in items[1].original

    def test_comment_between_items_attaches_to_following_item(self):
        src = "int x = 1;\n// about y\nint y = 2;"
        items = classify_source_spans(src)
        assert "// about y" in items[1].original
        assert "// about y" not in items[0].original

    def test_brace_in_string_does_not_break_spans(self):
        src = 'std::string s{"}"};\nint x = 1;'
        items = classify_source_spans(src)
        assert [i.category for i in items] == ["var_decl", "var_decl"]
        assert items[0].original == 'std::string s{"}"};'


# ---------------------------------------------------------------------------
# emit_cpp_translation_unit
# ---------------------------------------------------------------------------


class TestEmitBasicStructure:
    def test_basic_deck(self):
        cells = [
            "#include <iostream>",
            "int x = 42;",
            "int add(int a, int b) { return a + b; }",
            'std::cout << add(x, 1) << "\\n";',
        ]
        tu = emit_cpp_translation_unit(cells)
        assert tu.startswith("#include <iostream>")
        assert "int x = 42;" in tu
        assert "int add(int a, int b) { return a + b; }" in tu
        assert "void slide_01() {" in tu
        assert '    std::cout << add(x, 1) << "\\n";' in tu
        assert "int main() {\n    slide_01();\n}" in tu
        assert tu.endswith("\n")
        # Statements come after the definitions they use.
        assert tu.index("int add") < tu.index("void slide_01")

    def test_includes_hoisted_and_deduped(self):
        cells = [
            "#include <vector>\nstd::vector<int> v{1};",
            "#include <vector>\n#include <string>\nv.push_back(2);",
        ]
        tu = emit_cpp_translation_unit(cells)
        assert tu.count("#include <vector>") == 1
        assert tu.count("#include <string>") == 1
        # Hoisted above all code.
        assert tu.index("#include <string>") < tu.index("std::vector<int> v{1};")

    def test_statement_cells_become_numbered_slides_called_in_order(self):
        cells = ["f();", "g();", "h();"]
        tu = emit_cpp_translation_unit(cells)
        assert "void slide_01() {" in tu
        assert "void slide_02() {" in tu
        assert "void slide_03() {" in tu
        body = tu[tu.index("int main()") :]
        assert body.index("slide_01();") < body.index("slide_02();") < body.index("slide_03();")

    def test_definition_only_cells_get_no_slide_function(self):
        cells = ["int x = 1;", "struct S { int a; };"]
        tu = emit_cpp_translation_unit(cells)
        assert "slide_" not in tu
        assert "int main() {}" in tu

    def test_empty_cells_are_skipped(self):
        tu = emit_cpp_translation_unit(["", "   \n  ", "int x = 1;"])
        assert "int x = 1;" in tu
        assert "slide_" not in tu

    def test_empty_deck_still_has_main(self):
        assert "int main() {}" in emit_cpp_translation_unit([])

    def test_string_contents_survive(self):
        tu = emit_cpp_translation_unit(['std::cout << "Hello, world!\\n";'])
        assert '"Hello, world!\\n"' in tu

    def test_comments_survive(self):
        tu = emit_cpp_translation_unit(["// the answer\nint answer = 42;"])
        assert "// the answer" in tu

    def test_mixed_cell_routes_defs_before_statements(self):
        cells = ["int x = next_id();\nregister_id(x);"]
        tu = emit_cpp_translation_unit(cells)
        assert tu.index("int x = next_id();") < tu.index("void slide_01")
        assert "    register_id(x);" in tu

    def test_control_statement_wrapped_in_slide(self):
        cells = ["for (int i = 0; i < 3; ++i) { std::cout << i; }"]
        tu = emit_cpp_translation_unit(cells)
        assert "void slide_01() {" in tu
        assert "    for (int i = 0; i < 3; ++i)" in tu

    def test_using_directive_at_namespace_scope(self):
        tu = emit_cpp_translation_unit(["using namespace std::literals;"])
        assert "using namespace std::literals;" in tu
        assert "slide_" not in tu

    def test_define_emitted_in_place(self):
        tu = emit_cpp_translation_unit(["#define ANSWER 42", "int x = ANSWER;"])
        assert "#define ANSWER 42" in tu

    def test_missing_semicolon_terminated(self):
        tu = emit_cpp_translation_unit(["int x = 1"])
        assert "int x = 1;" in tu


class TestEmitMain:
    def test_deck_defined_main_suppresses_generated_main(self):
        cells = ["#include <iostream>", 'int main() {\n    std::cout << "hi";\n    return 0;\n}']
        tu = emit_cpp_translation_unit(cells)
        assert tu.count("int main()") == 1
        assert "return 0;" in tu


class TestEmitDisplayExpressions:
    def test_expr_display_wrapped_with_helper(self):
        tu = emit_cpp_translation_unit(["int x = 2;", "x + 40"])
        assert "CLM_DISPLAY(x + 40);" in tu
        assert "namespace clm {" in tu
        assert "#include <iostream>" in tu
        assert "#include <type_traits>" in tu
        # Helper precedes its first use.
        assert tu.index("#define CLM_DISPLAY") < tu.index("CLM_DISPLAY(x + 40);")

    def test_no_helper_without_display_expressions(self):
        tu = emit_cpp_translation_unit(["int x = 1;", "f(x);"])
        assert "CLM_DISPLAY" not in tu
        assert "namespace clm" not in tu

    def test_display_with_line_comment_closes_on_own_line(self):
        tu = emit_cpp_translation_unit(["x + 1 // off by one"])
        assert "CLM_DISPLAY(\n" in tu
        # The closing paren must sit on a line of its own so the trailing
        # line comment cannot swallow it.
        assert "\n    );" in tu

    def test_display_helper_includes_not_duplicated(self):
        tu = emit_cpp_translation_unit(["#include <iostream>", "1 + 1"])
        assert tu.count("#include <iostream>") == 1

    def test_bare_call_without_semicolon_is_displayed(self):
        # `sqrt(2.0)` without `;` relied on the kernel's auto-display even
        # though it classifies as call_stmt.
        tu = emit_cpp_translation_unit(["#include <cmath>", "sqrt(2.0)"])
        assert "CLM_DISPLAY(sqrt(2.0));" in tu

    def test_terminated_call_stays_plain_statement(self):
        tu = emit_cpp_translation_unit(["setup();"])
        assert "CLM_DISPLAY" not in tu
        assert "    setup();" in tu

    def test_qualified_call_is_a_statement_not_a_declaration(self):
        # std::sort(...) matches the out-of-class-ctor pattern; it must end
        # up inside a slide function, not at namespace scope.
        cells = [
            "#include <algorithm>\n#include <vector>",
            "std::vector<int> xs{3, 1, 2};",
            "std::sort(xs.begin(), xs.end());",
        ]
        tu = emit_cpp_translation_unit(cells)
        assert "void slide_01() {\n    std::sort(xs.begin(), xs.end());\n}" in tu


# ---------------------------------------------------------------------------
# Compile smoke test (runs only when a C++ compiler is available)
# ---------------------------------------------------------------------------

_CXX = shutil.which("g++") or shutil.which("clang++")


@pytest.mark.skipif(_CXX is None, reason="no C++ compiler on PATH")
class TestEmittedCodeCompiles:
    def _check(self, tu: str, tmp_path):
        path = tmp_path / "deck.cpp"
        path.write_text(tu, encoding="utf-8")
        proc = subprocess.run(
            [_CXX, "-std=c++20", "-fsyntax-only", str(path)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr

    def test_representative_deck_compiles(self, tmp_path):
        cells = [
            "#include <iostream>\n#include <vector>",
            "std::vector<int> numbers{1, 2, 3};",
            "int sum(const std::vector<int>& xs) {\n"
            "    int result{0};\n"
            "    for (int x : xs) { result += x; }\n"
            "    return result;\n"
            "}",
            'std::cout << sum(numbers) << "\\n";',
            "numbers.size()",
            "struct Point { int x; int y; };",
            "Point p{3, 4};",
            "p.x + p.y",
        ]
        self._check(emit_cpp_translation_unit(cells), tmp_path)

    def test_display_fallback_for_unstreamable_type_compiles(self, tmp_path):
        cells = [
            "struct Opaque { int v; };",
            "Opaque o{1};",
            "o",
        ]
        self._check(emit_cpp_translation_unit(cells), tmp_path)

    def test_void_display_expression_compiles(self, tmp_path):
        # A bare member call classifies as expr_display (the identifier-then-
        # paren call_stmt pattern doesn't match through the `.`); push_back
        # returns void, so this exercises the void branch of clm::display.
        cells = [
            "#include <vector>",
            "std::vector<int> numbers{1, 2, 3};",
            "numbers.push_back(4)",
        ]
        self._check(emit_cpp_translation_unit(cells), tmp_path)
