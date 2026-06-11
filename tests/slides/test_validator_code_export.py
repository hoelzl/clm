"""Tests for the ``code_export`` conformance check (#331).

The redefinition rule's negative tests encode the four false-positive traps
found while validating the classifier against the full CppCourses corpus:
language-paired cells, legal overloads, template specializations, and
``start``/``completed`` pairs. Each of these cost a debugging round in the
feasibility scan — they must never regress into findings.
"""

from __future__ import annotations

import textwrap

from clm.slides.cpp_code_analysis import (
    classify_item,
    classify_source,
    normalize_args,
    split_top_level,
    strip_comments_and_strings,
)
from clm.slides.validator import validate_file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_deck(tmp_path, content, name="slides_test.cpp"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _code_export_findings(path):
    result = validate_file(path, checks=["code_export"])
    return [f for f in result.findings if f.category == "code_export"]


# ---------------------------------------------------------------------------
# Classifier unit tests
# ---------------------------------------------------------------------------


class TestStripCommentsAndStrings:
    def test_line_and_block_comments_removed(self):
        src = "int i{}; // trailing {\n/* block ; */ int j{};"
        cleaned = strip_comments_and_strings(src)
        assert "{" not in cleaned.split(";")[0].replace("{}", "")
        assert "block" not in cleaned
        assert "int j{};" in cleaned

    def test_raw_string_removed(self):
        src = 'auto s = R"x(unbalanced { ; )x";'
        cleaned = strip_comments_and_strings(src)
        assert "unbalanced" not in cleaned
        assert cleaned.count("{") == 0

    def test_string_and_char_literals_blanked(self):
        cleaned = strip_comments_and_strings("call(\"a;b\", ';');")
        assert "a;b" not in cleaned
        assert cleaned.count(";") == 1


class TestSplitTopLevel:
    def test_if_else_is_one_item(self):
        items = split_top_level("if (x) { a(); } else { b(); }")
        assert len(items) == 1

    def test_do_while_is_one_item(self):
        items = split_top_level("do { x(); } while (cond);")
        assert len(items) == 1

    def test_try_catch_is_one_item(self):
        items = split_top_level("try { risky(); } catch (...) { handle(); }")
        assert len(items) == 1

    def test_definition_then_statement_splits(self):
        items = split_top_level("int square(int x) { return x * x; } square(3);")
        assert len(items) == 2


class TestNormalizeArgs:
    def test_strips_names_and_defaults(self):
        assert normalize_args("int x, double y = 1.0") == "int,double"

    def test_reference_and_const(self):
        assert normalize_args("std::string const& name") == "std::stringconst&"

    def test_template_args_keep_commas_inside(self):
        assert normalize_args("std::map<int, int> m") == "std::map<int,int>"


class TestClassifyItem:
    def test_variable_declaration(self):
        item = classify_item("int i{};")
        assert (item.category, item.name) == ("var_decl", "i")

    def test_east_const_variable(self):
        assert classify_item("int const i{17};").category == "var_decl"

    def test_array_variable(self):
        item = classify_item("int numbers[5];")
        assert (item.category, item.name) == ("var_decl", "numbers")

    def test_function_definition_signature(self):
        item = classify_item("int add(int x, int y) { return x + y; }")
        assert item.category == "fn_def"
        assert item.signature == "add(int,int)"

    def test_const_member_function_signature_differs(self):
        plain = classify_item("int MyVector::at(int i) { return 0; }")
        const = classify_item("int MyVector::at(int i) const { return 0; }")
        assert plain.category == const.category == "member_fn_def"
        assert plain.signature != const.signature
        assert const.signature.endswith(" const")

    def test_out_of_class_constructor(self):
        item = classify_item("MyVector::MyVector(int n) { resize(n); }")
        assert item.category == "member_fn_def"

    def test_type_definition(self):
        item = classify_item("struct Point { int x; int y; };")
        assert (item.category, item.name) == ("type_def", "Point")

    def test_template_specialization_name_includes_args(self):
        primary = classify_item("template <typename T> struct TypeName { };")
        spec = classify_item("template <> struct TypeName<int> { };")
        assert primary.name == "TypeName"
        assert spec.name == "TypeName<int>"

    def test_main_definition(self):
        assert classify_item("int main() { return 0; }").category == "main_def"

    def test_display_expression_vs_statement(self):
        assert classify_item("i + 1").category == "expr_display"
        assert classify_item("i + 1;").category == "expr_stmt"

    def test_output_statement(self):
        assert classify_item('std::cout << "hi" << i;').category == "output_stmt"


class TestClassifySource:
    def test_includes_extracted(self):
        items = classify_source("#include <iostream>\nint i{};")
        assert [it.category for it in items] == ["include", "var_decl"]

    def test_comments_do_not_confuse_classification(self):
        items = classify_source("// int shadow{};\nint real{};")
        assert [it.name for it in items] == ["real"]


# ---------------------------------------------------------------------------
# Redefinition rule — positives
# ---------------------------------------------------------------------------


class TestRedefinitionErrors:
    def test_variable_redefined_in_untagged_cells(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %% tags=["keep"]
            int i{};

            // %%
            int i{};
            """,
        )
        findings = _code_export_findings(p)
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert "variable 'i' redefined" in findings[0].message
        # Untagged cells appear in both language views; the finding is merged.
        assert "both language views" in findings[0].message

    def test_function_redefined_same_signature(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            void greet(std::string name) {}

            // %%
            void greet(std::string who) {}
            """,
        )
        findings = _code_export_findings(p)
        assert len(findings) == 1
        assert "function" in findings[0].message
        assert findings[0].severity == "error"

    def test_type_redefined(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            struct Point { int x; };

            // %%
            struct Point { int x; int y; };
            """,
        )
        findings = _code_export_findings(p)
        assert len(findings) == 1
        assert "type 'Point' redefined" in findings[0].message

    def test_redefinition_across_untagged_and_lang_cell(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            int i{};

            // %% lang="de"
            int i{};
            """,
        )
        findings = _code_export_findings(p)
        assert len(findings) == 1
        assert 'lang="de" view' in findings[0].message


# ---------------------------------------------------------------------------
# Redefinition rule — the four false-positive traps (must stay silent)
# ---------------------------------------------------------------------------


class TestRedefinitionFalsePositiveTraps:
    def test_trap_1_language_paired_cells(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %% lang="de"
            int i_vi{};

            // %% lang="en"
            int i_vi{};
            """,
        )
        assert _code_export_findings(p) == []

    def test_trap_2a_overloads_by_parameter_type(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            void print(int value) {}

            // %%
            void print(double value) {}
            """,
        )
        assert _code_export_findings(p) == []

    def test_trap_2b_const_vs_nonconst_member_function(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            int MyVector::at(int i) { return data[i]; }

            // %%
            int MyVector::at(int i) const { return data[i]; }
            """,
        )
        assert _code_export_findings(p) == []

    def test_trap_3_template_specialization(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            template <typename T> struct TypeName { };

            // %%
            template <> struct TypeName<int> { };
            """,
        )
        assert _code_export_findings(p) == []

    def test_trap_4_start_completed_pair(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %% tags=["start"]
            class Stack {};

            // %% tags=["completed"]
            class Stack {
            public:
                void push(int value);
            };
            """,
        )
        assert _code_export_findings(p) == []

    def test_del_cells_are_excluded(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %% tags=["del"]
            int i{};

            // %%
            int i{};
            """,
        )
        assert _code_export_findings(p) == []


# ---------------------------------------------------------------------------
# main() whitelist
# ---------------------------------------------------------------------------


class TestMainWhitelist:
    def test_main_without_marker_is_error(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            int main() { return 0; }
            """,
        )
        findings = _code_export_findings(p)
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert "main()" in findings[0].message
        assert "clm: allow-main" in findings[0].suggestion

    def test_main_with_header_marker_is_allowed(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // clm: allow-main

            // %%
            int main() { return 0; }
            """,
        )
        assert _code_export_findings(p) == []

    def test_marker_inside_cell_body_does_not_count(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            // clm: allow-main
            int main() { return 0; }
            """,
        )
        findings = _code_export_findings(p)
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# Jinja in code cells
# ---------------------------------------------------------------------------


class TestJinjaInCodeCells:
    def test_jinja_expression_is_warning(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            print({{ value }});
            """,
        )
        findings = _code_export_findings(p)
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "Jinja" in findings[0].message

    def test_jinja_statement_is_warning(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            {% if lang == "de" %}
            int zaehler{};
            {% endif %}
            """,
        )
        findings = _code_export_findings(p)
        assert len(findings) == 1
        assert findings[0].severity == "warning"

    def test_jinja_string_literal_escape_is_not_flagged(self, tmp_path):
        # The escape idiom from the builder deck: Jinja emitting literal
        # braces. It expands to plain C++ before the export runs.
        p = _write_deck(
            tmp_path,
            """\
            // %% tags=["keep"]
            sendRequest("https://example.com", {{'{{"Content-Type", "application/json"}}'}}, {});
            """,
        )
        assert _code_export_findings(p) == []

    def test_nested_brace_init_is_not_jinja(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            std::array<std::array<int, 2>, 2> arr{{1, 2}, {3, 4}};
            """,
        )
        assert _code_export_findings(p) == []


# ---------------------------------------------------------------------------
# Mixed cells
# ---------------------------------------------------------------------------


class TestMixedCells:
    def test_definition_plus_statement_is_info(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            int square(int x) { return x * x; }
            square(3);
            """,
        )
        findings = _code_export_findings(p)
        assert len(findings) == 1
        assert findings[0].severity == "info"
        assert "mixes definitions and statements" in findings[0].message

    def test_pure_definition_cell_is_silent(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            int square(int x) { return x * x; }
            """,
        )
        assert _code_export_findings(p) == []

    def test_pure_statement_cell_is_silent(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            square(3);
            """,
        )
        assert _code_export_findings(p) == []


# ---------------------------------------------------------------------------
# Scope: C++ decks only, default bundle membership
# ---------------------------------------------------------------------------


class TestCheckScope:
    def test_noop_for_python_decks(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            # %%
            i = 1

            # %%
            i = 1
            """,
            name="slides_test.py",
        )
        assert _code_export_findings(p) == []

    def test_included_in_default_check_bundle(self, tmp_path):
        p = _write_deck(
            tmp_path,
            """\
            // %%
            int i{};

            // %%
            int i{};
            """,
        )
        result = validate_file(p)
        assert any(f.category == "code_export" for f in result.findings)
