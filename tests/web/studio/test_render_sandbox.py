"""The tier-2 render is sandboxed (S7 of the 2026-07-24 adversarial review).

``render_j2_cell`` renders a **request body** — ``POST /api/studio/deck/render-cell``
hands it whatever the client sent. On a plain ``jinja2.Environment`` a template
can read ``__class__`` and walk out of the template namespace from there, which
the review reproduced against this exact function. It now uses
``ImmutableSandboxedEnvironment``.

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

        Renders the same payloads on a plain ``Environment`` configured the way
        ``render_j2_cell`` configured it before the fix, and requires that
        **every** one leaks its marker. A threshold ("most of them") would
        tolerate exactly the typo this is meant to catch: a payload that
        renders nothing makes its case above pass for the wrong reason.
        """
        from jinja2 import Environment

        from clm.workers.notebook.utils.prog_lang_utils import jinja_prefix_for

        env = Environment(  # noqa: S701 - deliberately the pre-fix unsafe one
            autoescape=False,
            line_statement_prefix=jinja_prefix_for("python"),
            keep_trailing_newline=True,
        )
        globals_ = {
            "is_notebook": False,
            "is_html": True,
            "lang": "de",
            "author": "Preview",
            "organization": "",
        }

        not_leaked = []
        for param in ESCAPE_ATTEMPTS:
            source, marker = param.values
            try:
                if marker not in env.from_string(source, globals=globals_).render():
                    not_leaked.append(source)
            except Exception as exc:  # noqa: BLE001 - an erroring payload proves nothing
                not_leaked.append(f"{source}  ({type(exc).__name__})")

        assert not not_leaked, f"these payloads do not leak even unsandboxed: {not_leaked}"

    def test_a_chained_refusal_reports_a_usable_error(self, tmp_path: Path):
        """When it does error, the message has to name the problem."""
        ok, error, _ = render_j2_cell(tmp_path / DECK, '{{ "".__class__.__name__ }}', "de")
        assert ok is False
        assert "__class__" in str(error)
        assert "unsafe" in str(error)


class TestResourceBounds:
    """Blocking attribute access says nothing about how *big* a render may get.

    ``{{ "A" * 200000000 }}`` allocated 200 MB in ~1.5s on a plain sandbox, and
    the route rendered inline on the event loop — so one POST from any token
    holder stalled every other request, and a loop of them exhausts memory.
    """

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param('{{ "A" * 200000000 }}', id="str-repeat"),
            pytest.param('{{ 200000000 * "A" }}', id="str-repeat-reversed"),
            pytest.param("{{ [1] * 999999999 }}", id="list-repeat"),
        ],
    )
    def test_oversized_repetition_is_refused(self, tmp_path: Path, source: str):
        ok, error, text = render_j2_cell(tmp_path / DECK, source, "de")

        assert ok is False
        assert "refusing to repeat" in str(error)
        # Refused *before* allocating, not truncated after: the body comes back
        # untouched and no giant string was ever built.
        assert text == source

    def test_repetition_within_the_limit_still_works(self, tmp_path: Path):
        from clm.web.studio.render import MAX_REPEAT

        ok, error, text = render_j2_cell(tmp_path / DECK, '{{ "ab" * 10 }}', "de")
        assert ok and error is None
        assert text == "ab" * 10
        assert MAX_REPEAT > 10  # the limit is not so tight it breaks real macros

    def test_output_is_bounded_while_it_accumulates(self, tmp_path: Path, monkeypatch):
        """Growth the repeat cap cannot see: a loop of individually-legal emits.

        The bound has to apply *during* the join, not to the finished string.
        With a post-render ``len()`` check, ``{% for i in range(500) %}{{ "A" *
        100000 }}{% endfor %}`` peaked at 100 MB before anything looked at it —
        every repeat is legal and only the total is not.
        """
        from clm.web.studio import render as render_mod

        monkeypatch.setattr(render_mod, "MAX_OUTPUT_CHARS", 100)
        source = "{% for i in range(200) %}xxxxx{% endfor %}"
        ok, error, text = render_j2_cell(tmp_path / DECK, source, "de")

        assert ok is False
        assert "exceeded" in str(error)
        assert text == source

    def test_concatenation_is_bounded(self, tmp_path: Path, monkeypatch):
        """``+`` on sequences is intercepted before it allocates."""
        from clm.web.studio import render as render_mod

        monkeypatch.setattr(render_mod, "MAX_OUTPUT_CHARS", 1000)
        source = '{% set s = "A" * 900 %}{% set s = s + s %}{{ s|length }}'
        ok, error, _ = render_j2_cell(tmp_path / DECK, source, "de")

        assert ok is False
        assert "refusing to build" in str(error)

    def test_a_large_render_is_not_silently_truncated(self, tmp_path: Path, monkeypatch):
        """Refusing beats returning a partial deck — a truncated preview reads
        as real content."""
        from clm.web.studio import render as render_mod

        monkeypatch.setattr(render_mod, "MAX_OUTPUT_CHARS", 50)
        source = "{% for i in range(100) %}0123456789{% endfor %}"
        ok, _error, text = render_j2_cell(tmp_path / DECK, source, "de")

        assert ok is False
        assert text == source  # the body, not a 50-character prefix

    def test_tilde_concatenation_is_a_known_gap(self, tmp_path: Path):
        """``~`` is deliberately NOT bounded — pinned so it stays a known fact.

        Jinja compiles ``~`` to a ``Concat`` node, which is not a ``BinExpr``
        (so ``intercepted_binops`` cannot see it) and which calls ``str_join``
        resolved from the compiled template's own namespace (so
        ``environment.concat`` cannot either). The only remaining hook is a
        process-wide monkeypatch of ``jinja2.runtime``, which would change how
        the *build* renders every deck.

        A token holder can therefore still exhaust this process, which is
        accepted: the token is the trust boundary (D4) and the same client can
        already rewrite any deck (issue #698). This test documents the limit
        rather than asserting a fix — if a future jinja2 or a deliberate change closes it,
        this fails and the docstrings claiming the gap should be updated.
        """
        source = '{% set s = "A" * 1000 %}{% set s = s ~ s %}{{ s|length }}'
        ok, _error, text = render_j2_cell(tmp_path / DECK, source, "de")

        assert ok is True
        assert text == "2000"


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
