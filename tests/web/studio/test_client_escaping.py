"""The Studio frontend's HTML escaping (S7 of the 2026-07-24 review).

``esc()`` did not escape quotes, and ``inline()`` interpolates its output into
``<a href="…">`` — so a markdown link target could close the attribute and add
its own, reaching ``innerHTML``. Separately, nothing stopped a ``javascript:``
target from becoming a working link.

There is no JS test harness in this repo and adding one (package.json, a
runner, a CI step) is out of proportion to three pure functions. Instead the
functions are lifted out of ``app.js`` by name and executed under ``node``,
which is present on the GitHub runners and on the maintainer's machine. If
node is missing the module skips with that reason stated — a skip is honest,
a test that silently asserts nothing is not.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = (
    Path(__file__).resolve().parents[3] / "src" / "clm" / "web" / "static" / "studio" / "app.js"
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH; the Studio frontend helpers cannot be executed",
)


def _extract_function(source: str, name: str) -> str:
    r"""Return the full text of the top-level ``function <name>(…) {…}``.

    Brace-counted rather than regex-terminated, so a nested function body does
    not truncate the result. The scanner is *not* string- or regex-aware, so a
    brace inside a string literal or a quantifier like ``/\d{1,3}/`` would
    miscount — the assertion below turns that into a loud failure rather than a
    silently truncated extraction, and node would reject the result anyway.
    """
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces while extracting {name}()")


@pytest.fixture(scope="module")
def run_js():
    """Return a callable evaluating ``expr`` against app.js's escaping helpers."""
    source = APP_JS.read_text(encoding="utf-8")
    helpers = "\n".join(_extract_function(source, n) for n in ("esc", "safeUrl", "inline"))

    def _run(expr: str) -> str:
        script = f"{helpers}\nprocess.stdout.write(JSON.stringify({expr}));"
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["node", "-e", script],  # noqa: S607 - resolved via PATH, guarded by the skipif
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, f"node failed: {proc.stderr}"
        return json.loads(proc.stdout)

    return _run


class TestEsc:
    def test_escapes_the_five_html_significant_characters(self, run_js):
        assert run_js("""esc('&<>"\\'')""") == "&amp;&lt;&gt;&quot;&#39;"

    def test_ampersand_is_escaped_first(self, run_js):
        """Otherwise ``&lt;`` from a literal ``<`` gets re-escaped into ``&amp;lt;``."""
        assert run_js("esc('<')") == "&lt;"

    def test_plain_text_is_untouched(self, run_js):
        assert run_js("esc('Woche 01: Einführung')") == "Woche 01: Einführung"


class TestLinkTargetCannotEscapeTheAttribute:
    def test_quote_in_link_target_cannot_add_an_attribute(self, run_js):
        """The S7 chain: `"` closed href and injected an event handler."""
        out = run_js("""inline('[x](a" onerror="alert(1))')""")

        assert 'onerror="' not in out
        assert "&quot;" in out  # the quote survived as an entity, not as syntax

    def test_quote_in_link_text_cannot_add_an_attribute(self, run_js):
        out = run_js("""inline('[a" onmouseover="alert(1)](http://ok.example)')""")

        assert 'onmouseover="' not in out

    def test_angle_brackets_cannot_open_a_tag(self, run_js):
        out = run_js("""inline('[x](http://a.example) <img src=x onerror=alert(1)>')""")

        assert "<img" not in out
        assert "&lt;img" in out


