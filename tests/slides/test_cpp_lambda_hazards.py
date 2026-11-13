"""Lambdas outside the display macros that kill the xeus-cpp kernel, or can (#1228).

Every shape was run in the notebook image built for #1228 (xeus-cpp 0.10.0 on
libstdc++ 15), each in a fresh ``xcpp20`` and a fresh ``xcpp23`` kernel after
the same three cells (the includes, ``std::vector<int> v{1, 2, 3}; int
total{0};`` and a function template ``apply``); the lambda matrix was run
three times with identical outcomes. What came out:

- **A lambda anywhere inside a top-level ``for`` or ``switch`` kills the
  kernel under both standards.** Classic and range-based ``for``, with and
  without braces; the lambda passed inline, called at once, in a declaration
  inside the loop, or bound to a name there. ``clm validate`` warns.
- **A lambda in a top-level call or expression statement, or below ``do`` /
  ``try``, is unpredictable.** ``std::ranges::sort(v, <lambda>);`` runs under
  ``xcpp23`` and kills ``xcpp20``; ``total = std::accumulate(..., <lambda>);``
  does the opposite; ``apply(<lambda>);`` kills both on libstdc++ 15 and ran
  under ``xcpp20`` on libstdc++ 14; and one statement that ran after the
  cells above killed the kernel after a different set of includes. Some
  statements of this shape ran everywhere they were tried
  (``std::for_each(first, last, <lambda>);``), but nothing in the text tells
  them apart. ``clm validate`` reports the shape as information, so the
  author of a deck whose kernel dies finds the candidates.
- **The rest ran in every configuration** and is silent: a lambda in a
  declaration, in a ``std::cout <<`` statement, below ``if`` / ``while``, in a
  bare block, in a function, class or named-lambda body, and a named lambda
  used anywhere — a top-level ``for`` included.

A lambda inside ``SHOW`` / ``CLM_DISPLAY`` belongs to the display-macro rule
(#1044, ``test_validator_code_export.py``) and is not reported twice.
"""

from __future__ import annotations

import pytest

from clm.slides.cpp_show import find_lambda_hazards
from clm.slides.validator import validate_file

PRINTING = '[](int i) { std::cout << i << "\\n"; }'


def _deck_with_cell(tmp_path, body, header="// %%"):
    path = tmp_path / "slides_test.cpp"
    path.write_text(f"{header}\n{body}\n", encoding="utf-8")
    return path


def _lambda_findings(path):
    result = validate_file(path, checks=["code_export"])
    return [f for f in result.findings if "top-level" in f.message]


class TestLambdaInsideForOrSwitch:
    """Dead under ``xcpp20`` and ``xcpp23`` alike."""

    @pytest.mark.parametrize(
        ("body", "statement"),
        [
            (f"for (int k = 0; k < 1; ++k) {{ std::ranges::for_each(v, {PRINTING}); }}", "for"),
            (f"for (int x : v) {{ std::ranges::for_each(v, {PRINTING}); }}", "for"),
            # Without braces.
            (f"for (int k = 0; k < 1; ++k) std::ranges::for_each(v, {PRINTING});", "for"),
            # A comparator is enough: no output, a lone ``return``.
            (
                "for (int k = 0; k < 1; ++k) "
                "{ std::ranges::sort(v, [](int a, int b) { return a > b; }); }",
                "for",
            ),
            # Called at once.
            ('for (int x : v) { std::cout << [](int i) { return i * 2; }(x) << "\\n"; }', "for"),
            # In a declaration inside the loop — the shape that is safe at top level.
            (
                "for (int k = 0; k < 1; ++k) { auto n = std::ranges::count_if(v, "
                '[](int i) { return i > 1; }); std::cout << n << "\\n"; }',
                "for",
            ),
            # Even bound to a name there.
            (
                "for (int k = 0; k < 1; ++k) "
                '{ auto f = [](int i) { std::cout << i << "\\n"; }; f(k); }',
                "for",
            ),
            (
                f"switch (1) {{ case 1: std::ranges::for_each(v, {PRINTING}); break; "
                "default: break; }",
                "switch",
            ),
            (
                "switch (1) { case 1: { auto f = [](int i) { std::cout << i; }; f(1); break; } "
                "default: break; }",
                "switch",
            ),
        ],
    )
    def test_kills_the_kernel(self, body, statement):
        hazards = find_lambda_hazards(body)
        assert len(hazards) == 1
        assert hazards[0].kills_kernel
        assert hazards[0].statement == statement


