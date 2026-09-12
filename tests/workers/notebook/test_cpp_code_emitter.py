"""Tests for the C++ deck emitter (#333 phase 1, #928 phases 1-2).

Covers the span-aware classification layer in ``cpp_code_analysis``
(length-preserving masking, original-text recovery) and the section-function
export of :func:`emit_cpp_deck`.
"""

import importlib.resources
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from clm.workers.notebook.cpp_code_analysis import (
    classify_source,
    classify_source_spans,
    mask_comments_and_strings,
    split_top_level,
    split_top_level_spans,
    strip_comments_and_strings,
)
from clm.workers.notebook.cpp_code_emitter import (
    DANGLING_NOTE,
    CppCell,
    CppDeckExport,
    emit_cpp_deck,
    identifier_from_slide_id,
    merge_adjacent_workshop_ranges,
)


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

    def test_brace_init_temporary_in_fluent_chain_is_one_item(self):
        src = "RequestBuilder{}.setTimeout(10).send();"
        assert split_top_level(src) == [src]

    def test_brace_init_temporary_with_chain_on_next_line(self):
        src = "RequestBuilder{}\n    .setTimeout(10)\n    .send();"
        assert split_top_level(src) == [src.strip()]

    def test_brace_init_mid_declaration_is_one_item(self):
        src = "auto n = std::vector<int>{1, 2}.size();"
        assert split_top_level(src) == [src]

    def test_brace_init_followed_by_comma_continues_declaration(self):
        src = "int a[] = {1, 2}, b[] = {3};"
        assert split_top_level(src) == [src]

    def test_adjacent_definitions_still_split(self):
        src = "void f() {}\nint g() { return 1; }"
        items = split_top_level(src)
        assert items == ["void f() {}", "int g() { return 1; }"]


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
# emit_cpp_deck
# ---------------------------------------------------------------------------


def code(source: str, **kwargs) -> CppCell:
    return CppCell("code", source, **kwargs)


def md(source: str, **kwargs) -> CppCell:
    return CppCell("markdown", source, **kwargs)


def slide(title: str, slide_id: str | None, tag: str = "slide") -> CppCell:
    return md(f"## {title}", tags=(tag,), slide_id=slide_id)


def emit(*cells: CppCell, **kwargs) -> str:
    """The lecture file (``<stem>.cpp``) of the emitted file set."""
    return emit_cpp_deck(list(cells), **kwargs).main


def emit_files(*cells: CppCell, **kwargs) -> CppDeckExport:
    return emit_cpp_deck(list(cells), **kwargs)


def _function_body(tu: str, name: str) -> str:
    start = tu.index(f"void {name}() {{")
    return tu[start : tu.index("\n}\n", start)]


class TestEmitBasicStructure:
    def test_basic_deck(self):
        tu = emit(
            code("#include <iostream>"),
            code("int x = 42;"),
            code("int add(int a, int b) { return a + b; }"),
            code('std::cout << add(x, 1) << "\\n";'),
        )
        assert tu.startswith("#include <iostream>")
        # No slide tags: everything is one leading section.
        assert "void section_01() {" in tu
        assert '    std::cout << "== section_01 ==\\n";' in tu
        # x is only used inside the section, so it stays a local.
        assert "    int x = 42;" in tu
        assert '    std::cout << add(x, 1) << "\\n";' in tu
        assert "int main() {\n    section_01();\n}" in tu
        assert tu.endswith("\n")
        # The definition precedes the function that uses it.
        assert tu.index("int add") < tu.index("void section_01")

    def test_includes_hoisted_and_deduped(self):
        tu = emit(
            code("#include <vector>\nstd::vector<int> v{1};"),
            code("#include <vector>\n#include <string>\nv.push_back(2);"),
        )
        assert tu.count("#include <vector>") == 1
        assert tu.count("#include <string>") == 1
        assert tu.index("#include <string>") < tu.index("std::vector<int> v{1};")

    def test_brace_init_fluent_chain_stays_one_statement(self):
        # Regression: the builder deck's `RequestBuilder{}.setTimeout(10)...`
        # was split after the brace-init `}`, leaving a stray `.setTimeout`
        # item that cannot compile.
        tu = emit(code("// Change only the timeout....\nRequestBuilder{}.setTimeout(10).send();"))
        assert "    RequestBuilder{}.setTimeout(10).send();" in tu
        assert "\n    .setTimeout" not in tu

    def test_statement_cells_stay_in_order_within_a_section(self):
        tu = emit(code("f();"), code("g();"), code("h();"))
        body = _function_body(tu, "section_01")
        assert body.index("f();") < body.index("g();") < body.index("h();")
        # One cell per paragraph.
        assert "    f();\n\n    g();\n\n    h();" in body

    def test_items_of_one_cell_stay_together(self):
        tu = emit(code("f();\ng();"), code("h();"))
        assert "    f();\n    g();\n\n    h();" in tu

    def test_definition_only_deck_gets_no_function(self):
        tu = emit(code("struct S { int a; };"), code("void f() {}"))
        assert "void section_01" not in tu
        assert "int main() {}" in tu

    def test_empty_cells_are_skipped(self):
        tu = emit(code(""), code("   \n  "), code("struct S {};"))
        assert "struct S {};" in tu
        assert "TODO" not in tu
        assert "void section_01" not in tu

    def test_empty_deck_still_has_main(self):
        assert "int main() {}" in emit()

    def test_string_contents_survive(self):
        tu = emit(code('std::cout << "Hello, world!\\n";'))
        assert '"Hello, world!\\n"' in tu

    def test_comments_survive(self):
        tu = emit(code("// the answer\nint answer = 42;"))
        assert "// the answer" in tu

    def test_mixed_cell_keeps_declaration_and_statement_together(self):
        tu = emit(code("int x = next_id();\nregister_id(x);"))
        assert "    int x = next_id();\n    register_id(x);" in tu

    def test_control_statement_in_body(self):
        tu = emit(code("for (int i = 0; i < 3; ++i) { std::cout << i; }"))
        assert "    for (int i = 0; i < 3; ++i)" in tu

    def test_anonymous_enum_at_namespace_scope(self):
        tu = emit(code("enum { RED, GREEN };"), code("int color = RED;"))
        assert not tu.index("enum { RED, GREEN };") > tu.index("int color = RED;")
        assert "\nenum { RED, GREEN };" in tu
        assert "    int color = RED;" in tu

    def test_using_directive_at_namespace_scope(self):
        tu = emit(code("using namespace std::literals;"))
        assert tu.startswith("using namespace std::literals;\n")
        assert "void section_01" not in tu

    def test_define_emitted_in_place(self):
        tu = emit(code("#define ANSWER 42"), code("int x = ANSWER;"))
        assert "\n#define ANSWER 42" in tu

    def test_missing_semicolon_terminated(self):
        tu = emit(code("int x = 1"))
        assert "int x = 1;" in tu


