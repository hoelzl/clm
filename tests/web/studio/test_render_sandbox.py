"""The tier-2 render is sandboxed (S7 of the 2026-07-24 adversarial review).

``render_j2_cell`` renders a **request body** — ``POST /api/studio/deck/render-cell``
hands it whatever the client sent. On a plain ``jinja2.Environment`` a template
can read ``__class__`` and walk out of the template namespace from there, which
the review reproduced against this exact function. It now uses
``SandboxedEnvironment``.

**What these tests assert, and why not the obvious thing.** They do not assert
"the render fails". The environment uses a lenient ``Undefined``, so a refused
attribute becomes undefined and renders as the empty string — the escape is
blocked but the render often still reports ``ok``. Asserting on the failure
mode would therefore pin an incidental detail and, worse, would still pass if a
future change rendered the value *and then* errored. What matters is that the
traversal never produces its value, so each case pairs a payload with the
string its success would put in the output, and asserts that string is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.web.studio.render import render_j2_cell

DECK = "slides_x.de.py"

#: ``(source, marker)`` — ``marker`` is what a *successful* escape puts in the
#: output. Each is a distinct route out of the template namespace, and no
#: marker occurs in its own source, so a tier-1 fallback (body echoed back
#: unchanged) cannot accidentally satisfy the assertion.
ESCAPE_ATTEMPTS = [
    pytest.param('{{ "".__class__.__name__ }}', "str", id="str-class"),
    pytest.param("{{ ().__class__.__base__.__name__ }}", "object", id="tuple-base"),
    pytest.param("{{ ().__class__.__base__.__subclasses__() }}", "<class", id="subclasses"),
    pytest.param('{{ ""|attr("__class__")|attr("__name__") }}', "str", id="attr-filter"),
    pytest.param('{{ "".__getattribute__("__class__").__name__ }}', "str", id="getattribute"),
    pytest.param("{% set c = joiner.__class__ %}{{ c.__mro__ }}", "object", id="set-then-read"),
    pytest.param("{{ cycler.__init__.__globals__ }}", "builtins", id="jinja-global"),
    pytest.param("{{ namespace.__init__.__globals__ }}", "builtins", id="namespace-global"),
    pytest.param("{{ self.__init__.__globals__ }}", "builtins", id="self-global"),
]


class TestSandboxBlocksTraversal:
    @pytest.mark.parametrize(("source", "marker"), ESCAPE_ATTEMPTS)
    def test_escape_yields_nothing(self, tmp_path: Path, source: str, marker: str):
        _ok, _error, text = render_j2_cell(tmp_path / DECK, source, "de")

        assert marker not in text, f"sandbox leaked {marker!r} for: {source}"

    def test_the_markers_would_otherwise_appear(self, tmp_path: Path):
        """Prove the assertions above have teeth.

        Renders the same payloads on a plain ``Environment`` — the pre-fix
        configuration — and requires that at least most of them *do* leak their
        marker. Without this, a typo in a payload would turn every case above
        into a test that passes because nothing rendered at all.
        """
        from jinja2 import Environment

        env = Environment(autoescape=False)  # noqa: S701 - deliberately the unsafe one
        leaked = []
        for param in ESCAPE_ATTEMPTS:
            source, marker = param.values
            try:
                if marker in env.from_string(source).render():
                    leaked.append(source)
            except Exception:  # noqa: BLE001, S110 - a payload that errors is not a leak
                pass

        assert len(leaked) >= 5, f"only {len(leaked)} payloads leak unsandboxed: {leaked}"

    def test_a_chained_refusal_reports_a_usable_error(self, tmp_path: Path):
        """When it does error, the message has to name the problem."""
        ok, error, _ = render_j2_cell(tmp_path / DECK, '{{ "".__class__.__name__ }}', "de")
        assert ok is False
        assert "__class__" in str(error)
        assert "unsafe" in str(error)


class TestLegitimateRenderingSurvives:
    """The sandbox must not cost the feature it protects."""

    def test_arithmetic_and_filters_still_work(self, tmp_path: Path):
        ok, error, text = render_j2_cell(
            tmp_path / DECK, "{{ (1 + 2) * 3 }} {{ 'x' | upper }}", "de"
        )
        assert ok and error is None
        assert "9 X" in text

    def test_injected_globals_are_readable(self, tmp_path: Path):
        ok, _, text = render_j2_cell(tmp_path / DECK, "{{ lang }}|{{ author }}", "de")
        assert ok
        assert "de|Preview" in text

    # The bundled macros are the whole point of tier 2, and there is one set per
    # shipped language. A sandbox refusal inside a macro would break previews
    # for that language only — exactly the kind of gap a single-language test
    # misses.
    @pytest.mark.parametrize(
        ("suffix", "prefix"),
        [
            ("de.py", "#"),
            ("de.cpp", "//"),
            ("de.cs", "//"),
            ("de.java", "//"),
            ("de.ts", "//"),
        ],
    )
    def test_every_shipped_language_macro_set_renders(
        self, tmp_path: Path, suffix: str, prefix: str
    ):
        source = (
            f"{prefix} j2 from 'macros.j2' import header_de\n"
            f'{prefix} {{{{ header_de("Hallo Welt") }}}}'
        )
        ok, error, text = render_j2_cell(tmp_path / f"slides_x.{suffix}", source, "de")

        assert ok, f"{suffix} macros do not render under the sandbox: {error}"
        assert "Hallo Welt" in text
        assert "{{" not in text
