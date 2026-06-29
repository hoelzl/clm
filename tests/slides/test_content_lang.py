"""Unit tests for the lightweight DE/EN content-language detector (content_lang)."""

from __future__ import annotations

from clm.slides.content_lang import detect


def test_clear_german_prose_is_confident_de():
    g = detect("Dies ist ein deutscher Absatz mit vielen Woertern und Umlauten wie schoen und gut")
    assert g.label == "de"
    assert g.confident


def test_clear_english_prose_is_confident_en():
    g = detect("This is an english paragraph with many words and it is clearly english here")
    assert g.label == "en"
    assert g.confident


def test_umlaut_is_a_strong_german_marker():
    g = detect("Wir möchten die Schlüssel über die Brücke führen und prüfen")
    assert g.label == "de"
    assert g.confident


def test_title_only_text_abstains():
    # The exact case the classifier must not guess on (drives the duplicate-id row).
    assert detect("Introduction").label == "unknown"
    assert detect("Array Limitations").label == "unknown"


def test_code_heavy_or_neutral_text_abstains():
    assert detect("x = foo(bar) + baz(qux)").label == "unknown"


def test_unknown_is_not_confident():
    assert not detect("Introduction").confident
