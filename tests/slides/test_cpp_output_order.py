"""C++ cells whose notebook output is displayed in another order than written (#1228).

In a notebook cell the xeus-cpp kernel replays everything written through
``std::cout`` (``SHOW`` / ``CLM_DISPLAY`` included) before everything written
through C stdio (``std::print``, ``std::println``, ``printf``, ``puts``),
whatever the order in the code. ``clm validate`` warns where that changes
what the reader sees.

The mechanism was measured in the notebook image built for #1228 (xeus-cpp
0.10.0; ``xcpp23``, and ``xcpp20`` for ``printf`` / ``puts``), each shape in
one cell: straight-line code in both orders, a ``for`` loop with and without
braces, an ``if`` block, a whole loop before or after a write of the other
kind, a function, a named lambda and a member function called twice,
``std::println(std::cout, ...)``, and ``stderr``. The *flagged* shapes came
out reordered, the *silent* ones in program order. The remaining parameters
vary the writer or the head of the block, not the mechanism; the last group
of silent cases is about the text-level detection, not the kernel.
"""

from __future__ import annotations

import pytest

from clm.slides.cpp_output_order import (
    OutputOrderProblem,
    find_output_order_problem,
    find_undisplayed_write,
)
from clm.slides.validator import validate_file


def _deck_with_cell(tmp_path, body, header="// %%"):
    path = tmp_path / "slides_test.cpp"
    path.write_text(f"{header}\n{body}\n", encoding="utf-8")
    return path


def _order_findings(path):
    result = validate_file(path, checks=["code_export"])
    return [f for f in result.findings if "notebook" in f.message]


class TestCStdioWrittenBeforeCout:
    """Straight-line code: the later ``std::cout`` output is displayed first."""

    @pytest.mark.parametrize(
        ("body", "stdio", "stream"),
        [
            ('std::println("1"); std::cout << "2\\n";', "std::println", "std::cout"),
            ('std::print("1\\n"); std::cout << "2\\n";', "std::print", "std::cout"),
            ('std::println("1"); SHOW(x);', "std::println", "SHOW"),
            ('std::println("1"); CLM_DISPLAY(x);', "std::println", "CLM_DISPLAY"),
            ('std::printf("1\\n"); std::cout << "2\\n";', "printf", "std::cout"),
            ('printf("1\\n"); std::cout << "2\\n";', "printf", "std::cout"),
            ('std::puts("1"); std::cout << "2\\n";', "puts", "std::cout"),
            # ``using std::cout;`` decks.
            ('std::println("1"); cout << "2\\n";', "std::println", "std::cout"),
            # A ``std::cout`` write on either side: the one after is the problem.
            (
                'std::cout << "1\\n"; std::println("2"); std::cout << "3\\n";',
                "std::println",
                "std::cout",
            ),
            # ``std::println()`` prints the newline to stdout.
            ('std::println(); std::cout << "2\\n";', "std::println", "std::cout"),
            # Inside a block that is executed once, the order is still the order.
            ('if (ok) { std::println("1"); std::cout << "2\\n"; }', "std::println", "std::cout"),
            # A whole loop of C stdio before a ``std::cout`` write.
            (
                'for (int i = 1; i <= 2; ++i) { std::println("{}", i); }\nstd::cout << "3\\n";',
                "std::println",
                "std::cout",
            ),
        ],
    )
    def test_is_a_problem(self, body, stdio, stream):
        assert find_output_order_problem(body) == OutputOrderProblem(stdio, stream, "order")


class TestBothChannelsInALoop:
    """The second iteration's ``std::cout`` output precedes the first's C stdio."""

    @pytest.mark.parametrize(
        "body",
        [
            'for (int i = 1; i <= 2; ++i) { std::cout << i << "\\n"; std::println("{}", i); }',
            'for (int x : v) { SHOW(x); std::println("{}", x); }',
            'while (n-- > 0) { std::cout << n; std::printf("%d\\n", n); }',
            'do { std::cout << 1; std::print("x"); } while (false);',
            # A loop without braces.
            'for (int x : v) std::cout << x << "\\n", std::println("{}", x);',
            # Not measured: the loop sits in a block, which changes nothing about it.
            'if (ok) { for (int x : v) { std::cout << x; std::println("{}", x); } }',
        ],
    )
    def test_is_a_problem(self, body):
        problem = find_output_order_problem(body)
        assert problem is not None and problem.reason == "loop"


