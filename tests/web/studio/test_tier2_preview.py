"""The tier-2 in-page preview and its server-side sanitizer (issue #697).

Two failures met here, and they are different in kind:

* **The tier never ran.** Its client gate was ``cell.cell_type === "markdown"``
  while the API types a Jinja cell as ``"j2"``, so the branch was unreachable
  from the day it was written and nothing noticed — nothing asserted the
  frontend path at all. So the gate is now a named function,
  ``needsServerRender``, executed here under node *and* checked against the
  cell payload the real service emits. Either half alone would have missed the
  original bug: the predicate can be right about a contract that changed, and
  the contract can be right with nobody consulting it.
* **The consumer was a raw ``innerHTML`` sink.** The expansion is deliberately
  HTML (the header macros emit ``<div>``, ``<br>`` and a ``data:`` logo), so
  escaping it deletes the feature. Decision D13: sanitize **server-side**
  against an allowlist. These tests are the allowlist's teeth — including the
  case that made ``data:`` awkward, since the logo needs it and a link must not
  have it.
"""

from __future__ import annotations

import builtins
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clm.web.studio.render import _to_preview_html, render_j2_cell_html
from clm.web.studio.sanitize import (
    SanitizerUnavailableError,
    sanitize_preview_html,
)

from .conftest import Course, make_app

TOKEN = "test-studio-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

J2_HEADER = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Hallo Welt\") }}"

APP_JS = (
    Path(__file__).resolve().parents[3] / "src" / "clm" / "web" / "static" / "studio" / "app.js"
)


# ---------------------------------------------------------------------------
# The sanitizer allowlist.
# ---------------------------------------------------------------------------


