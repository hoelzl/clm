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
            "<script>alert(1)</script>",
            "<iframe src='https://evil'></iframe>",
            "<svg onload=alert(1)><circle/></svg>",
            "<style>body{display:none}</style>",
            "<form action='https://evil'><input name=a></form>",
            "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
            "<object data='x'></object>",
        ],
    )
    def test_active_content_is_removed_with_its_contents(self, html: str) -> None:
        cleaned = sanitize_preview_html(html)
        assert "alert" not in cleaned
        assert "<script" not in cleaned and "<iframe" not in cleaned
        assert "<svg" not in cleaned and "<style" not in cleaned

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
        ],
    )
    def test_a_link_can_never_carry_data_or_script_schemes(self, html: str) -> None:
        """``data:`` is allowed for the logo, so a link must be refused explicitly."""
        cleaned = sanitize_preview_html(html)
        assert "href" not in cleaned

    def test_a_non_image_data_uri_is_refused_even_on_img(self) -> None:
        cleaned = sanitize_preview_html('<img src="data:text/html,<b>x</b>">')
        assert "data:" not in cleaned

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
