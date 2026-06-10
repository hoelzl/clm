"""Tests for :mod:`clm.slides.code_cell_extract`."""

from __future__ import annotations

from clm.slides.code_cell_extract import extract_from_code
from clm.slides.headingless import Category


class TestPrecedence:
    def test_class_def_wins(self):
        source = (
            "import requests\n"
            "x = 5\n"
            "class HistoryChatbot(BaseChatbot):\n"
            "    pass\n"
            "def helper():\n"
            "    pass\n"
        )
        e = extract_from_code(source)
        assert e is not None
        assert e.category == Category.EXTRACTABLE
        assert e.source == "code:class"
        assert e.text == "class HistoryChatbot"

    def test_function_def_beats_assignment_and_import(self):
        source = "import requests\nx = 5\n\ndef process_text(s):\n    return s\n"
        e = extract_from_code(source)
        assert e is not None
        assert e.source == "code:def"
        assert e.text == "function process_text"

    def test_async_function_def(self):
        source = "async def fetch(url):\n    return None\n"
        e = extract_from_code(source)
        assert e is not None
        assert e.source == "code:def"
        assert e.text == "function fetch"

    def test_assignment_beats_import(self):
        source = "import requests\n\nresponse = client.chat.completions.create()\n"
        e = extract_from_code(source)
        assert e is not None
        assert e.source == "code:assign"
        assert e.text == "response"

    def test_import_beats_call(self):
        source = "import requests\n\nclient.method()\n"
        e = extract_from_code(source)
        assert e is not None
        assert e.source == "code:import"
        assert "requests" in e.text

    def test_call_when_nothing_else(self):
        source = "response.choices[0].message.content\n"
        # Bare attribute access — not a Call expression — falls through
        # by default. The opt-in ``display_exprs`` extractor (#233) names
        # it; see TestDisplayExprExtractors.
        e = extract_from_code(source)
        assert e is None


class TestImportExtractor:
    def test_single_import(self):
        e = extract_from_code("import requests\n")
        assert e is not None
        assert e.source == "code:import"
        assert e.text == "import requests"

    def test_multiple_imports(self):
        e = extract_from_code("import requests\nimport trafilatura\nimport ftfy\n")
        assert e is not None
        assert e.text == "import requests trafilatura ftfy"

    def test_comma_separated_import(self):
        e = extract_from_code("import requests, trafilatura, ftfy\n")
        assert e is not None
        assert e.text == "import requests trafilatura ftfy"

    def test_from_import(self):
        e = extract_from_code("from cleantext import clean\n")
        assert e is not None
        assert e.text == "import clean"

    def test_mixed_import_styles(self):
        e = extract_from_code(
            "import requests\nimport trafilatura\nimport ftfy\nfrom cleantext import clean\n"
        )
        assert e is not None
        assert e.text == "import requests trafilatura ftfy clean"

    def test_import_with_asname(self):
        e = extract_from_code("import numpy as np\n")
        assert e is not None
        assert e.text == "import np"

    def test_dotted_import_uses_root(self):
        e = extract_from_code("import openai.types\n")
        assert e is not None
        assert e.text == "import openai"

    def test_import_name_cap(self):
        # The composite title caps at 4 names to keep the report readable;
        # the underlying slugify still enforces a character cap on top of
        # that.
        e = extract_from_code("import a\nimport b\nimport c\nimport d\nimport e\nimport f\n")
        assert e is not None
        assert e.text == "import a b c d"


class TestAssignmentExtractor:
    def test_simple_assignment(self):
        e = extract_from_code("response = client.chat.completions.create()\n")
        assert e is not None
        assert e.source == "code:assign"
        assert e.text == "response"

    def test_annotated_assignment(self):
        e = extract_from_code("count: int = 5\n")
        assert e is not None
        assert e.source == "code:assign"
        assert e.text == "count"

    def test_tuple_unpacking(self):
        e = extract_from_code("a, b = (1, 2)\n")
        assert e is not None
        assert e.source == "code:assign"
        assert "a" in e.text
        assert "b" in e.text

    def test_attribute_target_falls_through(self):
        # `obj.attr = ...` doesn't produce a clean slug; we expect the
        # extractor to fall through to the next strategy (import here).
        e = extract_from_code("import os\nobj.attr = 5\n")
        assert e is not None
        assert e.source == "code:import"