class TestSanitizer:
    @pytest.mark.parametrize(
        "html",
        [
            "<script>PAYLOAD</script>",
            "<iframe src='https://evil'>PAYLOAD</iframe>",
            "<style>PAYLOAD</style>",
            "<form action='https://evil'>PAYLOAD<input name=a></form>",
            "<object data='x'>PAYLOAD</object>",
            "<noscript>PAYLOAD</noscript>",
            "<template>PAYLOAD</template>",
        ],
    )
    def test_active_content_is_removed_with_its_text(self, html: str) -> None:
        """Not just unwrapped — the text goes too.

        An adversarial review found the original version of this test vacuous:
        nh3's ``clean_content_tags`` defaults to ``{script, style}``, so
        ``<iframe>fallback</iframe>`` left ``fallback`` behind as prose. Harmless
        in itself, but fallback text is exactly where a payload's "your session
        expired, tap here" copy lives, and the test's name claimed otherwise.
        ``PAYLOAD`` is the marker so the assertion cannot pass by accident.
        """
        assert sanitize_preview_html(html) == ""

    @pytest.mark.parametrize(
        "html",
        [
            "<svg onload=alert(1)><circle/></svg>",
            "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
            "<xmp><p></xmp><img src=x onerror=alert(1)>",
            "<select><noembed></select><img src=x onerror=alert(1)>",
        ],
    )
    def test_namespace_confusion_payloads_are_inert(self, html: str) -> None:
        cleaned = sanitize_preview_html(html)
        assert "alert" not in cleaned
        assert "<svg" not in cleaned and "onerror" not in cleaned

    @pytest.mark.parametrize(
        "html",
        [
            "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
            '<div style="text-align:center"><b>T</b></div>',
            '<img src="data:image/png;base64,AAAA">',
            # Non-idempotence is the *signature* of a filter that rewrites a
            # value: pass 1 emits something pass 2 refuses. The `&#127;`
            # javascript bypass showed up here first, so the corpus now carries
            # the shapes that would expose the next one.
            '<a href="&#127;javascript:alert(1)">x</a>',
            '<a href="&#1;data:text/html,x">x</a>',
            '<a href="&#127;//evil.example/x">x</a>',
            '<img src="&#8;data:image/svg+xml;base64,AAaa">',
            '<a href="&#9;https://ok.example">x</a>',
        ],
    )
    def test_sanitizing_is_idempotent(self, html: str) -> None:
        """A second pass must neither resurrect markup nor refuse pass 1's output."""
        once = sanitize_preview_html(html)
        assert sanitize_preview_html(once) == once

    def test_event_handlers_are_dropped(self) -> None:
        cleaned = sanitize_preview_html('<img src="https://x/y.png" onerror="alert(1)">')
        assert "onerror" not in cleaned
        assert 'src="https://x/y.png"' in cleaned

    def test_the_header_macros_markup_survives(self) -> None:
        """If this fails the feature is gone, not merely degraded."""
        cleaned = sanitize_preview_html(
            '<div style="text-align:center; font-size:200%;">\n'
            " <b>Titel</b>\n"
            "</div>\n"
            "<br/>\n"
            '<div style="text-align:center; font-size:120%;">Preview</div>'
        )
        assert "<b>Titel</b>" in cleaned
        assert "text-align:center" in cleaned
        assert "<br>" in cleaned

    def test_the_data_uri_logo_survives(self) -> None:
        cleaned = sanitize_preview_html(
            '<img src="data:image/svg+xml;base64,PHN2Zy8+" style="display:block;width:5%">'
        )
        assert 'src="data:image/svg+xml;base64,PHN2Zy8+"' in cleaned

    def test_a_line_wrapped_data_uri_survives(self) -> None:
        """The bundled ``*.base64`` includes are wrapped, so the URI has newlines."""
        cleaned = sanitize_preview_html('<img src="data:image/svg+xml;base64,PHN2\nZy8+\nAA">')
        assert "data:image/svg+xml;base64," in cleaned

    @pytest.mark.parametrize(
        "html",
        [
            '<a href="data:text/html,<script>alert(1)</script>">x</a>',
            '<a href="DATA:text/html,x">x</a>',
            '<a href="da\nta:text/html,x">x</a>',
            '<a href="javascript:alert(1)">x</a>',
            '<a href="vbscript:msgbox(1)">x</a>',
            # The C0-control prefixes an adversarial review used to bypass this:
            # ammonia and the URL parser strip every C0 control, Python's `\s`
            # does not, so `&#1;data:` looked like the scheme "\x01data" to the
            # filter and like `data:` to nh3. `&#N;` needs no raw control byte in
            # the deck — the HTML parser decodes it. See _URL_INSIGNIFICANT.
            '<a href="&#1;data:text/html;base64,PHNjcmlwdD4=">x</a>',
            '<a href="&#8;data:text/html,x">x</a>',
            '<a href="&#14;data:text/html,x">x</a>',
            '<a href="&#27;javascript:alert(1)">x</a>',
            # &#127; (DEL) is the case the *first* round of fixes got wrong: it is
            # in this module's strip set but NOT in ammonia's, so a filter that
            # rewrote kept values turned an inert relative path into a live
            # `javascript:` URL that nothing re-checked. See _attribute_filter.
            '<a href="&#127;javascript:alert(1)">x</a>',
            '<a href="&#x7f;javascript:alert(1)">x</a>',
            '<a href="&#127;&#1; javascript:alert(1)">x</a>',
            '<a href="&#127;vbscript:msgbox(1)">x</a>',
            '<a href="&#127;data:text/html,x">x</a>',
        ],
    )
    def test_a_link_can_never_carry_data_or_script_schemes(self, html: str) -> None:
        """``data:`` is allowed for the logo, so a link must be refused explicitly."""
        cleaned = sanitize_preview_html(html)
        assert "href" not in cleaned

    @pytest.mark.parametrize(
        "prefix",
        ["", "&#1;", "&#8;", "&#9;", "&#14;", "&#27;", "&#127;", "&#x7f;", " ", "&#127;&#1;"],
    )
    @pytest.mark.parametrize(
        "scheme",
        [
            "javascript:alert(1)",
            "jAvAsCrIpT:alert(1)",
            "vbscript:msgbox(1)",
            "about:blank",
            "blob:https://x/y",
            "filesystem:https://x/y",
            "data:text/html,x",
        ],
    )
    def test_no_prefix_can_smuggle_an_unlisted_scheme_out(self, prefix: str, scheme: str) -> None:
        """The property that replaced two rounds of case-by-case patching.

        nh3 checks its scheme allowlist against the *incoming* value and writes
        whatever the filter returns, and the two normalizations do not agree in
        either direction (``&#127;`` is stripped here and not by ammonia;
        ``<tab>blob:`` reads as a relative path to ammonia and as a scheme here).
        So the filter applies the allowlist itself, over its own normalization,
        and only ever rejects. This cross-product is that invariant: no padding,
        in any spelling, may cause an unlisted scheme to reach the client.
        """
        for markup in ('<a href="{}">x</a>', '<img src="{}">'):
            cleaned = sanitize_preview_html(markup.format(prefix + scheme)).lower()
            for unlisted in ("javascript", "vbscript", "about:", "blob:", "filesystem:", "data:"):
                assert unlisted not in cleaned, f"{unlisted!r} survived in {cleaned!r}"

    @pytest.mark.parametrize(
        "html",
        [
            '<img src="data:text/html,<b>x</b>">',
            '<img src="&#1;data:text/html,x">',
            '<img src="&#14;data:application/javascript,x">',
        ],
    )
    def test_a_non_image_data_uri_is_refused_even_on_img(self, html: str) -> None:
        """Cases 2–3 are the control-prefix bypass: the confinement to
        ``data:image/`` has to normalize the same way nh3's scheme check does, or
        the prefix defeats one check and not the other."""
        cleaned = sanitize_preview_html(html)
        assert "data:" not in cleaned

    def test_a_kept_url_is_returned_byte_for_byte(self) -> None:
        """The filter must not "tidy" a value it keeps — that escapes nh3's check.

        This test used to assert the opposite (that a stray control prefix was
        stripped from a kept URL), and satisfying it is what produced the
        ``&#127;javascript:`` bypass: nh3 validates the *incoming* value, so a
        rewritten return value is never re-checked. The prefix is inert — a URL
        parser resolves ``\x7fjavascript:…`` as a same-origin path — so leaving it
        alone is both safer and simpler.
        """
        cleaned = sanitize_preview_html('<img src="&#8;data:image/svg+xml;base64,AAaaBB">')
        assert "\x08data:image/svg+xml;base64,AAaaBB" in cleaned

    @pytest.mark.parametrize(
        "url",
        [
            "//evil.example/x",
            "\\\\evil.example/x",
            "/\\evil.example/x",
            "\\/evil.example/x",
            "&#1;//evil.example/x",
            "/\t/evil.example/x",
        ],
    )
    @pytest.mark.parametrize("markup", ['<a href="{}">x</a>', '<img src="{}">'])
    def test_authority_relative_targets_are_refused(self, markup: str, url: str) -> None:
        """The rule the client's ``safeUrl()`` already applies to tier-1 links.

        Tier-2 output lands in the same page — one holding a non-expiring bearer
        token — so it gets the same rule. WHATWG parsing treats ``\\`` as ``/``
        for http(s), so all four leading pairs reach the same host, and an
        ``<img>`` needs no click at all: opening the deck is the beacon. The
        tier-1 markdown renderer has no image syntax, so this vector arrives
        *with* this feature.
        """
        cleaned = sanitize_preview_html(markup.format(url))
        assert "evil.example" not in cleaned

    @pytest.mark.parametrize(
        "url", ["https://ok.example/x", "/root/relative", "img/logo.png", "#anchor"]
    )
    def test_same_origin_and_absolute_targets_still_work(self, url: str) -> None:
        cleaned = sanitize_preview_html(f'<a href="{url}">x</a>')
        assert url in cleaned

    def test_class_is_not_allowed(self) -> None:
        """Because the page's own stylesheet is a CSS-allowlist bypass.

        ``.toast`` is ``position: fixed; z-index: 20`` in index.html, so injected
        markup that merely *names* it gets the fixed overlay
        :data:`ALLOWED_CSS_PROPERTIES` exists to refuse — no ``style`` needed.
        Found by an adversarial review; the macros only ever emit inline styles.
        """
        cleaned = sanitize_preview_html('<div class="toast show">Sitzung abgelaufen</div>')
        assert "class" not in cleaned
        assert "Sitzung abgelaufen" in cleaned  # the text is prose, not a threat

    def test_https_links_still_work(self) -> None:
        cleaned = sanitize_preview_html('<a href="https://ok.example">ok</a>')
        assert 'href="https://ok.example"' in cleaned

    def test_css_is_reduced_to_an_allowlist(self) -> None:
        cleaned = sanitize_preview_html(
            '<div style="text-align:center; position:fixed; '
            'background-image:url(https://evil/x); width:5%">x</div>'
        )
        assert "text-align:center" in cleaned
        assert "width:5%" in cleaned
        assert "position" not in cleaned
        assert "url(" not in cleaned

    def test_a_style_with_nothing_allowlisted_is_dropped_entirely(self) -> None:
        cleaned = sanitize_preview_html('<div style="position:fixed;z-index:9999">x</div>')
        assert "style" not in cleaned

    def test_backslash_escaped_css_is_dropped(self) -> None:
        cleaned = sanitize_preview_html(r'<div style="width:expre\ssion(alert(1))">x</div>')
        assert "expre" not in cleaned

    @pytest.mark.parametrize(
        "style",
        [
            "width:url/*x*/(javascript:alert(1))",
            "width:expression/*x*/(alert(1))",
            "width:/*}*/url(x)",
        ],
    )
    def test_css_comments_cannot_hide_a_rejected_construct(self, style: str) -> None:
        """A comment splits ``url(`` so a naive ``url\\s*\\(`` walks past it.

        No impact on its own — none of the allowlisted properties accepts
        ``url()`` — but the guard has to do what its docstring says, and nothing
        covered it before an adversarial review pointed at it.
        """
        cleaned = sanitize_preview_html(f'<div style="{style}">x</div>')
        assert "url" not in cleaned and "expression" not in cleaned

    def test_fails_closed_without_nh3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An install without ``[web]`` loses the feature, not the guarantee."""
        real_import = builtins.__import__

        def _no_nh3(name, *args, **kwargs):
            if name == "nh3":
                raise ImportError("No module named 'nh3'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_nh3)
        with pytest.raises(SanitizerUnavailableError, match="clm\\[web\\]"):
            sanitize_preview_html("<b>x</b>")


# ---------------------------------------------------------------------------
# Expanded text → injectable HTML.
# ---------------------------------------------------------------------------


class TestPreviewHtmlNormalisation:
    def test_the_cell_delimiter_line_is_dropped(self) -> None:
        text = '# %% [markdown] lang="de" tags=["slide"]\n# <b>Titel</b>'
        assert _to_preview_html(text, "#") == "<b>Titel</b>"

    def test_an_unprefixed_delimiter_is_dropped_too(self) -> None:
        """The macro's own first line carries no comment token."""
        text = '%% [markdown] lang="de"\n// <b>T</b>'
        assert _to_preview_html(text, "//") == "<b>T</b>"

    def test_comment_prefixes_are_stripped_per_line(self) -> None:
        assert _to_preview_html("# <div>\n#  x\n# </div>", "#") == "<div>\n x\n</div>"

    def test_unprefixed_continuation_lines_are_left_alone(self) -> None:
        """A wrapped base64 logo continues without the comment token."""
        text = '# <img src="data:image/svg+xml;base64,AAAA\nBBBB\nCCCC">'
        assert _to_preview_html(text, "#") == (
            '<img src="data:image/svg+xml;base64,AAAA\nBBBB\nCCCC">'
        )

    @pytest.mark.parametrize(
        "line",
        ["# %%not a delimiter, prose", "# 100%% sicher", "# %%%"],
    )
    def test_prose_that_merely_starts_with_percent_percent_survives(self, line: str) -> None:
        """``%%`` needs whitespace or ``[`` after it — jupytext's own rule.

        The first version of the delimiter pattern was ``%%.*``, which silently
        deleted any line beginning that way.
        """
        assert line.split("# ", 1)[1] in _to_preview_html(line, "#")