class TestSections:
    def test_slide_and_subslide_open_functions_named_by_slide_id(self):
        tu = emit(
            slide("Intro", "intro"),
            code("f();"),
            slide("Brace initialization", "brace-initialization", tag="subslide"),
            code("g();"),
        )
        assert "void intro() {" in tu
        assert "void brace_initialization() {" in tu
        assert "    f();" in _function_body(tu, "intro")
        assert "    g();" in _function_body(tu, "brace_initialization")
        assert "int main() {\n    intro();\n    brace_initialization();\n}" in tu

    def test_cells_before_the_first_opener_form_a_leading_section(self):
        tu = emit(code("setup();"), slide("Intro", "intro"), code("f();"))
        assert "void section_01() {" in tu
        assert "int main() {\n    section_01();\n    intro();\n}" in tu

    def test_code_cell_opener_belongs_to_the_section_it_opens(self):
        tu = emit(
            slide("Intro", "intro"),
            code("f();"),
            code("g();", tags=("subslide",), slide_id="demo"),
        )
        assert "    g();" in _function_body(tu, "demo")
        assert "g();" not in _function_body(tu, "intro")

    @pytest.mark.parametrize(
        "slide_id, expected",
        [
            ("brace-initialization", "brace_initialization"),
            ("42-answer", "s_42_answer"),
            ("Einführung", "Einfuhrung"),
            ("main", "main_section"),
            ("class", "class_section"),
            ("a--b__c", "a_b_c"),
            ("---", None),
            ("", None),
            (None, None),
        ],
    )
    def test_identifier_from_slide_id(self, slide_id, expected):
        assert identifier_from_slide_id(slide_id) == expected

    def test_duplicate_slide_ids_get_numeric_suffix(self):
        tu = emit(slide("A", "intro"), code("f();"), slide("B", "intro"), code("g();"))
        assert "void intro() {" in tu
        assert "void intro_2() {" in tu
        assert "int main() {\n    intro();\n    intro_2();\n}" in tu

    def test_section_name_avoids_names_the_deck_defines(self):
        # Code-derived slide_ids often equal the function the cell defines.
        tu = emit(
            md("## Twice", tags=("slide",), slide_id="twice"),
            code("int twice(int x) { return 2 * x; }"),
            code("twice(1)"),
            md("## Counter", tags=("slide",), slide_id="counter"),
            code("int counter{0};"),
            code("counter++;"),
        )
        assert "void twice_section() {" in tu
        assert "void counter_section() {" in tu
        assert "int main() {\n    twice_section();\n    counter_section();\n}" in tu

    def test_opener_without_slide_id_falls_back_to_section_number(self):
        tu = emit(slide("A", "a"), code("f();"), slide("B", None), code("g();"))
        assert "void section_02() {" in tu

    def test_banner_uses_the_first_heading(self):
        tu = emit(md("# Deck\n\n## Brace `init` 100%", tags=("slide",), slide_id="x"), code("f();"))
        assert '    std::cout << "== Deck ==\\n";' in tu

    def test_banner_escapes_quotes_and_backslashes(self):
        tu = emit(md('## Say "hi" \\ bye', tags=("slide",), slide_id="x"), code("f();"))
        assert '    std::cout << "== Say \\"hi\\" \\\\ bye ==\\n";' in tu

    def test_banner_falls_back_to_humanized_slide_id(self):
        tu = emit(md("Just text.", tags=("slide",), slide_id="brace-init"), code("f();"))
        assert '    std::cout << "== Brace init ==\\n";' in tu

    def test_banner_falls_back_to_function_name(self):
        tu = emit(code("f();"))
        assert '    std::cout << "== section_01 ==\\n";' in tu

    def test_markdown_only_section_emits_comments_but_no_function(self):
        tu = emit(slide("Title", "title"), md("Some prose."), slide("Code", "c"), code("f();"))
        assert "// ## Title\n\n// Some prose." in tu
        assert "void title()" not in tu
        assert "int main() {\n    c();\n}" in tu

    def test_markdown_before_first_statement_goes_above_the_function(self):
        tu = emit(
            slide("Intro", "intro"),
            md("Explains the helper."),
            code("int helper() { return 1; }"),
            md("Now use it."),
            code("helper();"),
            md("And that's it."),
        )
        assert (
            "// ## Intro\n\n// Explains the helper.\nint helper() { return 1; }\n\n"
            "// Now use it.\nvoid intro() {"
        ) in tu
        assert "    helper();\n\n    // And that's it." in _function_body(tu, "intro")

    def test_markdown_after_first_statement_goes_into_the_body(self):
        tu = emit(slide("Intro", "intro"), code("f();"), md("Then g."), code("g();"))
        assert "    f();\n\n    // Then g.\n    g();" in _function_body(tu, "intro")

    def test_multiline_markdown_becomes_a_comment_block(self):
        tu = emit(md("Line one\n\n- bullet"), code("f();"))
        assert "// Line one\n//\n// - bullet" in tu

    def test_iostream_is_forced_for_the_banner(self):
        tu = emit(code("f();"))
        assert tu.startswith("#include <iostream>")

    def test_no_iostream_without_functions(self):
        tu = emit(code("struct S {};"))
        assert "#include <iostream>" not in tu


class TestPromotion:
    def test_variable_used_by_a_later_section_is_promoted(self):
        tu = emit(slide("A", "a"), code("int x{1};"), slide("B", "b"), code("x++;"))
        assert "\nint x{1};\n" in tu
        assert "    int x{1};" not in tu
        assert tu.index("int x{1};") < tu.index("void b()")
        assert "    x++;" in _function_body(tu, "b")

    def test_unreferenced_variable_stays_local(self):
        tu = emit(slide("A", "a"), code("int x{1};"), slide("B", "b"), code("f();"))
        assert "    int x{1};" in _function_body(tu, "a")

    def test_variable_used_by_a_hoisted_definition_is_promoted(self):
        tu = emit(code("int counter{0};\nvoid bump() { ++counter; }"))
        assert tu.startswith("int counter{0};\nvoid bump() { ++counter; }\n")
        assert "void section_01" not in tu

    def test_promotion_is_transitive_within_the_section(self):
        tu = emit(slide("A", "a"), code("int n{3};\nint total{n};"), slide("B", "b"), code("total"))
        assert "\nint n{3};\nint total{n};\n" in tu
        assert tu.index("int total{n};") < tu.index("void b()")
        assert "void a()" not in tu  # nothing was left for a body

    def test_reference_in_a_comment_does_not_promote(self):
        tu = emit(slide("A", "a"), code("int x{1};"), slide("B", "b"), code("f(); // x"))
        assert "    int x{1};" in _function_body(tu, "a")

    def test_reference_in_a_string_does_not_promote(self):
        tu = emit(slide("A", "a"), code("int x{1};"), slide("B", "b"), code('puts("x");'))
        assert "    int x{1};" in _function_body(tu, "a")

    def test_partial_identifier_match_does_not_promote(self):
        tu = emit(slide("A", "a"), code("int x{1};"), slide("B", "b"), code("xs.clear();"))
        assert "    int x{1};" in _function_body(tu, "a")

    def test_reference_in_a_blanked_later_cell_promotes(self):
        # Code-along: the later cell is blank in this view, but its original
        # source still uses x, so a student typing it back needs x visible.
        tu = emit(
            slide("A", "a"),
            code("int x{1};"),
            slide("B", "b"),
            code("", original_source="x++;"),
        )
        assert "    int x{1};" not in tu
        assert tu.index("int x{1};") < tu.index("void b()")
        assert "    // TODO: B" in _function_body(tu, "b")

    def test_member_access_does_not_promote(self):
        tu = emit(slide("A", "a"), code("int x{1};"), slide("B", "b"), code("p.x = 2;\nq->x = 3;"))
        assert "    int x{1};" in _function_body(tu, "a")

    def test_global_tag_moves_the_cell_to_the_header(self):
        files = emit_files(slide("A", "a"), code("int cfg{1};", tags=("global",)), code("f();"))
        assert files.header is not None
        assert "\nint cfg{1};\n" in files.header
        assert "int cfg{1};" not in files.main
        assert files.main.startswith('#include "deck.hpp"\n')