class TestLambdaInATopLevelStatement:
    """Which of these run depends on the callee and the configuration: all are reported."""

    @pytest.mark.parametrize(
        "body",
        [
            f"std::ranges::for_each(v, {PRINTING});",
            "std::ranges::for_each(v, [&](int i) { total += i; });",
            "std::ranges::for_each(v, [](int) {});",
            "std::ranges::sort(v, [](int a, int b) { return a > b; });",
            "std::ranges::transform(v, v.begin(), [](int i) { return i * 2; });",
            f"std::for_each(v.begin(), v.end(), {PRINTING});",
            f"apply({PRINTING});",
            "total = std::accumulate(v.begin(), v.end(), 0, [](int s, int n) { return s + n; });",
            "v.erase(std::remove_if(v.begin(), v.end(), [](int i) { return i > 1; }), v.end());",
            'std::println("{}", std::ranges::count_if(v, [](int i) { return i > 1; }));',
            # Called on the spot.
            f"{PRINTING}(1);",
            f"do {{ std::ranges::for_each(v, {PRINTING}); }} while (false);",
            f"try {{ std::ranges::for_each(v, {PRINTING}); }} catch (...) {{}}",
        ],
    )
    def test_is_reported_as_fragile(self, body):
        hazards = find_lambda_hazards(body)
        assert len(hazards) == 1
        assert not hazards[0].kills_kernel
        assert hazards[0].statement == ""


class TestShapesThatRan:
    """Ran under both kernels: not reported."""

    @pytest.mark.parametrize(
        "body",
        [
            # A lambda in a declaration.
            f"auto f = {PRINTING};",
            "const auto by_size = [](int a, int b) { return a < b; };",
            "auto n = std::ranges::count_if(v, [](int i) { return i > 1; });",
            "int t{std::accumulate(v.begin(), v.end(), 0, [](int s, int n) { return s + n; })};",
            # A named lambda, used anywhere.
            "std::ranges::for_each(v, f);",
            "for (int k = 0; k < 1; ++k) { std::ranges::for_each(v, f); }",
            # In a ``std::cout <<`` statement.
            'std::cout << std::ranges::count_if(v, [](int i) { return i > 1; }) << "\\n";',
            # Below ``if`` / ``while``, and in a bare block.
            f"if (true) {{ std::ranges::for_each(v, {PRINTING}); }}",
            "if (true) { std::ranges::sort(v, [](int a, int b) { return a > b; }); }",
            f"while (once-- > 0) {{ std::ranges::for_each(v, {PRINTING}); }}",
            f"{{ std::ranges::for_each(v, {PRINTING}); }}",
            # In a body: the loop that kills the kernel at top level runs here.
            f"void run() {{ for (int k = 0; k < 1; ++k) {{ std::ranges::for_each(v, {PRINTING}); "
            "} }",
            f"auto g = [] {{ for (int k = 0; k < 1; ++k) {{ std::ranges::for_each(v, {PRINTING}); "
            "} };",
            f"void run() {{ std::ranges::for_each(v, {PRINTING}); }}",
        ],
    )
    def test_is_silent(self, body):
        assert find_lambda_hazards(body) == []

    @pytest.mark.parametrize(
        "body",
        [
            # Detection, not kernel behaviour: brackets that are not lambdas.
            "for (auto& [key, value] : table) { v[0] = value; }",
            "for (const auto [key, value] : table) { total += value; }",
            "for (int k = 0; k < 3; ++k) { [[likely]] if (k) { a[k] = b[k]; } }",
            "for (int k = 0; k < 3; ++k) { int row[3]{k, k, k}; total += row[0]; }",
            "switch (v[0]) { case 1: total = v[1]; break; default: break; }",
            "v[0] = v[1];",
            "std::ranges::sort(v);",
            # Text that only looks like one.
            "// std::ranges::sort(v, [](int a, int b) { return a > b; });\nint x{1};",
            '/* for (;;) { auto f = [] {}; } */ std::cout << "x";',
            'const char* s{"for (;;) { auto f = [] {}; }"};',
            "#define EACH(v) std::ranges::for_each(v, [](int i) { std::cout << i; })",
            "",
        ],
    )
    def test_text_that_is_not_a_lambda_is_silent(self, body):
        assert find_lambda_hazards(body) == []


