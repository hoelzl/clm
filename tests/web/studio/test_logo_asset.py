"""The bundled logo as a same-origin asset, and the pre-sanitize rewrite (#706).

The point of the issue is deletion: the ``data:`` confinement rule was the
tier-2 sanitizer's most complex hand-rolled piece and the site of the first
bypass the #704 adversarial rounds found. These tests pin what replaced it:

* the rewrite recognizes the **bundled** logo (whitespace-normalized equality
  against the packaged ``*.base64`` include — nothing else qualifies),
* the asset route serves the **packaged** logo file (a fixed mapping, never a
  request-derived path) and does so without the token, because an ``<img>``
  fetch cannot carry one,
* and the packaged ``*.base64`` resources actually decode to the packaged
  image files — otherwise the rewrite and the route disagree about what the
  logo is, silently.
"""

from __future__ import annotations

import base64
from importlib import resources
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clm.web.studio.logo import _LOGOS, logo_file, rewrite_bundled_logo
from tests.web.studio.conftest import Course, make_app

TOKEN = "test-token-706"


def _packaged_base64(prog_lang: str) -> str:
    base64_name = _LOGOS[prog_lang][0]
    return (
        resources.files("clm.workers.notebook")
        .joinpath(f"templates_{prog_lang}/{base64_name}")
        .read_text(encoding="ascii")
    )


def _logo_data_uri(prog_lang: str) -> str:
    """The URI as the macro emits it: verbatim (line-wrapped) include output."""
    media_type = _LOGOS[prog_lang][2]
    return f"data:{media_type};base64,{_packaged_base64(prog_lang)}"


class TestRewriteBundledLogo:
    @pytest.mark.parametrize("prog_lang", sorted(_LOGOS))
    def test_the_bundled_logo_becomes_an_asset_url(self, prog_lang: str) -> None:
        html = f'<div><img src="{_logo_data_uri(prog_lang)}" width="24"></div>'
        rewritten = rewrite_bundled_logo(html, prog_lang)
        assert f'<img src="/api/studio/asset/logo/{prog_lang}"' in rewritten
        assert "data:" not in rewritten

    def test_wrapping_spelling_does_not_matter(self) -> None:
        """Recognition normalizes whitespace, so re-wrapped base64 still matches."""
        compact = "".join(_packaged_base64("python").split())
        html = f'<img src="data:image/svg+xml;base64,{compact}">'
        assert "/api/studio/asset/logo/python" in rewrite_bundled_logo(html, "python")

    def test_a_foreign_data_uri_is_left_for_the_sanitizer(self) -> None:
        """Not byte-equal to the bundled logo → untouched here, refused there."""
        html = '<img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=">'
        assert rewrite_bundled_logo(html, "python") == html

    def test_the_cpp_logo_is_not_confused_with_the_python_one(self) -> None:
        """Recognition is per-language: the png payload is not the svg one."""
        html = f'<img src="{_logo_data_uri("cpp")}">'
        assert rewrite_bundled_logo(html, "python") == html

    def test_an_unknown_language_rewrites_nothing(self) -> None:
        html = f'<img src="{_logo_data_uri("python")}">'
        assert rewrite_bundled_logo(html, "rust") == html

    def test_the_rewrite_is_idempotent(self) -> None:
        """The client re-renders cells; a second pass must be a no-op."""
        html = f'<img src="{_logo_data_uri("python")}">'
        once = rewrite_bundled_logo(html, "python")
        assert rewrite_bundled_logo(once, "python") == once

    def test_other_tags_with_data_urls_are_not_touched(self) -> None:
        """Only ``<img src>`` is rewritten — a link never becomes an asset URL."""
        html = f'<a href="{_logo_data_uri("python")}">x</a>'
        assert rewrite_bundled_logo(html, "python") == html


class TestPackagedLogoConsistency:
    @pytest.mark.parametrize("prog_lang", sorted(_LOGOS))
    def test_the_base64_include_decodes_to_the_served_file(self, prog_lang: str) -> None:
        """If these drift apart the preview shows a different logo than the build."""
        found = logo_file(prog_lang)
        assert found is not None
        path, _media_type = found
        decoded = base64.b64decode("".join(_packaged_base64(prog_lang).split()))
        assert decoded == path.read_bytes()

    def test_an_unknown_language_has_no_logo(self) -> None:
        assert logo_file("rust") is None


class TestLogoAssetRoute:
    @pytest.fixture()
    def client(self, course: Course) -> TestClient:
        app = make_app(course.spec_path, course.slides_dir.parent / "jobs.db", TOKEN)
        return TestClient(app)

    @pytest.mark.parametrize("prog_lang", sorted(_LOGOS))
    def test_the_logo_is_served_without_a_token(self, client: TestClient, prog_lang: str) -> None:
        """An ``<img>`` fetch cannot carry ``Authorization`` — by design."""
        r = client.get(f"/api/studio/asset/logo/{prog_lang}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith(_LOGOS[prog_lang][2])
        found = logo_file(prog_lang)
        assert found is not None
        assert r.content == found[0].read_bytes()

    def test_the_response_disables_script_for_document_loads(self, client: TestClient) -> None:
        """An SVG opened as a *document* gets a self-contained lockdown CSP.

        The global middleware lets a route's CSP *replace* the app-wide one,
        so this must carry ``default-src 'none'`` itself — ``script-src
        'none'`` alone would silently drop every other directive.
        """
        r = client.get("/api/studio/asset/logo/python")
        assert r.headers["content-security-policy"] == (
            "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'"
        )

    def test_an_unknown_language_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/studio/asset/logo/rust").status_code == 404

    def test_a_path_traversal_spelling_cannot_escape(self, client: TestClient) -> None:
        """``prog_lang`` selects a mapping entry; it is never a path fragment."""
        assert client.get("/api/studio/asset/logo/..%2F..%2Fpyproject").status_code in (
            404,
            422,
        )


class TestRewritePerformance:
    """The regex runs on request-controlled bytes, so it must be linear.

    The first version matched ``src="`` in the same pattern as ``<img``, and
    every ``<img`` start rescanned the rest of the input: ``"<img a" * N``
    cost O(N²) — 34 s at N=20000, an authenticated threadpool DoS found by
    the #709 review round. The ``str.find`` loop rewinds nothing — an
    unterminated ``<img`` ends the scan in one pass; the threshold is generous
    so slow CI can't flake, while the old shape would exceed it by an order
    of magnitude.
    """

    def test_many_img_starts_do_not_go_quadratic(self) -> None:
        import time

        payload = "<img a " * 20000  # no complete tag, the old quadratic case
        start = time.monotonic()
        assert rewrite_bundled_logo(payload, "python") == payload
        assert time.monotonic() - start < 5.0

    def test_many_complete_img_tags_are_linear_too(self) -> None:
        import time

        payload = '<img src="data:image/png;base64,AAAA">' * 20000
        start = time.monotonic()
        assert rewrite_bundled_logo(payload, "python") == payload
        assert time.monotonic() - start < 5.0