class TestEmitCodeAlongTodos:
    """Phase 2 of #928: TODO markers sit where the blanked code would go."""

    def test_blanked_statement_leaves_a_todo_in_the_body(self):
        tu = emit(
            slide("Calls", "calls"), code("#include <iostream>"), code("", original_source="f();")
        )
        assert "    // TODO: Calls" in _function_body(tu, "calls")
        assert tu.count("TODO") == 1

    def test_todo_names_the_section_heading_not_the_slide_id(self):
        tu = emit(slide("Brace init", "brace-init"), code("", original_source="f();"))
        assert "// TODO: Brace init" in tu

    def test_todo_falls_back_to_humanized_slide_id_without_heading(self):
        tu = emit(code("", original_source="f();", tags=("slide",), slide_id="brace-init"))
        assert "// TODO: Brace init" in _function_body(tu, "brace_init")

    def test_empty_cell_counts_as_blanked_when_the_spec_blanks(self):
        tu = emit(code("#include <iostream>"), code(""), code("  \n "), blanks_code_cells=True)
        assert tu.count("    // TODO: section_01") == 2

    def test_originally_empty_cell_is_not_a_todo_even_when_the_spec_blanks(self):
        # Partial blanks only its workshop range, so an empty pre-workshop
        # cell (snapshot present, original empty) must not become a TODO.
        tu = emit(code("f();"), code("", original_source=""), blanks_code_cells=True)
        assert "TODO" not in tu

    def test_default_still_skips_empty_cells(self):
        tu = emit(code(""), code("struct S {};"))
        assert "TODO" not in tu

    def test_kept_cells_emit_normally_between_todos(self):
        tu = emit(code("int x = 1;"), code("", original_source="g();"), code("f(x);"))
        body = _function_body(tu, "section_01")
        assert "    int x = 1;\n\n    // TODO: section_01\n\n    f(x);" in body

    def test_blanked_definition_leaves_a_todo_at_namespace_scope(self):
        tu = emit(
            slide("Functions", "functions"),
            code("", original_source="int twice(int x) { return 2 * x; }"),
            code('std::cout << "hi";', tags=("keep",)),
        )
        assert "// TODO: define twice" in tu
        assert tu.index("// TODO: define twice") < tu.index("void functions()")
        assert "TODO" not in _function_body(tu, "functions")

    def test_blanked_variable_names_the_variable_in_the_body(self):
        tu = emit(slide("Vars", "vars"), code("", original_source="int i1{10};"))
        assert "    // TODO: define i1" in _function_body(tu, "vars")

    def test_blanked_cell_with_several_definitions_gets_one_todo(self):
        tu = emit(
            slide("Types", "types"),
            code("", original_source="struct Point { int x; };\nusing P = Point;\nint f();"),
        )
        assert tu.count("TODO") == 1
        assert "// TODO: define Point, P, f" in tu

    def test_blanked_variable_used_by_a_later_section_gets_its_todo_at_namespace_scope(self):
        # The student must type ``x`` where the later section can see it.
        tu = emit(
            slide("A", "a"),
            code("", original_source="int x{1};"),
            slide("B", "b"),
            code("", original_source="x++;"),
        )
        assert tu.index("// TODO: define x") < tu.index("void b()")
        assert "void a()" not in tu  # the section body is empty
        assert "    // TODO: B" in _function_body(tu, "b")

    def test_blanked_global_cell_todo_goes_to_the_header(self):
        files = emit_files(
            slide("A", "a"), code("", original_source="f();", tags=("global",)), code("g();")
        )
        assert files.header is not None
        assert "// TODO: A" in files.header
        assert "TODO" not in files.main

    def test_mixed_definition_and_statement_cell_todo_goes_to_namespace_scope(self):
        tu = emit(slide("A", "a"), code("", original_source="int f() { return 1; }\nf();"))
        assert "// TODO: define f" in tu
        assert "void a()" not in tu

    def test_kept_variable_used_by_a_blanked_definition_is_promoted(self):
        tu = emit(
            slide("A", "a"),
            code("int counter{0};", tags=("keep",)),
            code("", original_source="void bump() { ++counter; }"),
        )
        assert tu.index("int counter{0};") < tu.index("// TODO: define bump")
        assert "void a()" not in tu

    def test_section_name_avoids_a_name_a_blanked_cell_defines(self):
        # Completed and code-along must name the sections identically.
        tu = emit(
            md("## Include", tags=("slide",), slide_id="include"),
            code("", original_source="void include() {}"),
            code("include();", tags=("keep",)),
        )
        assert "void include_section()" in tu


