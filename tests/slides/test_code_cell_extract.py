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
        # Bare attribute access — not a Call expression — falls through.
        # That's an ast.Expr wrapping ast.Attribute, not ast.Call.
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