class TestCallExtractor:
    def test_bare_function_call(self):
        e = extract_from_code("setup_environment()\n")
        assert e is not None
        assert e.source == "code:call"
        assert e.text == "setup_environment"

    def test_method_call(self):
        e = extract_from_code("client.chat()\n")
        assert e is not None
        assert e.source == "code:call"
        assert e.text == "client chat"

    def test_chained_attribute_call(self):
        e = extract_from_code("client.chat.completions.create()\n")
        assert e is not None
        assert e.source == "code:call"
        assert e.text == "client chat completions create"


class TestUnparsable:
    def test_syntax_error_returns_none(self):
        e = extract_from_code("!pip install foo\n")
        assert e is None

    def test_empty_returns_none(self):
        assert extract_from_code("") is None

    def test_whitespace_only_returns_none(self):
        assert extract_from_code("   \n   \n") is None

    def test_comments_only_returns_none(self):
        # `ast.parse` accepts comments-only input — produces a module
        # with no statements, so no extractor fires.
        assert extract_from_code("# A comment\n# Another comment\n") is None

    def test_magic_in_comment_returns_none(self):
        assert extract_from_code("# !pip install foo\n") is None


class TestFirstCodeLineFallback:
    """The opt-in ``accept_code_derived`` first-code-line fallback (#251).

    For bare-expression code cells the five intent-based extractors can't
    name — there is no salient construct — the fallback slugs the first real
    code line. It is comment-token-aware so it works for non-Python decks
    too, where ``ast.parse`` always fails. Off by default.
    """

    def test_off_by_default_returns_none(self):
        # Back-compat: without the flag a bare expression is still None, so
        # the content-anchor in sync_writeback and the four content-derived
        # funnels are byte-for-byte unchanged.
        assert extract_from_code("(1 + 1j) * (1 + 1j)") is None

    def test_binop_expression(self):
        e = extract_from_code("(1 + 1j) * (1 + 1j)", accept_code_derived=True)
        assert e is not None
        assert e.category == Category.EXTRACTABLE
        assert e.source == "code:line"
        assert e.text == "(1 + 1j) * (1 + 1j)"

    def test_string_concatenation(self):
        e = extract_from_code('"1" + "2"', accept_code_derived=True)
        assert e is not None and e.source == "code:line" and e.text == '"1" + "2"'

    def test_subscript_expression(self):
        e = extract_from_code("letters[0:3]", accept_code_derived=True)
        assert e is not None and e.text == "letters[0:3]"

    def test_attribute_expression(self):
        e = extract_from_code("response.choices[0].message.content", accept_code_derived=True)
        assert e is not None and e.text == "response.choices[0].message.content"

    def test_comparison_expression(self):
        e = extract_from_code("a == b", accept_code_derived=True)
        assert e is not None and e.text == "a == b"

    def test_big_int_literal(self):
        e = extract_from_code(
            "10000000000000000000000000000000000000000000000000 + 1", accept_code_derived=True
        )
        assert e is not None and e.source == "code:line"

    def test_list_literal(self):
        e = extract_from_code("[1, 2, 3]", accept_code_derived=True)
        assert e is not None and e.text == "[1, 2, 3]"

    def test_ast_construct_still_wins_over_fallback(self):
        # A parseable construct keeps its intent-based label even with the
        # flag on — the fallback is the last extractor.
        e = extract_from_code("x = 5\n", accept_code_derived=True)
        assert e is not None and e.source == "code:assign" and e.text == "x"

    def test_skips_leading_comment(self):
        e = extract_from_code("#setup\n(1 + 1j) * (1 + 1j)\n", accept_code_derived=True)
        assert e is not None and e.text == "(1 + 1j) * (1 + 1j)"

    def test_comment_only_returns_none(self):
        assert extract_from_code("# just a comment", "#", accept_code_derived=True) is None

    def test_magic_only_returns_none(self):
        # The pre-existing "unparsable/magic stays a refusal" contract holds:
        # the magic line is skipped and nothing else remains.
        assert extract_from_code("!pip install requests\n", accept_code_derived=True) is None

    def test_punctuation_only_returns_none(self):
        # No alphanumeric -> no usable slug -> genuinely content-less -> stays
        # a refusal even with the flag on.
        assert extract_from_code("...", accept_code_derived=True) is None
        assert extract_from_code("()", accept_code_derived=True) is None
        assert extract_from_code("_", accept_code_derived=True) is None

    # -- non-Python: ast.parse always fails; the comment-token-aware scanner
    #    is the path that completes a .cs / .cpp / .java / .ts deck. --

    def test_non_python_first_line(self):
        e = extract_from_code("var z = (1 + 2) * (3 + 4);", "//", accept_code_derived=True)
        assert e is not None and e.source == "code:line"
        assert e.text == "var z = (1 + 2) * (3 + 4);"

    def test_non_python_skips_line_comment_no_space(self):
        e = extract_from_code("//note\nDoThing(x);", "//", accept_code_derived=True)
        assert e is not None and e.text == "DoThing(x);"

    def test_non_python_skips_inline_block_comment(self):
        e = extract_from_code("/* helper */\nDoThing(x);", "//", accept_code_derived=True)
        assert e is not None and e.text == "DoThing(x);"

    def test_non_python_skips_multiline_block_comment(self):
        e = extract_from_code("/*\n multi\n line\n*/\nDoThing(x);", "//", accept_code_derived=True)
        assert e is not None and e.text == "DoThing(x);"

    def test_non_python_comment_only_returns_none(self):
        assert extract_from_code("// just a comment", "//", accept_code_derived=True) is None
        assert extract_from_code("/* block only */", "//", accept_code_derived=True) is None