class TestDanglingKeepCells:
    """D5: a kept cell that needs blanked code is emitted commented out."""

    def test_keep_cell_referencing_a_blanked_name_is_commented_out(self):
        tu = emit(
            slide("A", "a"),
            code("", original_source="int twice(int x) { return 2 * x; }"),
            code("twice(21)", tags=("keep",)),
        )
        body = _function_body(tu, "a")
        assert f"    {DANGLING_NOTE}\n    // CLM_DISPLAY(twice(21));" in body
        assert "\n    CLM_DISPLAY" not in body

    def test_note_precedes_the_first_commented_item_only_once(self):
        tu = emit(
            code("", original_source="int x{1};"),
            code("f(x);\ng(x);", tags=("keep",)),
        )
        assert tu.count(DANGLING_NOTE) == 1
        assert "    // f(x);\n    // g(x);" in tu

    def test_reference_is_deck_global(self):
        tu = emit(
            slide("A", "a"),
            code("", original_source="int x{1};"),
            slide("B", "b"),
            code("x++;", tags=("keep",)),
        )
        assert "    // x++;" in _function_body(tu, "b")

    def test_dependency_is_transitive(self):
        tu = emit(
            code("", original_source="int x{1};"),
            code("int y{x + 1};", tags=("keep",)),
            code("std::cout << y;", tags=("keep",)),
        )
        assert "    // int y{x + 1};" in tu
        assert "    // std::cout << y;" in tu
        assert tu.count(DANGLING_NOTE) == 2

    def test_keep_cell_before_the_blank_is_not_dangling(self):
        # A kept section may reuse a local name a later workshop cell blanks.
        tu = emit(
            slide("Demo", "demo"),
            code("int result{1};\nresult"),
            slide("Workshop", "workshop"),
            code("", original_source="int result{2};"),
        )
        assert DANGLING_NOTE not in tu
        assert "\nint result{1};\n" in tu
        assert "    CLM_DISPLAY(result);" in _function_body(tu, "demo")

    def test_keep_cell_defining_the_name_itself_is_not_dangling(self):
        tu = emit(
            code("", original_source="int x{1};"),
            code("int x{2};\nx", tags=("keep",)),
        )
        assert DANGLING_NOTE not in tu

    def test_member_access_is_not_a_reference(self):
        tu = emit(
            code("struct P { int x; };\nP p{1};", tags=("keep",)),
            code("", original_source="int x{1};"),
            code("p.x", tags=("keep",)),
        )
        assert DANGLING_NOTE not in tu

    def test_reference_in_a_string_or_comment_is_not_a_reference(self):
        tu = emit(
            code("", original_source="int x{1};"),
            code('std::cout << "x"; // x', tags=("keep",)),
        )
        assert DANGLING_NOTE not in tu

    def test_dangling_definition_is_commented_out_at_namespace_scope(self):
        tu = emit(
            slide("A", "a"),
            code("", original_source="struct Point { int x; };"),
            code("Point origin() { return {}; }", tags=("keep",)),
            code("f();", tags=("keep",)),
        )
        assert f"{DANGLING_NOTE}\n// Point origin() {{ return {{}}; }}" in tu
        assert tu.index("// Point origin()") < tu.index("void a()")
        assert "    f();" in _function_body(tu, "a")

    def test_dangling_cell_with_both_scopes_gets_the_note_in_each(self):
        tu = emit(
            code("", original_source="int x{1};"),
            code("int f() { return x; }\nf();", tags=("keep",)),
        )
        assert tu.count(DANGLING_NOTE) == 2
        assert "// int f() { return x; }" in tu
        assert "    // f();" in tu

    def test_dangling_display_still_pulls_in_the_helper_include(self):
        tu = emit(code("", original_source="int x{1};"), code("x", tags=("keep",)))
        assert "#include <clm/display.hpp>" in tu

    def test_dangling_main_is_commented_out_and_main_generated(self):
        tu = emit(
            code("", original_source="int f() { return 1; }"),
            code("int main() { return f(); }", tags=("keep",)),
        )
        assert "// int main() { return f(); }" in tu
        assert "\nint main() {}" in tu

    def test_completed_view_has_no_dangling_cells(self):
        tu = emit(code("int x{1};"), code("x++;"))
        assert DANGLING_NOTE not in tu

    def test_qualified_reference_counts(self):
        tu = emit(
            code("", original_source="namespace frac { struct Fraction { int n; }; }"),
            code("frac::Fraction half{1};", tags=("keep",)),
        )
        assert "    // frac::Fraction half{1};" in tu

    def test_class_inside_a_missing_namespace_block_is_missing(self):
        # The classifier sees only the namespace block; the scan looks inside.
        tu = emit(
            code("namespace poly { class Polynomial; }", tags=("keep", "start")),
            code("", original_source="namespace poly { class Polynomial { int d; }; }"),
            code("namespace poly { Polynomial::Polynomial() {} }", tags=("keep",)),
        )
        assert "// namespace poly { Polynomial::Polynomial() {} }" in tu

    def test_member_definition_does_not_provide_the_class(self):
        tu = emit(
            code("", original_source="struct Point { double x; double distance(); };"),
            code("double Point::distance() { return x; }", tags=("keep",)),
        )
        assert "// double Point::distance() { return x; }" in tu

    def test_blanked_member_definition_comments_out_its_callers(self):
        tu = emit(
            code("struct Point { double x; double distance(); };", tags=("keep",)),
            code("", original_source="double Point::distance() { return x; }"),
            code("Point p{1.0};", tags=("keep",)),
            code("p.distance()", tags=("keep",)),
        )
        assert "    Point p{1.0};" in tu
        assert "    // CLM_DISPLAY(p.distance());" in tu

    def test_missing_operator_makes_its_operand_type_missing(self):
        # ``a * b`` never spells ``operator*``: every later cell touching
        # the operand type is commented out instead.
        tu = emit(
            code("struct Fraction { int n; };", tags=("keep",)),
            code("Fraction one{1};", tags=("keep",)),
            code("", original_source="Fraction operator*(Fraction a, Fraction b) { return a; }"),
            code("Fraction two{2};", tags=("keep",)),
            code("two * one", tags=("keep",)),
            code("one.n", tags=("keep",)),
        )
        assert "    Fraction one{1};" in tu
        assert "    // Fraction two{2};" in tu
        assert "    // CLM_DISPLAY(two * one);" in tu
        assert "    CLM_DISPLAY(one.n);" in tu

    def test_later_kept_definition_makes_the_name_available_again(self):
        tu = emit(
            code("", original_source="int f() { return 1; }"),
            code("int f() { return 2; }", tags=("keep",)),
            code("f()", tags=("keep",)),
        )
        assert DANGLING_NOTE not in tu


class TestExcludedCells:
    """Solution cells the view drops (``completed``/``alt``) still count."""

    def test_excluded_cell_is_never_emitted_and_leaves_no_todo(self):
        tu = emit(code("f();"), code("", original_source="int secret{42};", excluded=True))
        assert "secret" not in tu
        assert "TODO" not in tu

    def test_excluded_cell_does_not_open_a_section(self):
        tu = emit(
            slide("A", "a"),
            code("f();"),
            code("", original_source="g();", excluded=True, tags=("subslide",), slide_id="b"),
            code("h();"),
        )
        assert "void b()" not in tu
        assert "    f();\n\n    h();" in _function_body(tu, "a")

    def test_start_completed_pair_stub_does_not_satisfy_dependents(self):
        # The kept ``start`` stub defines Point2 too, but the solution twin
        # comes later and is the version the kept cells need.
        tu = emit(
            code("struct Point2 { double x; };", tags=("start",)),
            code(
                "",
                original_source="struct Point2 { double x; double len(); };",
                excluded=True,
                tags=("completed",),
            ),
            code("double Point2::len() { return x; }", tags=("keep",)),
            code("Point2 p{1.0};", tags=("keep",)),
        )
        assert "\nstruct Point2 { double x; };\n" in tu
        assert "// double Point2::len() { return x; }" in tu
        assert "    // Point2 p{1.0};" in tu

    def test_excluded_specialization_shares_the_primary_name(self):
        tu = emit(
            code("template <typename T> struct Buf { T d; };", tags=("keep",)),
            code(
                "",
                original_source="template <typename T> struct Buf<std::vector<T>> { T d; };",
                excluded=True,
            ),
            code("Buf<int> b;", tags=("keep",)),
        )
        assert "    // Buf<int> b;" in tu

    def test_excluded_cell_still_drives_promotion(self):
        # Parity with the Completed view, which contains the cell.
        tu = emit(
            slide("A", "a"),
            code("int x{1};", tags=("keep",)),
            slide("B", "b"),
            code("", original_source="x++;", excluded=True),
        )
        assert "\nint x{1};\n" in tu
        assert "    int x{1};" not in tu

    def test_excluded_cell_reserves_its_name_for_section_naming(self):
        tu = emit(
            md("## Include", tags=("slide",), slide_id="include"),
            code("", original_source="void include() {}", excluded=True),
            code("f();", tags=("keep",)),
        )
        assert "void include_section()" in tu