class TestBothChannelsInAFunction:
    """Reordered as soon as the function is called twice in one cell."""

    @pytest.mark.parametrize(
        "body",
        [
            'void both(int i) { std::cout << i << "\\n"; std::println("{}", i); }',
            'auto both = [](int i) { std::cout << i << "\\n"; std::println("{}", i); };',
            'struct S { void show(int i) const { std::cout << i; std::println("{}", i); } };',
            # Not measured as written (see the module docstring of the detector
            # for how a block is recognized): the same bodies, other heads.
            'auto both = [&] { std::cout << 1; std::println("x"); };',
            'auto f() -> void { std::cout << 1; std::println("x"); }',
            'void f() noexcept { std::cout << 1; std::println("x"); }',
            'template <typename T>\nvoid dump(const T& t) { SHOW(t); std::println("{}", t); }',
        ],
    )
    def test_is_a_problem(self, body):
        problem = find_output_order_problem(body)
        assert problem is not None and problem.reason == "function"


class TestDisplayedInProgramOrder:
    """Shapes the notebook displays correctly, or that mix nothing."""

    @pytest.mark.parametrize(
        "body",
        [
            # All ``std::cout`` output first, then C stdio: displayed as written.
            'std::cout << "1\\n"; std::println("2");',
            'SHOW(x); std::println("2");',
            'std::cout << "1\\n"; std::printf("2\\n");',
            'if (ok) { std::cout << "1\\n"; std::println("2"); }',
            'for (int i = 1; i <= 2; ++i) { std::cout << i << "\\n"; }\nstd::println("3");',
            'std::cout << "0\\n";\nfor (int i = 1; i <= 2; ++i) { std::println("{}", i); }',
            # Written through ``std::cout``.
            'std::cout << "1\\n"; std::println(std::cout, "2"); std::cout << "3\\n";',
            'std::print(std::cout, "1"); std::cout << "2\\n";',
            # One channel only.
            'std::println("1"); std::println("2");',
            'std::cout << "1\\n"; SHOW(x);',
            # Another stream: stderr is not interleaved with stdout anyway.
            'std::println(stderr, "1"); std::cout << "2\\n";',
            'std::println("1"); std::cerr << "2\\n";',
            # Two functions with one channel each mix nothing by sharing a cell.
            'void a() { std::println("a"); }\nvoid b() { std::cout << "b\\n"; }',
        ],
    )
    def test_is_silent(self, body):
        assert find_output_order_problem(body) is None

    @pytest.mark.parametrize(
        "body",
        [
            # Detection, not kernel behaviour: text that only looks like a write.
            '// std::println("1");\nstd::cout << "2\\n";',
            '/* std::println("1"); */ std::cout << "2\\n";',
            'const char* s{"std::println(1)"}; std::cout << s;',
            '#define P(x) std::println("{}", x)\nstd::cout << 1;',
            '#define P(x) \\\n    std::println("{}", x)\nstd::cout << 1;',
            # Not ``std::print``: another library's, a member, a deck's own function.
            'fmt::println("1"); std::cout << "2\\n";',
            'logger.println("1"); std::cout << "2\\n";',
            'print("1"); std::cout << "2\\n";',
            'println("1"); std::cout << "2\\n";',
            # Not stdout.
            'std::fprintf(stderr, "1"); std::cout << "2\\n";',
            'std::snprintf(buffer, 8, "1"); std::cout << "2\\n";',
            # Not ``std::cout``.
            'std::println("1"); out.cout << "2\\n";',
            'std::println("1"); ns::cout << "2\\n";',
            'std::println("1"); obj.SHOW(x);',
            # A stream, a ``FILE*`` or a format held in a variable: unknown, so silent.
            'std::println(log_file, "1"); std::cout << "2\\n";',
            'std::println(fmt, 1); std::cout << "2\\n";',
        ],
    )
    def test_text_that_is_not_a_write_is_silent(self, body):
        assert find_output_order_problem(body) is None