class TestLinkSchemes:
    @pytest.mark.parametrize(
        "target",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "vbscript:msgbox(1)",
        ],
    )
    def test_scripting_schemes_are_neutralised(self, run_js, target: str):
        out = run_js(f"inline('[click]({target})')")

        assert 'href="#"' in out, out

    def test_control_characters_cannot_smuggle_a_scheme(self, run_js):
        """Browsers strip tabs/newlines when resolving a URL.

        ``java\\tscript:`` therefore executes as ``javascript:`` while defeating
        a naive scheme test, so the characters are stripped *before* the test.
        """
        out = run_js(r"""inline('[click](java\tscript:alert(1))')""")

        assert 'href="#"' in out, out

    @pytest.mark.parametrize(
        "target",
        [
            "http://example.test/a",
            "https://example.test/a?x=1&y=2",
            "mailto:someone@example.test",
            "relative/path.html",
            "#anchor",
        ],
    )
    def test_ordinary_targets_still_work(self, run_js, target: str):
        out = run_js(f"inline('[click]({target})')")

        # Assert the target is preserved verbatim rather than "is not '#'":
        # for the `#anchor` case the latter is satisfied by the target itself,
        # so a regression that returned "#" would still have passed.
        expected = target.replace("&", "&amp;")  # esc() runs before safeUrl()
        assert f'href="{expected}"' in out, out
        assert ">click</a>" in out

    @pytest.mark.parametrize(
        "target",
        [
            "//evil.example",
            "//evil.example/path",
        ],
    )
    def test_protocol_relative_targets_are_neutralised(self, run_js, target: str):
        """No scheme in the string, but the browser supplies the page's.

        That makes it a live off-origin navigation from a deck's link target,
        on a page whose localStorage holds a bearer token that never expires.
        """
        out = run_js(f"inline('[click]({target})')")

        assert 'href="#"' in out, out

    @pytest.mark.parametrize(
        "target",
        [
            "\u00a0javascript:alert(1)",
            "\u00a0//evil.example",
        ],
    )
    def test_unicode_whitespace_is_not_trimmed_into_a_scheme(self, run_js, target: str):
        """Browsers do not strip U+00A0, so neither may we.

        A JS ``.trim()`` here would remove it and *promote* these into a live
        scheme and a live protocol-relative URL respectively. Left in place,
        both are inert relative paths.
        """
        out = run_js(f'inline("[click]({target})")')

        # The precise property is about the *start* of the href: a browser
        # honours a scheme only at position 0, and it does not strip U+00A0 —
        # so the character surviving is exactly what keeps these inert. A bare
        # "javascript: not in out" would wrongly fail on the harmless
        # " javascript:" that we want to see.
        assert 'href="javascript:' not in out, out
        assert 'href="//evil.example"' not in out, out
        assert " " in out, "the NBSP was trimmed — that is what promotes these"


class TestResolveToken:
    """The pairing half of the fragment change — the part that can break login.

    ``resolveToken`` is the only reader of the new ``#token=`` form. Nothing
    else pins it: the CLI test checks the string the desktop *prints*.
    """

    @pytest.fixture(scope="class")
    def run_resolve(self):
        source = APP_JS.read_text(encoding="utf-8")
        fn = _extract_function(source, "resolveToken")

        def _run(url: str) -> str:
            # Minimal stand-ins for the three browser globals resolveToken uses.
            script = f"""
                const store = {{}};
                globalThis.localStorage = {{
                    getItem: (k) => (k in store ? store[k] : null),
                    setItem: (k, v) => {{ store[k] = String(v); }},
                }};
                globalThis.window = {{
                    location: {{ href: {json.dumps(url)} }},
                    history: {{ replaceState: () => {{}} }},
                }};
                const TOKEN_KEY = "clm_studio_token";
                {fn}
                process.stdout.write(JSON.stringify(resolveToken()));
            """
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["node", "-e", script],  # noqa: S607 - guarded by the module skipif
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert proc.returncode == 0, f"node failed: {proc.stderr}"
            return json.loads(proc.stdout)

        return _run

    def test_reads_the_fragment_form(self, run_resolve):
        assert run_resolve("http://host:8000/studio/#token=abc123") == "abc123"

    def test_still_reads_the_legacy_query_form(self, run_resolve):
        """An old bookmark or printed QR must not strand a user unpaired."""
        assert run_resolve("http://host:8000/studio/?token=abc123") == "abc123"

    def test_base64url_tokens_survive_verbatim(self, run_resolve):
        """``secrets.token_urlsafe`` emits ``-`` and ``_``; ``+`` would decode to a space.

        ``URLSearchParams`` turns ``+`` into a space, so a token containing one
        would be silently corrupted and pairing would fail with no diagnostic.
        base64url never emits ``+`` — this pins that the characters it *does*
        emit come through untouched.
        """
        token = "aB3-_xY9zQ-w_pL2"
        assert run_resolve(f"http://host:8000/studio/#token={token}") == token

    def test_no_token_anywhere_is_empty(self, run_resolve):
        assert run_resolve("http://host:8000/studio/") == ""

    def test_fragment_wins_over_a_stale_query(self, run_resolve):
        assert run_resolve("http://host:8000/studio/?token=old#token=new") == "new"