class TestEmitMain:
    def test_deck_defined_main_suppresses_generated_main(self):
        tu = emit(
            code("#include <iostream>"),
            code('int main() {\n    std::cout << "hi";\n    return 0;\n}'),
        )
        assert tu.count("int main()") == 1
        assert "return 0;" in tu
        assert "section_01" not in tu

    def test_deck_defined_main_still_gets_section_functions(self):
        tu = emit(slide("A", "a"), code("f();"), code("int main() { a(); }"))
        assert "void a() {" in tu
        assert tu.count("int main()") == 1


class TestEmitDisplayExpressions:
    def test_expr_display_wrapped_with_labeled_helper(self):
        tu = emit(code("int x = 2;"), code("x + 40"))
        assert "    CLM_DISPLAY(x + 40);" in tu
        # The helper is a vendored support header, not an inline block.
        assert "#include <clm/display.hpp>" in tu
        assert "namespace clm" not in tu
        assert "#define CLM_DISPLAY" not in tu
        assert tu.index("#include <clm/display.hpp>") < tu.index("CLM_DISPLAY(x + 40);")

    def test_no_helper_without_display_expressions(self):
        tu = emit(code("int x = 1;"), code("f(x);"))
        assert "CLM_DISPLAY" not in tu
        assert "clm/display.hpp" not in tu

    def test_display_with_line_comment_closes_on_own_line(self):
        tu = emit(code("x + 1 // off by one"))
        assert "CLM_DISPLAY(\n" in tu
        # The closing paren must sit on a line of its own so the trailing
        # line comment cannot swallow it.
        assert "\n    );" in tu

    def test_display_helper_includes_not_duplicated(self):
        tu = emit(code("#include <iostream>"), code("1 + 1"))
        assert tu.count("#include <iostream>") == 1

    def test_bare_call_without_semicolon_is_displayed(self):
        # `sqrt(2.0)` without `;` relied on the kernel's auto-display even
        # though it classifies as call_stmt.
        tu = emit(code("#include <cmath>"), code("sqrt(2.0)"))
        assert "CLM_DISPLAY(sqrt(2.0));" in tu

    def test_terminated_call_stays_plain_statement(self):
        tu = emit(code("setup();"))
        assert "CLM_DISPLAY" not in tu
        assert "    setup();" in tu

    def test_qualified_call_is_a_statement_not_a_declaration(self):
        # std::sort(...) matches the out-of-class-ctor pattern; it must end
        # up inside the section function, not at namespace scope.
        tu = emit(
            code("#include <algorithm>\n#include <vector>"),
            code("std::vector<int> xs{3, 1, 2};"),
            code("std::sort(xs.begin(), xs.end());"),
        )
        assert "    std::sort(xs.begin(), xs.end());" in _function_body(tu, "section_01")


# ---------------------------------------------------------------------------
# Classifier regressions: #922 digit separators, #921 requires clauses
# ---------------------------------------------------------------------------

_MIXED_DECL_STMT_CELL = "long arg2{2'000'000'000};\nh(1, arg2);"

_REQUIRES_CLAUSE_TEMPLATE = _dedent(
    """
    template <typename T>
        requires std::totally_ordered<T>
    T ordered_min(T a, T b)
    {
        return a < b ? a : b;
    }
    """
)


class TestDigitSeparators:
    """Issue #922: a ``'`` inside a numeric literal is not a char-literal quote.

    The stripper used to open a char literal at ``2'000`` and swallow the
    rest of the cell, so the declaration and the statement after it became
    one item that the emitter placed at namespace scope.
    """

    def test_strip_keeps_digit_separators(self):
        assert strip_comments_and_strings(_MIXED_DECL_STMT_CELL) == _MIXED_DECL_STMT_CELL

    def test_mask_keeps_digit_separators(self):
        assert mask_comments_and_strings(_MIXED_DECL_STMT_CELL) == _MIXED_DECL_STMT_CELL

    @pytest.mark.parametrize(
        "src", ["auto x = 0x1'F'FF;", "double d = 1'000.5;", "int w = 1'0;", "auto b = 0b1'01;"]
    )
    def test_hex_binary_and_fractional_separators_survive(self, src):
        assert strip_comments_and_strings(src) == src
        assert mask_comments_and_strings(src) == src

    @pytest.mark.parametrize(
        "src, expected",
        [
            ("char c = 'a';", "char c = ' ';"),
            ("char c = u8'a';", "char c = u8' ';"),
            ("char c = L'x';", "char c = L' ';"),
            ("auto s = 1 + 'a';", "auto s = 1 + ' ';"),
        ],
    )
    def test_char_literals_are_still_literals(self, src, expected):
        assert strip_comments_and_strings(src) == expected

    def test_declaration_and_statement_split_into_two_items(self):
        items = classify_source(_MIXED_DECL_STMT_CELL)
        assert [(i.category, i.name) for i in items] == [
            ("var_decl", "arg2"),
            ("call_stmt", None),
        ]

    def test_spans_split_the_same_way(self):
        items = classify_source_spans(_MIXED_DECL_STMT_CELL)
        assert [i.original for i in items] == ["long arg2{2'000'000'000};", "h(1, arg2);"]


