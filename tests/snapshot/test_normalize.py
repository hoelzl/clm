"""Unit tests for ``clm.snapshot.normalize``."""

from __future__ import annotations

from clm.snapshot.normalize import normalize_for_compare, normalize_html


class TestNormalizeHtml:
    def test_default_repr_address_is_redacted(self):
        out = normalize_html(b"<pre>&lt;__main__.Foo at 0x2733c2b8ad0&gt;</pre>")
        assert out == b"<pre>&lt;__main__.Foo at 0xADDR&gt;</pre>"

    def test_multiple_addresses_redacted(self):
        out = normalize_html(b"<a>0x1234abcd</a><b>0xDEADBEEF</b>")
        assert out == b"<a>0xADDR</a><b>0xADDR</b>"

    def test_uppercase_hex_marker_redacted(self):
        out = normalize_html(b"0X1234ABCD")
        assert out == b"0xADDR"

    def test_short_hex_literal_left_alone(self):
        # 0xff and 0xAB are too short to be plausible memory addresses;
        # they more commonly appear as actual constants in slide code.
        assert normalize_html(b"0xff") == b"0xff"
        assert normalize_html(b"0xAB") == b"0xAB"

    def test_non_hex_content_unchanged(self):
        original = b"<html><body><p>plain</p></body></html>"
        assert normalize_html(original) == original


class TestNormalizeForCompare:
    def test_html_normalized_when_include_html(self):
        out = normalize_for_compare(
            "speaker/x/y.html",
            b"<pre>0x2733c2b8ad0</pre>",
            include_html=True,
        )
        assert out == b"<pre>0xADDR</pre>"

    def test_html_not_normalized_by_default(self):
        # include_html=False — caller is responsible for skipping HTML;
        # this function only normalizes when explicitly asked.
        original = b"<pre>0x2733c2b8ad0</pre>"
        assert normalize_for_compare("foo.html", original, include_html=False) == original

    def test_non_html_left_alone_even_when_include_html(self):
        # ipynb has its own determinism layer (PR #76); we don't strip
        # hex addresses from it because they might be meaningful content.
        original = b'"output": "0x2733c2b8ad0"'
        assert normalize_for_compare("foo.ipynb", original, include_html=True) == original