class TestLambdasOfTheDisplayMacros:
    """A lambda inside ``SHOW`` is the display-macro rule's (#1044): no second finding."""

    @pytest.mark.parametrize(
        "body",
        [
            "SHOW([](int a) { return a; }(1));",
            "CLM_DISPLAY([](int a) { return a; }(1));",
            "for (int i = 0; i < 1; ++i) { SHOW([](int a) { return a; }(i)); }",
            # Mid-edit: a ``SHOW`` that is never closed owns the rest of the cell.
            "SHOW([](int a) { return a; }(1)",
        ],
    )
    def test_is_left_to_the_show_rule(self, body):
        assert find_lambda_hazards(body) == []

    def test_a_lambda_next_to_a_show_in_the_same_loop_is_still_reported(self):
        body = "for (int x : v) { SHOW(x); std::ranges::for_each(v, [](int i) { total += i; }); }"
        hazards = find_lambda_hazards(body)
        assert [hazard.kills_kernel for hazard in hazards] == [True]


class TestValidateFindings:
    def test_for_loop_is_a_warning_that_says_how_to_fix(self, tmp_path):
        body = "for (int x : v) {\n    std::ranges::for_each(v, [](int i) { total += i; });\n}"

        findings = _lambda_findings(_deck_with_cell(tmp_path, body))

        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity == "warning"
        assert finding.category == "code_export"
        assert finding.line == 1
        assert finding.message.startswith("`for (int x : v) { std::ranges::for_each(v, ")
        assert finding.message.endswith(
            "holds a lambda inside a top-level `for`, which kills the kernel"
        )
        assert "before the statement" in finding.suggestion
        assert "inside a function body is fine" in finding.suggestion

    def test_switch_is_named_in_the_message(self, tmp_path):
        body = "switch (1) { case 1: { auto f = [] {}; f(); break; } default: break; }"
        findings = _lambda_findings(_deck_with_cell(tmp_path, body))
        assert len(findings) == 1
        assert "inside a top-level `switch`, which kills the kernel" in findings[0].message

    def test_fragile_statement_is_information_not_a_warning(self, tmp_path):
        body = "std::ranges::sort(v, [](int a, int b) { return a > b; });"

        findings = _lambda_findings(_deck_with_cell(tmp_path, body))

        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity == "info"
        assert finding.message == (
            "`std::ranges::sort(v, [](int a, int b) { return a > b; });` holds an inline "
            "lambda in a top-level statement, which can kill the kernel"
        )
        assert "name the lambda first" in finding.suggestion
        assert "is safe" in finding.suggestion

    def test_one_finding_per_statement(self, tmp_path):
        body = (
            "std::ranges::sort(v, [](int a, int b) { return a > b; });\n"
            "for (int x : v) { std::ranges::for_each(v, [](int i) { total += i; }); }\n"
            "auto f = [](int i) { return i; };"
        )
        findings = _lambda_findings(_deck_with_cell(tmp_path, body))
        assert sorted(f.severity for f in findings) == ["info", "warning"]

    @pytest.mark.parametrize("tag", ["start", "del"])
    def test_cell_that_is_never_executed_is_silent(self, tmp_path, tag):
        body = "for (int x : v) { std::ranges::for_each(v, [](int i) { total += i; }); }"
        path = _deck_with_cell(tmp_path, body, f'// %% tags=["{tag}"]')
        assert _lambda_findings(path) == []

    @pytest.mark.parametrize("tag", ["completed", "keep", "global"])
    def test_executed_cell_kinds_are_checked(self, tmp_path, tag):
        body = "for (int x : v) { std::ranges::for_each(v, [](int i) { total += i; }); }"
        path = _deck_with_cell(tmp_path, body, f'// %% tags=["{tag}"]')
        assert len(_lambda_findings(path)) == 1

    def test_show_lambda_in_a_loop_gets_the_show_finding_only(self, tmp_path):
        body = "for (int i = 0; i < 1; ++i) { SHOW([](int a) { return a; }(i)); }"
        result = validate_file(_deck_with_cell(tmp_path, body), checks=["code_export"])
        messages = [f.message for f in result.findings if "kill" in f.message]
        assert len(messages) == 1
        assert "holds an inline lambda, which kills the kernel" in messages[0]

    def test_other_languages_are_not_checked(self, tmp_path):
        path = tmp_path / "slides_test.py"
        path.write_text("# %%\nfor x in v: f = [](int i) { return i; }\n", encoding="utf-8")
        assert _lambda_findings(path) == []