class TestUnfinishedCode:
    """A cell that is mid-edit must not break validation, nor hide a clear case."""

    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            # A loop header that is never closed: no loop body to speak of.
            ('for (int x : v std::cout << x; std::println("{}", x);', None),
            # A single-statement loop body that runs into the end of the cell ...
            ('for (int x : v) std::cout << x, std::println("{}", x)', "loop"),
            # ... or into the brace that closes the surrounding block.
            ('if (ok) { for (int x : v) std::cout << x, std::println("{}", x) }', "loop"),
            # Unbalanced braces around a clear case.
            ('} std::println("1"); std::cout << "2\\n";', "order"),
            ('{ std::println("1"); std::cout << "2\\n";', "order"),
            ("", None),
        ],
    )
    def test_does_not_raise(self, body, reason):
        problem = find_output_order_problem(body)
        assert (problem.reason if problem else None) == reason


class TestValidateFinding:
    def test_warns_and_says_how_to_fix(self, tmp_path):
        path = _deck_with_cell(tmp_path, 'std::println("1");\nstd::cout << "2\\n";')

        findings = _order_findings(path)

        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity == "warning"
        assert finding.category == "code_export"
        assert finding.line == 1
        assert finding.message == (
            "`std::println` is written before `std::cout`, but the notebook displays it after"
        )
        assert "std::println(std::cout, ...)" in finding.suggestion
        assert "split the cell" in finding.suggestion

    def test_loop_and_function_messages_name_both_writers(self, tmp_path):
        loop = _deck_with_cell(tmp_path, 'for (int x : v) { SHOW(x); std::println("{}", x); }')
        assert [f.message for f in _order_findings(loop)] == [
            "`SHOW` and `std::println` alternate in a loop, but the notebook displays "
            "all `SHOW` output first"
        ]
        function = _deck_with_cell(tmp_path, 'void both() { std::cout << 1; std::printf("2"); }')
        assert [f.message for f in _order_findings(function)] == [
            "`std::cout` and `printf` are mixed in one function, whose output the notebook reorders"
        ]

    def test_one_finding_per_cell(self, tmp_path):
        body = 'std::println("1"); std::cout << "2\\n"; std::println("3"); std::cout << "4\\n";'
        assert len(_order_findings(_deck_with_cell(tmp_path, body))) == 1

    def test_each_cell_is_judged_on_its_own(self, tmp_path):
        path = tmp_path / "slides_test.cpp"
        path.write_text(
            '// %%\nstd::println("1");\n\n// %%\nstd::cout << "2\\n";\n', encoding="utf-8"
        )
        assert _order_findings(path) == []

    @pytest.mark.parametrize("tag", ["start", "del"])
    def test_cell_that_is_never_executed_is_silent(self, tmp_path, tag):
        path = _deck_with_cell(
            tmp_path, 'std::println("1"); std::cout << "2\\n";', f'// %% tags=["{tag}"]'
        )
        assert _order_findings(path) == []

    @pytest.mark.parametrize("tag", ["completed", "keep", "alt"])
    def test_executed_cell_kinds_are_checked(self, tmp_path, tag):
        path = _deck_with_cell(
            tmp_path, 'std::println("1"); std::cout << "2\\n";', f'// %% tags=["{tag}"]'
        )
        assert len(_order_findings(path)) == 1

    def test_other_languages_are_not_checked(self, tmp_path):
        path = tmp_path / "slides_test.py"
        path.write_text('# %%\nstd::println("1"); std::cout << "2"\n', encoding="utf-8")
        assert _order_findings(path) == []

    def test_crlf_deck(self, tmp_path):
        path = tmp_path / "slides_test.cpp"
        path.write_bytes(b'// %%\r\nstd::println("1");\r\nstd::cout << "2\\n";\r\n')
        assert len(_order_findings(path)) == 1


