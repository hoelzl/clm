"""Tests for the bilingual + sibling ``header`` macros in the //-comment
template families (C++/C#/Java/TypeScript).

These templates use the same header-line-less convention as
``templates_python``: a deck's title is a standalone ``// {{ header(...) }}``
j2 call with no authored ``// %%`` wrapper, and the macro itself emits the
leading ``%% [markdown] lang="de"`` boundary. This is the property the
multi-language authoring migration depends on
(docs/claude/multi-language-authoring-tooling-investigation.md §10).
"""

from __future__ import annotations

import pytest
from jinja2 import Environment, PackageLoader, StrictUndefined

# (prog_lang, template dir) — all of these use the "// j2" line-statement prefix.
CLIKE = [
    pytest.param("templates_cpp", id="cpp"),
    pytest.param("templates_csharp", id="csharp"),
    pytest.param("templates_java", id="java"),
    pytest.param("templates_typescript", id="typescript"),
]


def _render(template_dir: str, template_source: str) -> str:
    env = Environment(
        loader=PackageLoader("clm.workers.notebook", template_dir),
        autoescape=False,
        undefined=StrictUndefined,
        line_statement_prefix="// j2 ",
        keep_trailing_newline=True,
    )
    template = env.from_string(
        template_source,
        globals={
            "is_notebook": True,
            "is_html": False,
            "author": "Test Author",
            "organization": "",
        },
    )
    return template.render()


@pytest.mark.parametrize("template_dir", CLIKE)
class TestClikeHeaderMacros:
    def test_bilingual_header_emits_both_language_boundaries(self, template_dir: str) -> None:
        # No authored "// %%" wrapper: the macro must supply both boundaries.
        rendered = _render(
            template_dir,
            "// j2 from 'macros.j2' import header\n// {{ header('Titel', 'Title') }}\n",
        )
        assert "Titel" in rendered
        assert "Title" in rendered
        assert '// %% [markdown] lang="de" tags=["slide"]' in rendered
        assert '// %% [markdown] lang="en" tags=["slide"]' in rendered

    def test_header_de_emits_only_de(self, template_dir: str) -> None:
        rendered = _render(
            template_dir,
            "// j2 from 'macros.j2' import header_de\n// {{ header_de('Titel') }}\n",
        )
        assert '// %% [markdown] lang="de" tags=["slide"]' in rendered
        assert 'lang="en"' not in rendered
        assert "Titel" in rendered

    def test_header_en_emits_only_en(self, template_dir: str) -> None:
        rendered = _render(
            template_dir,
            "// j2 from 'macros.j2' import header_en\n// {{ header_en('Title') }}\n",
        )
        assert '// %% [markdown] lang="en" tags=["slide"]' in rendered
        assert 'lang="de"' not in rendered
        assert "Title" in rendered