# ---------------------------------------------------------------------------
# End to end: a real macro expansion, sanitized.
# ---------------------------------------------------------------------------


class TestRenderJ2CellHtml:
    def test_a_real_header_becomes_sanitized_html(self, tmp_path: Path) -> None:
        ok, error, html = render_j2_cell_html(tmp_path / "slides_x.de.py", J2_HEADER, "de")
        assert ok and error is None and html is not None
        assert "Hallo Welt" in html
        assert "{{" not in html  # expanded, not echoed
        assert "%%" not in html  # the cell delimiter is gone
        assert "\n# <" not in html  # and so is the comment prefix
        assert "<div" in html  # the macro's markup survived
        assert "<script" not in html

    def test_a_body_smuggling_script_is_sanitized_not_reflected(self, tmp_path: Path) -> None:
        """The body is a *request* body — a token holder can put anything in it."""
        body = "# <img src=x onerror=alert(1)>\n# <script>alert(2)</script>"
        ok, error, html = render_j2_cell_html(tmp_path / "slides_x.de.py", body, "de")
        assert ok and html is not None
        assert "onerror" not in html and "<script" not in html

    def test_broken_jinja_yields_no_html(self, tmp_path: Path) -> None:
        ok, error, html = render_j2_cell_html(
            tmp_path / "slides_x.de.py", "# {{ not valid jinja ", "de"
        )
        assert ok is False and error and html is None

    def test_sanitizer_failure_yields_no_html(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(_html: str) -> str:
            raise SanitizerUnavailableError("nh3 missing")

        monkeypatch.setattr("clm.web.studio.render.sanitize_preview_html", _boom)
        ok, error, html = render_j2_cell_html(tmp_path / "slides_x.de.py", J2_HEADER, "de")
        assert ok is False and html is None and error == "nh3 missing"

    def test_an_unexpected_sanitizer_error_also_yields_no_html(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``nh3.clean`` itself raising must fail closed, not fall through."""

        def _boom(_html: str) -> str:
            raise ValueError("nh3 config rejected")

        monkeypatch.setattr("clm.web.studio.render.sanitize_preview_html", _boom)
        ok, error, html = render_j2_cell_html(tmp_path / "slides_x.de.py", J2_HEADER, "de")
        assert ok is False and html is None
        assert error is not None and "sanitize failed" in error


# ---------------------------------------------------------------------------
# The endpoint, and the contract the frontend gate depends on.
# ---------------------------------------------------------------------------


class TestEndpointContract:
    @pytest.fixture()
    def client(self, course: Course) -> TestClient:
        app = make_app(course.spec_path, course.slides_dir.parent / "jobs.db", TOKEN)
        return TestClient(app)

    def test_rendered_response_carries_sanitized_html_and_the_original_body(
        self, client: TestClient, course: Course
    ) -> None:
        r = client.post(
            "/api/studio/deck/render-cell",
            headers=AUTH,
            json={"deck_id": course.deck_id, "body": J2_HEADER, "is_j2": True, "lang": "de"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["rendered"] is True
        assert "Hallo Welt" in data["html"]
        # `body` echoes the request so the client can fall back; it is NOT the
        # unsanitized expansion (shipping that would invite injecting it).
        assert data["body"] == J2_HEADER

    def test_a_non_j2_cell_gets_no_html(self, client: TestClient, course: Course) -> None:
        r = client.post(
            "/api/studio/deck/render-cell",
            headers=AUTH,
            json={"deck_id": course.deck_id, "body": "# plain", "is_j2": False},
        )
        assert r.status_code == 200
        assert r.json() == {"rendered": False, "body": "# plain", "html": None, "error": None}

    def test_the_api_marks_a_j2_cell_the_way_the_client_gate_expects(
        self, client: TestClient, course: Course
    ) -> None:
        """The half of the original bug that lived in the *contract*.

        The gate keys on ``is_j2``; this asserts the payload actually sets it,
        and that ``cell_type`` is ``"j2"`` — i.e. the old
        ``cell_type === "markdown"`` gate is provably wrong, not merely
        replaced.
        """
        deck_path = course.slides_dir / course.deck_id
        deck_path.write_text(
            "# j2 from 'macros.j2' import header_de\n"
            '# {{ header_de("Hallo") }}\n'
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro-welcome"\n'
            "# Willkommen\n",
            encoding="utf-8",
        )
        r = client.get("/api/studio/deck", params={"id": course.deck_id}, headers=AUTH)
        assert r.status_code == 200, r.text
        cells = r.json()["cells"]
        j2_cells = [c for c in cells if c["is_j2"]]
        assert j2_cells, f"no is_j2 cell in {[c['cell_type'] for c in cells]}"
        assert j2_cells[0]["cell_type"] == "j2"
        assert all(c["is_j2"] is False for c in cells if c["cell_type"] == "markdown")


# ---------------------------------------------------------------------------
# The frontend gate, executed.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH; the Studio frontend gate cannot be executed",
)
class TestClientGate:
    def _run(self, expr: str) -> object:
        from .test_client_escaping import _extract_function

        source = APP_JS.read_text(encoding="utf-8")
        fn = _extract_function(source, "needsServerRender")
        script = f"{fn}\nprocess.stdout.write(JSON.stringify({expr}));"
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["node", "-e", script],  # noqa: S607 - resolved via PATH, guarded by the skipif
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, f"node failed: {proc.stderr}"
        return json.loads(proc.stdout)

    def test_a_j2_cell_is_sent_to_the_server(self) -> None:
        assert self._run('needsServerRender({cell_type: "j2", is_j2: true})') is True

    def test_a_markdown_cell_is_not(self) -> None:
        assert self._run('needsServerRender({cell_type: "markdown", is_j2: false})') is False

    def test_the_gate_does_not_key_on_cell_type(self) -> None:
        """The original bug, pinned: a j2 cell is not typed ``markdown``."""
        assert self._run('needsServerRender({cell_type: "markdown", is_j2: true})') is True
        assert self._run('needsServerRender({cell_type: "j2", is_j2: false})') is False

    def test_a_missing_cell_is_handled(self) -> None:
        assert self._run("needsServerRender(undefined)") is False
        assert self._run("needsServerRender({})") is False


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH; the Studio consumer cannot be executed",
)
class TestClientConsumer:
    """``renderJ2`` itself — the line whose absence of coverage caused #696.

    An adversarial review made the point that pinning only the *gate* leaves the
    consumer exactly as untested as it was when it silently stopped working:
    rename the server's ``html`` field and every other test here still passes
    while the feature dies. So this executes ``renderJ2`` under node against a
    stub ``api()`` and a stub element, and asserts what it sends, what it
    assigns, and — importantly — when it assigns *nothing*.
    """

    def _run(self, harness: str) -> dict:
        from .test_client_escaping import _extract_function

        source = APP_JS.read_text(encoding="utf-8")
        # The shared extractor keys on `function <name>(`, which drops the
        # `async` keyword in front of it — and then node rejects the `await`
        # inside. Re-attach it rather than loosening the shared helper.
        fn = _extract_function(source, "renderJ2")
        if "async function renderJ2(" in source:
            fn = f"async {fn}"
        assert fn.startswith("async function renderJ2("), fn[:60]
        script = f"""
        let currentDeck = {{ deck_id: "m/t/slides_x.de.py" }};
        const calls = [];
        let apiImpl = async (path, opts) => {{
          calls.push({{ path, body: JSON.parse(opts.body) }});
          return {{ rendered: true, html: "<div>EXPANDED</div>", body: "raw", error: null }};
        }};
        const api = (path, opts) => apiImpl(path, opts);
        function stubEl() {{
          const el = {{ innerHTML: "TIER1", classes: [] }};
          // Record the argument on the *element*: a stub that drops it cannot
          // observe the one side effect this exists to check (an earlier version
          // did exactly that, so deleting the classList.add call passed).
          el.classList = {{ add: (c) => el.classes.push(c) }};
          return el;
        }}
        {fn}
        (async () => {{
          {harness}
        }})().then((out) => process.stdout.write(JSON.stringify(out)))
             .catch((e) => {{ process.stderr.write(String(e)); process.exit(1); }});
        """
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["node", "-e", script],  # noqa: S607 - resolved via PATH, guarded by the skipif
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, f"node failed: {proc.stderr}"
        return json.loads(proc.stdout)

    def test_it_posts_the_cell_to_the_render_endpoint(self) -> None:
        out = self._run(
            """
            const el = stubEl();
            await renderJ2({ body: "# {{ header_de('T') }}", lang: "de", is_j2: true }, el);
            return { calls, html: el.innerHTML };
            """
        )
        assert len(out["calls"]) == 1
        call = out["calls"][0]
        assert call["path"] == "/deck/render-cell"
        assert call["body"]["is_j2"] is True
        assert call["body"]["deck_id"] == "m/t/slides_x.de.py"
        assert call["body"]["lang"] == "de"

    def test_it_injects_the_servers_html_field(self) -> None:
        """The contract, executed: rename ``html`` server-side and this fails."""
        out = self._run(
            """
            const el = stubEl();
            await renderJ2({ body: "x", lang: "de" }, el);
            return { html: el.innerHTML, classes: el.classes };
            """
        )
        assert out["html"] == "<div>EXPANDED</div>"
        # The class carries the phone-legibility CSS (and the max-height that
        # keeps a preview inside its own card), so losing it is a real defect.
        assert out["classes"] == ["j2-preview"]

    def test_an_empty_expansion_keeps_tier_1(self) -> None:
        """``typeof "" === "string"``, so an empty render must be caught by name.

        A cell that is only a ``{% import %}`` expands to nothing; blanking the
        card would be worse than showing the raw source.
        """
        out = self._run(
            """
            apiImpl = async () => ({ rendered: true, html: "" });
            const el = stubEl();
            await renderJ2({ body: "x" }, el);
            return { html: el.innerHTML, classes: el.classes };
            """
        )
        assert out["html"] == "TIER1" and out["classes"] == []

    @pytest.mark.parametrize(
        "reply",
        [
            '{ rendered: false, html: null, body: "x", error: "nope" }',
            '{ rendered: true, html: null, body: "x", error: null }',
            "{}",
        ],
    )
    def test_a_non_render_leaves_tier_1_in_place(self, reply: str) -> None:
        out = self._run(
            f"""
            apiImpl = async () => ({reply});
            const el = stubEl();
            await renderJ2({{ body: "x" }}, el);
            return {{ html: el.innerHTML }};
            """
        )
        assert out["html"] == "TIER1"

    def test_a_failed_request_leaves_tier_1_in_place(self) -> None:
        out = self._run(
            """
            apiImpl = async () => { const e = new Error("401"); e.status = 401; throw e; };
            const el = stubEl();
            await renderJ2({ body: "x" }, el);
            return { html: el.innerHTML };
            """
        )
        assert out["html"] == "TIER1"

    def test_a_reply_arriving_after_navigation_is_dropped(self) -> None:
        """No write into a card belonging to a deck the user already left."""
        out = self._run(
            """
            apiImpl = async () => {
              currentDeck = { deck_id: "m/t/other.de.py" };   // navigated away
              return { rendered: true, html: "<div>LATE</div>" };
            };
            const el = stubEl();
            await renderJ2({ body: "x" }, el);
            return { html: el.innerHTML };
            """
        )
        assert out["html"] == "TIER1"

    def test_no_deck_open_sends_nothing(self) -> None:
        out = self._run(
            """
            currentDeck = null;
            const el = stubEl();
            await renderJ2({ body: "x" }, el);
            return { calls, html: el.innerHTML };
            """
        )
        assert out["calls"] == [] and out["html"] == "TIER1"

    def test_cell_card_wires_the_gate_to_the_consumer(self) -> None:
        """The *wiring*, which is the third thing that can silently rot.

        The gate is executed, the consumer is executed — and #696 was neither of
        those, it was the call between them. Executing ``cellCard`` would mean
        stubbing a DOM (``el()``, ``renderMarkdown``, ``querySelector``…), out of
        proportion here; so this reads the source and requires that the call
        exists, is guarded by the gate, and passes the cell and its body element.
        A static check, deliberately, and named as one.
        """
        from .test_client_escaping import _extract_function

        card = _extract_function(APP_JS.read_text(encoding="utf-8"), "cellCard")
        assert "needsServerRender(cell)" in card
        assert "renderJ2(cell, body)" in card
        gate = card.index("needsServerRender(cell)")
        call = card.index("renderJ2(cell, body)")
        assert gate < call, "the tier-2 call must sit inside the gate, not before it"