class TestRequiresClause:
    """Issue #921: a requires-clause between the template head and declarator.

    The declaration regexes are anchored at the item start, so the clause
    hid the declarator and the expression fallback took the whole function
    template for a display expression.
    """

    def test_constrained_function_template_is_a_definition(self):
        (item,) = classify_source(_REQUIRES_CLAUSE_TEMPLATE)
        assert (item.category, item.name, item.signature) == (
            "fn_def",
            "ordered_min",
            "ordered_min(T,T)",
        )

    @pytest.mark.parametrize(
        "src, category, name",
        [
            (
                "template <typename T> requires (sizeof(T) > 4) && !std::is_void_v<T> void f(T) {}",
                "fn_def",
                "f",
            ),
            (
                "template <typename T> requires std::integral<T> || std::floating_point<T> "
                "struct Num { T v; };",
                "type_def",
                "Num",
            ),
            ("template <typename T> requires C<T> T g(T a);", "fn_decl", "g"),
            ("template <typename T> requires C<T> using Ref = T&;", "alias_def", "Ref"),
        ],
    )
    def test_compound_and_parenthesized_constraints(self, src, category, name):
        (item,) = classify_source(src)
        assert (item.category, item.name) == (category, name)

    def test_trailing_requires_clause_unchanged(self):
        (item,) = classify_source(
            "template <typename T> T h(T a) requires std::totally_ordered<T> { return a; }"
        )
        assert (item.category, item.name) == ("fn_def", "h")

    def test_constrained_parameter_form_unchanged(self):
        (item,) = classify_source("template <std::totally_ordered T> T k(T a) { return a; }")
        assert (item.category, item.name) == ("fn_def", "k")

    def test_spans_keep_the_original_text(self):
        (item,) = classify_source_spans(_REQUIRES_CLAUSE_TEMPLATE)
        assert item.category == "fn_def"
        assert item.original == _REQUIRES_CLAUSE_TEMPLATE


class TestParenInitialization:
    """``int i2(20);`` is a variable, not a function declaration (#928)."""

    @pytest.mark.parametrize(
        "src, name",
        [
            ("int i2(20);", "i2"),
            ("double d(-1.5);", "d"),
            ('std::string s("hi");', "s"),
            ("char c('x');", "c"),
            ("bool b(true);", "b"),
            ("int* p(nullptr);", "p"),
        ],
    )
    def test_literal_paren_initializer_is_a_variable(self, src, name):
        (item,) = classify_source(src)
        assert (item.category, item.name) == ("var_decl", name)

    def test_identifier_argument_stays_a_declaration(self):
        # The most vexing parse: without knowing whether `value` names a type
        # or a variable, this reads as a function declaration.
        (item,) = classify_source("int i2b(value);")
        assert item.category == "fn_decl"

    def test_type_argument_stays_a_declaration(self):
        (item,) = classify_source("int f(int);")
        assert (item.category, item.name) == ("fn_decl", "f")

    def test_paren_initialized_variable_stays_local(self):
        tu = emit(code("int i2(20);"), code("i2"))
        assert "    int i2(20);" in _function_body(tu, "section_01")


class TestEmitClassifierRegressions:
    def test_mixed_declaration_and_statement_cell_stays_in_the_body(self):
        tu = emit(code("void h(int, long) {}"), code(_MIXED_DECL_STMT_CELL))
        assert "    long arg2{2'000'000'000};\n    h(1, arg2);" in _function_body(tu, "section_01")

    def test_requires_clause_template_stays_at_namespace_scope(self):
        tu = emit(code(_REQUIRES_CLAUSE_TEMPLATE), code("ordered_min(1, 2)"))
        assert _REQUIRES_CLAUSE_TEMPLATE in tu
        assert "    CLM_DISPLAY(ordered_min(1, 2));" in tu
        assert tu.count("CLM_DISPLAY(") == 1  # the one call; the macro lives in the header


def workshop(title: str, slide_id: str | None = None, *tags: str) -> CppCell:
    return md(f"## {title}", tags=("slide", "workshop", *tags), slide_id=slide_id)