class TestOutputTheNotebookNeverDisplays:
    """C ``stderr`` and ``std::clog`` (#1228).

    Measured in the same image, each writer in a cell of its own followed by
    a ``std::cout`` write: the ``std::cout`` line was displayed and the other
    was not — not in that cell, and not with the next one. ``std::cerr`` was
    displayed, as stderr output, also through ``std::println(std::cerr, ...)``.
    The C functions were measured under ``xcpp20`` too.
    """

    @pytest.mark.parametrize(
        ("body", "writer"),
        [
            ('std::println(stderr, "x");', "std::println(stderr, ...)"),
            ('std::print(stderr, "x\\n");', "std::print(stderr, ...)"),
            ('std::fprintf(stderr, "x\\n");', "fprintf(stderr, ...)"),
            ('fprintf(stderr, "%d\\n", n);', "fprintf(stderr, ...)"),
            ('std::fputs("x\\n", stderr);', "fputs(..., stderr)"),
            # Flushing does not bring it back.
            ('std::fprintf(stderr, "x\\n"); std::fflush(stderr);', "fprintf(stderr, ...)"),
            ('std::perror("x");', "perror"),
            ('std::clog << "x\\n";', "std::clog"),
            ('std::clog << "x" << std::endl;', "std::clog"),
            # ``using std::clog;`` decks.
            ('clog << "x\\n";', "std::clog"),
            # Not measured: the same writers in a function, where the call loses them.
            ('void warn() { std::println(stderr, "x"); }', "std::println(stderr, ...)"),
            # The first one names the finding.
            ('std::clog << "a"; std::perror("b");', "std::clog"),
        ],
    )
    def test_is_reported(self, body, writer):
        assert find_undisplayed_write(body) == writer

    @pytest.mark.parametrize(
        "body",
        [
            # Displayed.
            'std::cerr << "x\\n";',
            'std::println(std::cerr, "x");',
            'std::println("x");',
            'std::cout << "x\\n";',
            'std::fprintf(stdout, "x\\n");',
            # ``stderr`` named without being written to.
            "std::fflush(stderr);",
            "std::setbuf(stderr, nullptr);",
            # Detection: text that only looks like a write.
            '// std::println(stderr, "x");',
            'const char* s{"std::clog << 1"}; std::cout << s;',
            '#define WARN(x) std::fprintf(stderr, "%s", x)',
            'std::fputs("stderr", stdout);',
            # Another stream, another library's or a deck's own writer.
            'std::fprintf(log_file, "x\\n");',
            'std::println(log_file, "x");',
            "logger.clog << 1;",
            'my::perror("x");',
            "",
        ],
    )
    def test_is_silent(self, body):
        assert find_undisplayed_write(body) is None

    def test_validate_warns_and_says_how_to_fix(self, tmp_path):
        path = _deck_with_cell(tmp_path, 'std::println(stderr, "x");')
        result = validate_file(path, checks=["code_export"])
        findings = [f for f in result.findings if "not displayed" in f.message]

        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity == "warning"
        assert finding.category == "code_export"
        assert finding.line == 1
        assert finding.message == (
            "`std::println(stderr, ...)` output is not displayed in the notebook"
        )
        assert "std::println(std::cerr, ...)" in finding.suggestion

    def test_one_finding_per_cell(self, tmp_path):
        path = _deck_with_cell(tmp_path, 'std::clog << "a"; std::perror("b"); std::clog << "c";')
        result = validate_file(path, checks=["code_export"])
        assert len([f for f in result.findings if "not displayed" in f.message]) == 1

    @pytest.mark.parametrize("tag", ["start", "del"])
    def test_cell_that_is_never_executed_is_silent(self, tmp_path, tag):
        path = _deck_with_cell(tmp_path, 'std::println(stderr, "x");', f'// %% tags=["{tag}"]')
        result = validate_file(path, checks=["code_export"])
        assert [f for f in result.findings if "not displayed" in f.message] == []
