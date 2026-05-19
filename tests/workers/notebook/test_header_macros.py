"""Tests for the bilingual + sibling ``header`` macros in templates_python/macros.j2.

The bilingual ``header(de, en)`` macro and the Phase 5 sibling macros
``header_de(de)`` / ``header_en(en)`` all expand into percent-format
slide cell text. These tests verify each macro renders the expected
markdown block — they do not exercise the full notebook pipeline,
just the Jinja side that ``split``/``unify`` depend on for parity
between bilingual and split files.
"""

from __future__ import annotations

import pytest
from jinja2 import Environment, PackageLoader, StrictUndefined


def _render(template_source: str, **globals_: object) -> str:
    """Render ``template_source`` against the python notebook macros.

    Mirrors :class:`clm.workers.notebook.notebook_processor.NotebookProcessor`'s
    Jinja setup: ``line_statement_prefix="# j2 "`` so ``# j2 from ...
    import ...`` lines are processed as Jinja statements (the convention
    used in the slide ``.py`` files).
    """
    env = Environment(
        loader=PackageLoader("clm.workers.notebook", "templates_python"),
        autoescape=False,
        undefined=StrictUndefined,
        line_statement_prefix="# j2 ",
        keep_trailing_newline=True,
    )
    template = env.from_string(template_source, globals={**globals_})
    return template.render()


@pytest.fixture
def common_globals() -> dict[str, object]:
    return {
        "is_notebook": True,
        "is_html": False,
        "author": "Test Author",
        "organization": "",
    }


class TestBilingualHeader:
    def test_emits_both_languages(self, common_globals: dict[str, object]) -> None:
        rendered = _render(
            "# j2 from 'macros.j2' import header\n# {{ header('Titel', 'Title') }}\n",
            **common_globals,
        )
        # Both titles appear in the output.
        assert "Titel" in rendered
        assert "Title" in rendered
        # DE and EN are emitted as distinct percent-format markdown cells.
        assert '# %% [markdown] lang="de" tags=["slide"]' in rendered
        assert '# %% [markdown] lang="en" tags=["slide"]' in rendered


class TestHeaderDe:
    def test_emits_only_de(self, common_globals: dict[str, object]) -> None:
        rendered = _render(
            "# j2 from 'macros.j2' import header_de\n# {{ header_de('Titel') }}\n",
            **common_globals,
        )
        assert "Titel" in rendered
        # Sibling macro must produce only the DE cell — no EN cell at all.
        assert '# %% [markdown] lang="de" tags=["slide"]' in rendered
        assert 'lang="en"' not in rendered


class TestHeaderEn:
    def test_emits_only_en(self, common_globals: dict[str, object]) -> None:
        rendered = _render(
            "# j2 from 'macros.j2' import header_en\n# {{ header_en('Title') }}\n",
            **common_globals,
        )
        assert "Title" in rendered
        assert '# %% [markdown] lang="en" tags=["slide"]' in rendered
        assert 'lang="de"' not in rendered


class TestSiblingMacrosMatchBilingual:
    def test_de_side_of_bilingual_equals_header_de(self, common_globals: dict[str, object]) -> None:
        """``header_de`` must produce the same DE block the bilingual macro emits.

        This is the parity check that lets split-source builds produce
        byte-identical output to bilingual builds: per-language pipelines
        see the same DE / EN cell text either way.
        """
        bilingual = _render(
            "# j2 from 'macros.j2' import header\n# {{ header('Titel', 'Title') }}\n",
            **common_globals,
        )
        de_only = _render(
            "# j2 from 'macros.j2' import header_de\n# {{ header_de('Titel') }}\n",
            **common_globals,
        )
        # The DE block from the bilingual call site ends where the EN cell starts.
        de_split_point = bilingual.find('# %% [markdown] lang="en"')
        assert de_split_point != -1
        bilingual_de_part = bilingual[:de_split_point]
        # ``header_de`` is invoked from ``# {{ header_de(...) }}`` so the rendered
        # output starts at the same column as the bilingual ``# {{ header(...) }}``
        # call site — both files therefore see identical leading text for the
        # DE cell. Strip trailing whitespace before comparing to ignore Jinja's
        # final-newline handling, which is harmless for the build.
        assert bilingual_de_part.rstrip() == de_only.rstrip()

    def test_en_side_of_bilingual_equals_header_en(self, common_globals: dict[str, object]) -> None:
        bilingual = _render(
            "# j2 from 'macros.j2' import header\n# {{ header('Titel', 'Title') }}\n",
            **common_globals,
        )
        en_only = _render(
            "# j2 from 'macros.j2' import header_en\n# {{ header_en('Title') }}\n",
            **common_globals,
        )
        en_split_point = bilingual.find('# %% [markdown] lang="en"')
        assert en_split_point != -1
        bilingual_en_part = bilingual[en_split_point:]
        # The EN cell starts with its own ``# %%`` boundary in the bilingual
        # output. The ``header_en`` rendering starts at the call site's
        # ``# {{ header_en(...) }}`` line, so we compare on cell content
        # (everything from the first ``# %% [markdown] lang="en"`` line
        # downward).
        en_only_start = en_only.find('# %% [markdown] lang="en"')
        assert en_only_start != -1
        assert bilingual_en_part.rstrip() == en_only[en_only_start:].rstrip()