class TestMultiFileExport:
    """Phase 3 of #928: header and one file per workshop range."""

    def test_plain_deck_is_a_single_file(self):
        files = emit_files(slide("A", "a"), code("#include <vector>"), code("f();"))
        assert files.header is None
        assert files.workshops == ()
        assert files.main.startswith("#include <vector>\n")
        assert files.companion_files("deck") == {}

    def test_header_carries_pragma_includes_and_global_cells(self):
        files = emit_files(
            slide("A", "a"),
            code("#include <vector>"),
            code("int cfg{1};", tags=("global",)),
            code("std::vector<int> v{cfg};\nv"),
            stem="03 Deck",
        )
        assert files.header is not None
        assert files.header.startswith("#pragma once\n\n#include <vector>\n")
        # Forced includes (banner, display helper) move to the header too.
        assert "#include <iostream>" in files.header
        assert "#include <clm/display.hpp>" in files.header
        assert files.header.endswith("\n\nint cfg{1};\n")
        assert files.main.startswith('#include "03 Deck.hpp"\n\n')
        assert "#include <vector>" not in files.main
        assert files.companion_files("03 Deck") == {"03 Deck.hpp": files.header}

    def test_dangling_global_cell_is_commented_out_in_the_header(self):
        files = emit_files(
            slide("A", "a"),
            code("", original_source="int base{1};"),
            code("int twice{2 * base};", tags=("global", "keep")),
        )
        assert files.header is not None
        assert f"{DANGLING_NOTE}\n// int twice{{2 * base}};" in files.header
        assert "// TODO: define base" in files.main

    def test_workshop_range_becomes_its_own_file(self):
        files = emit_files(
            slide("Lecture", "lecture"),
            code("int lecture_value{1};\nlecture_value"),
            workshop("Workshop", "workshop-sum"),
            md("Write `sum`."),
            code("int sum(int a, int b) { return a + b; }"),
            code("sum(1, 2)"),
            stem="deck",
        )
        assert files.header is not None
        assert len(files.workshops) == 1
        ordinal, ws = files.workshops[0]
        assert ordinal == 1
        assert ws.startswith('#include "deck.hpp"\n\n')
        assert "// ## Workshop\n\n// Write `sum`.\nint sum(int a, int b) { return a + b; }" in ws
        assert "    CLM_DISPLAY(sum(1, 2));" in _function_body(ws, "workshop_sum")
        assert ws.endswith("int main() {\n    workshop_sum();\n}\n")
        # Nothing of the workshop leaks into the lecture file, and vice versa.
        assert "sum" not in files.main
        assert "lecture_value" not in ws
        assert files.main.endswith("int main() {\n    lecture();\n}\n")
        assert set(files.companion_files("deck")) == {"deck.hpp", "deck_workshop_1.cpp"}

    def test_workshop_opener_without_slide_tag_still_opens_a_section(self):
        files = emit_files(
            slide("A", "a"),
            code("f();"),
            md("## Workshop", tags=("workshop",), slide_id="ws"),
            code("g();"),
        )
        assert "    f();" in _function_body(files.main, "a")
        assert "g();" not in files.main
        assert "    g();" in _function_body(files.workshops[0][1], "ws")

    def test_end_workshop_returns_to_the_lecture_file(self):
        files = emit_files(
            slide("A", "a"),
            code("f();"),
            workshop("Workshop", "ws"),
            code("g();"),
            code("h();", tags=("end-workshop",)),
            slide("B", "b"),
            code("i();"),
        )
        ws = files.workshops[0][1]
        assert "g();" in ws and "h();" not in ws and "i();" not in ws
        # The closer opens a new lecture section (no slide tag: numbered).
        assert "    h();" in _function_body(files.main, "section_03")
        assert "    i();" in _function_body(files.main, "b")
        assert files.main.endswith("int main() {\n    a();\n    section_03();\n    b();\n}\n")

    def test_every_separated_workshop_gets_a_file_in_deck_order(self):
        files = emit_files(
            slide("A", "a"),
            code("f();"),
            workshop("First", "ws-1"),
            code("g();"),
            slide("Between", "between", "end-workshop"),
            code("h();"),
            workshop("Second", "ws-2"),
            md("Only prose here."),
        )
        assert [ordinal for ordinal, _ in files.workshops] == [1, 2]
        first, second = (text for _, text in files.workshops)
        assert "g();" in first
        assert "// Only prose here." in second
        assert second.endswith("int main() {}\n")
        assert "    h();" in _function_body(files.main, "between")
        assert set(files.companion_files("d")) == {"d.hpp", "d_workshop_1.cpp", "d_workshop_2.cpp"}

    def test_back_to_back_ranges_form_one_workshop_file(self):
        # ``workshop-task-N`` sub-slides each open a range under the
        # canonical detector; the tasks build on each other, so they share
        # one file (each still its own section function).
        files = emit_files(
            slide("A", "a"),
            code("f();"),
            workshop("Workshop", "workshop-monitor"),
            code("int check(double c) { return c > 1 ? 1 : 0; }", tags=("keep",)),
            md("## Task 1", tags=("subslide",), slide_id="workshop-task-1"),
            code("check(2.0)"),
            md("## Task 2", tags=("subslide",), slide_id="workshop-task-2"),
            code("check(0.5)"),
        )
        assert [ordinal for ordinal, _ in files.workshops] == [1]
        ws = files.workshops[0][1]
        assert "int check(double c)" in ws
        assert "    CLM_DISPLAY(check(2.0));" in _function_body(ws, "workshop_task_1")
        assert "    CLM_DISPLAY(check(0.5));" in _function_body(ws, "workshop_task_2")
        assert ws.endswith("int main() {\n    workshop_task_1();\n    workshop_task_2();\n}\n")
        assert set(files.companion_files("d")) == {"d.hpp", "d_workshop_1.cpp"}

    def test_global_cell_inside_a_workshop_stays_in_the_workshop_file(self):
        files = emit_files(
            slide("A", "a"),
            code("f();"),
            workshop("Workshop", "ws"),
            code("int answer{42};", tags=("global",)),
            code("answer"),
        )
        assert files.header is not None
        assert "answer" not in files.header
        ws = files.workshops[0][1]
        assert "\nint answer{42};\n" in ws
        assert ws.index("int answer{42};") < ws.index("void ws()")

    def test_reference_from_a_workshop_does_not_promote_a_lecture_variable(self):
        files = emit_files(
            slide("A", "a"),
            code("int x{1};"),
            workshop("Workshop", "ws"),
            code("x"),
        )
        assert "    int x{1};" in _function_body(files.main, "a")

    def test_reference_from_a_later_lecture_section_promotes_across_a_workshop(self):
        files = emit_files(
            slide("A", "a"),
            code("int x{1};"),
            workshop("Workshop", "ws"),
            code("f();"),
            slide("B", "b", "end-workshop"),
            code("x"),
        )
        assert "\nint x{1};\n" in files.main
        assert "int x{1};" not in files.workshops[0][1]

    def test_code_along_workshop_file_is_a_skeleton(self):
        files = emit_files(
            slide("A", "a"),
            code("", original_source="int base{1};"),
            workshop("Workshop", "ws"),
            code("", original_source="int twice(int x) { return 2 * x; }"),
            code("twice(base)", tags=("keep",)),
            code("f();", tags=("keep",)),
        )
        ws = files.workshops[0][1]
        assert "// TODO: define twice" in ws
        assert "return 2 * x" not in ws
        # Deck-global dangling scan: ``base`` is blanked in the lecture file.
        assert f"    {DANGLING_NOTE}\n    // CLM_DISPLAY(twice(base));" in ws
        assert "    f();" in _function_body(ws, "ws")
        assert "// TODO: define base" in _function_body(files.main, "a")

    def test_deck_defined_main_is_per_file(self):
        files = emit_files(
            slide("A", "a"),
            code("f();"),
            code("int main() { f(); }"),
            workshop("Workshop", "ws"),
            code("g();"),
        )
        assert "int main() { f(); }" in files.main
        assert files.main.count("int main()") == 1
        ws = files.workshops[0][1]
        assert ws.endswith("int main() {\n    ws();\n}\n")

    def test_workshop_defined_main_suppresses_its_generated_main(self):
        files = emit_files(
            slide("A", "a"),
            code("f();"),
            workshop("Workshop", "ws"),
            code("int main() { return 0; }"),
        )
        ws = files.workshops[0][1]
        assert ws.count("int main()") == 1
        assert files.main.endswith("int main() {\n    a();\n}\n")

    def test_explicit_workshop_ranges_override_detection(self):
        cells = [slide("A", "a"), code("f();"), slide("B", "b"), code("g();")]
        files = emit_files(*cells, workshop_ranges=[(2, 4)])
        assert files.header is not None
        assert "g();" in files.workshops[0][1]
        assert "g();" not in files.main
        assert emit_files(*cells, workshop_ranges=[]).header is None

    def test_lecture_using_directive_moves_to_the_header(self):
        files = emit_files(
            slide("A", "a"),
            code("#include <string>"),
            code("using namespace std::literals;"),
            code('auto s = "x"s;'),
            workshop("Workshop", "ws"),
            code('"y"sv'),
        )
        assert files.header is not None
        assert "\nusing namespace std::literals;\n" in files.header
        assert "using namespace" not in files.main
        assert "using namespace" not in files.workshops[0][1]

    def test_using_directive_stays_in_place_without_a_header(self):
        tu = emit(slide("A", "a"), code("using namespace std;"), code("f();"))
        assert tu.index("using namespace std;") < tu.index("void a()")

    def test_using_directive_inside_a_workshop_stays_there(self):
        files = emit_files(
            slide("A", "a"),
            code("f();"),
            workshop("Workshop", "ws"),
            code("using namespace std;"),
            code("g();"),
        )
        assert "using namespace" not in files.header
        assert "using namespace std;" in files.workshops[0][1]

    def test_lecture_definitions_a_workshop_uses_are_reported(self):
        files = emit_files(
            slide("A", "a"),
            code("struct Point { int x; };"),
            code("int scale{2};"),
            code("int shared{3};", tags=("global",)),
            code("void helper() {}"),
            workshop("Workshop", "ws"),
            code("Point p{scale * shared};\nint local{1};\nlocal"),
            code("", original_source="int twice(int v) { return 2 * v; }"),
        )
        # ``shared`` is in the header, ``local``/``twice`` are the workshop's
        # own, ``helper`` is unused: only Point and scale are reported.
        assert files.workshop_lecture_uses == {1: ("Point", "scale")}

    def test_lecture_use_report_skips_names_the_workshop_declares_locally(self):
        files = emit_files(
            slide("A", "a"),
            code("int i{0};\nint pos{1};\nstruct Point { int x; };"),
            workshop("Workshop", "ws"),
            code(
                "void loop() {\n    for (int i{1}; i <= 5; ++i) { std::cout << i; }\n"
                "    std::string::size_type pos = 0;\n    return;\n}\n"
                "std::size(v)\nstd::vector<Point> pts;"
            ),
        )
        # ``i``/``pos`` are the workshop's own locals, ``std::size`` is
        # qualified; ``Point`` is a genuine lecture use.
        assert files.workshop_lecture_uses == {1: ("Point",)}

    def test_no_lecture_uses_reported_when_nothing_is_used(self):
        files = emit_files(
            slide("A", "a"), code("int x{1};"), workshop("Workshop", "ws"), code("int y{2};")
        )
        assert files.workshop_lecture_uses == {}
        assert emit_files(slide("A", "a"), code("int x{1};")).workshop_lecture_uses == {}

    def test_merge_adjacent_workshop_ranges(self):
        assert merge_adjacent_workshop_ranges([(2, 5), (5, 9), (12, 14), (14, 15)]) == [
            (2, 9),
            (12, 15),
        ]
        assert merge_adjacent_workshop_ranges([]) == []

    def test_section_names_stay_unique_across_files(self):
        files = emit_files(
            slide("A", "same"),
            code("f();"),
            workshop("Workshop", "same"),
            code("g();"),
        )
        assert "void same()" in files.main
        assert "void same_2()" in files.workshops[0][1]