class TestDisplayExprExtractors:
    """The opt-in ``display_exprs`` extractors for display-style cells (#233).

    Off by default so the sync content-anchor (`construct_of`) keeps
    deriving exactly the anchors recorded in existing watermark baselines;
    ``assign-ids`` opts in.
    """

    def test_off_by_default(self):
        assert extract_from_code("data[:5]\n") is None
        assert extract_from_code("for x in items:\n    print(x)\n") is None

    def test_subscript_slice(self):
        e = extract_from_code("data[:5]\n", display_exprs=True)
        assert e is not None
        assert e.category == Category.EXTRACTABLE
        assert e.source == "code:expr"
        assert e.text == "data"

    def test_subscript_string_key(self):
        e = extract_from_code('result["choices"]\n', display_exprs=True)
        assert e is not None
        assert e.source == "code:expr"
        assert e.text == "result choices"

    def test_attribute_plus_subscript_key(self):
        e = extract_from_code('response.headers["Content-Type"]\n', display_exprs=True)
        assert e is not None
        assert e.text == "response headers Content-Type"

    def test_numeric_index_dropped(self):
        e = extract_from_code("items[0]\n", display_exprs=True)
        assert e is not None
        assert e.text == "items"

    def test_attribute_chain_with_numeric_index(self):
        e = extract_from_code("response.choices[0].message.content\n", display_exprs=True)
        assert e is not None
        assert e.text == "response choices message content"

    def test_bare_name(self):
        e = extract_from_code("ice_cream_x\n", display_exprs=True)
        assert e is not None
        assert e.text == "ice_cream_x"

    def test_for_loop(self):
        source = "for student in classroom:\n    print(evaluate_student(student))\n"
        e = extract_from_code(source, display_exprs=True)
        assert e is not None
        assert e.source == "code:for"
        assert e.text == "for student in classroom"

    def test_for_loop_over_call(self):
        e = extract_from_code("for i in range(10):\n    pass\n", display_exprs=True)
        assert e is not None
        assert e.text == "for i in range"

    def test_for_loop_tuple_target(self):
        e = extract_from_code("for k, v in mapping.items():\n    pass\n", display_exprs=True)
        assert e is not None
        assert e.source == "code:for"
        assert e.text.startswith("for k v in mapping")

    def test_call_still_wins_over_expr(self):
        e = extract_from_code("print(data[:5])\n", display_exprs=True)
        assert e is not None
        assert e.source == "code:call"

    def test_arithmetic_still_refuses(self):
        assert extract_from_code("(1 + 1j) * (1 + 1j)\n", display_exprs=True) is None

    def test_comparison_still_refuses(self):
        assert extract_from_code("a == b\n", display_exprs=True) is None

    def test_falls_back_to_code_line_when_both_flags(self):
        e = extract_from_code("a == b\n", display_exprs=True, accept_code_derived=True)
        assert e is not None
        assert e.source == "code:line"