# ---------------------------------------------------------------------------
# Compile smoke test (runs only when a C++ compiler is available)
# ---------------------------------------------------------------------------

_CXX = shutil.which("g++") or shutil.which("clang++")
# The vendored support headers the CMake export puts on the include path.
_SUPPORT_INCLUDE_DIR = (
    Path(str(importlib.resources.files("clm"))) / "data" / "cpp_export" / "include"
)


@pytest.mark.skipif(_CXX is None, reason="no C++ compiler on PATH")
class TestEmittedCodeCompiles:
    def _check(self, tu: str, tmp_path):
        path = tmp_path / "deck.cpp"
        path.write_text(tu, encoding="utf-8")
        proc = subprocess.run(
            [_CXX, "-std=c++20", "-fsyntax-only", f"-I{_SUPPORT_INCLUDE_DIR}", str(path)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr

    def test_representative_deck_compiles(self, tmp_path):
        tu = emit(
            md("# Vectors", tags=("slide",), slide_id="vectors"),
            code("#include <iostream>\n#include <vector>"),
            md("A vector of numbers."),
            code("std::vector<int> numbers{1, 2, 3};"),
            code(
                "int sum(const std::vector<int>& xs) {\n"
                "    int result{0};\n"
                "    for (int x : xs) { result += x; }\n"
                "    return result;\n"
                "}"
            ),
            code('std::cout << sum(numbers) << "\\n";'),
            code("numbers.size()"),
            md('## Points with a "quoted" heading', tags=("subslide",), slide_id="points"),
            code("struct Point { int x; int y; };"),
            code("Point p{3, 4};"),
            code("p.x + p.y"),
            md("## Reuse", tags=("subslide",), slide_id="reuse"),
            code("numbers.push_back(p.x);"),
            code("sum(numbers)"),
        )
        self._check(tu, tmp_path)

    def test_display_fallback_for_unstreamable_type_compiles(self, tmp_path):
        tu = emit(code("struct Opaque { int v; };"), code("Opaque o{1};"), code("o"))
        self._check(tu, tmp_path)

    def test_void_display_expression_compiles(self, tmp_path):
        # A bare member call classifies as expr_display (the identifier-then-
        # paren call_stmt pattern doesn't match through the `.`); push_back
        # returns void, so this exercises the void branch of clm::display.
        tu = emit(
            code("#include <vector>"),
            code("std::vector<int> numbers{1, 2, 3};"),
            code("numbers.push_back(4)"),
        )
        self._check(tu, tmp_path)

    def test_promoted_variable_used_by_hoisted_function_compiles(self, tmp_path):
        tu = emit(
            slide("Counter", "counter"),
            code("int counter{0};\nvoid bump() { ++counter; }"),
            code("bump();"),
            slide("Later", "later"),
            code("counter"),
        )
        self._check(tu, tmp_path)

    def test_code_along_todos_compile(self, tmp_path):
        tu = emit(
            slide("A", "a"),
            code("#include <iostream>", tags=("keep",)),
            code("", original_source="int x{1};"),
            code('std::cout << "kept\\n";', tags=("keep",)),
            blanks_code_cells=True,
        )
        self._check(tu, tmp_path)

    def test_code_along_with_dangling_keep_cells_compiles(self, tmp_path):
        # A blanked definition, a kept caller (dangling), a kept cell that
        # depends on the dangling one, a blanked promoted variable and an
        # untouched kept statement — the skeleton must compile as shipped.
        tu = emit(
            slide("Functions", "functions"),
            code("#include <iostream>", tags=("keep",)),
            code("", original_source="int twice(int x) { return 2 * x; }"),
            code("twice(21)", tags=("keep",)),
            code("int answer{twice(21)};\nstd::cout << answer;", tags=("keep",)),
            code("", original_source="int shared{1};"),
            code('std::cout << "kept\\n";', tags=("keep",)),
            slide("Later", "later"),
            code("", original_source="shared++;"),
            code("int local{2};\nlocal", tags=("keep",)),
        )
        assert tu.count(DANGLING_NOTE) == 2
        assert "// TODO: define twice" in tu
        assert "// TODO: define shared" in tu
        self._check(tu, tmp_path)

    def test_multi_file_deck_compiles_against_its_header(self, tmp_path):
        files = emit_files(
            slide("Vectors", "vectors"),
            code("#include <vector>"),
            code("int scale{2};", tags=("global",)),
            code("std::vector<int> numbers{1, 2, 3};"),
            code("numbers.size()"),
            workshop("Workshop", "workshop-scaled"),
            code("int scaled(int x) { return scale * x; }"),
            code("scaled(21)"),
            stem="deck",
        )
        assert files.header is not None
        (tmp_path / "deck.hpp").write_text(files.header, encoding="utf-8")
        self._check(files.main, tmp_path)
        self._check(files.workshops[0][1], tmp_path)

    def test_digit_separator_cell_compiles(self, tmp_path):
        # #922: the declaration and the call stay together in the body.
        self._check(emit(code("void h(int, long) {}"), code(_MIXED_DECL_STMT_CELL)), tmp_path)

    def test_requires_clause_template_compiles(self, tmp_path):
        # #921: the constrained template must not be display-wrapped.
        tu = emit(
            code("#include <concepts>"), code(_REQUIRES_CLAUSE_TEMPLATE), code("ordered_min(1, 2)")
        )
        self._check(tu, tmp_path)
