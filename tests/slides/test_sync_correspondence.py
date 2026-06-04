"""Unit tests for the cold-start CorrespondenceVerifier tier (#216 Phase 3).

The verifier confirms that the two structurally-aligned halves of a never-id'd
split pair actually correspond (are translations) before `clm slides sync` mints a
shared slide_id onto each pair. These tests pin the body-of-the-tier — fingerprint,
(de)serialization, validation, the static stand-in, and the cache — in isolation;
the engine wiring is exercised in the apply/CLI suites.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncCorrespondenceCache
from clm.slides.sync_recover import (
    CorrespondenceError,
    CorrespondenceInvalid,
    SlidePair,
    StaticCorrespondenceVerifier,
    correspondence_fingerprint,
    decode_verdicts,
    encode_verdicts,
    validate_correspondence,
)


def _pair(de_h: str, en_h: str, role: str = "slide") -> SlidePair:
    return SlidePair(de_heading=de_h, en_heading=en_h, de_snippet="", en_snippet="", role=role)


class TestFingerprint:
    def test_stable_for_equal_pairs(self):
        a = [_pair("# ## Variablen", "# ## Variables"), _pair("# ## Schleifen", "# ## Loops")]
        b = [_pair("# ## Variablen", "# ## Variables"), _pair("# ## Schleifen", "# ## Loops")]
        assert correspondence_fingerprint(a) == correspondence_fingerprint(b)

    def test_sensitive_to_heading_change(self):
        a = [_pair("# ## Variablen", "# ## Variables")]
        b = [_pair("# ## Variablen", "# ## Functions")]
        assert correspondence_fingerprint(a) != correspondence_fingerprint(b)

    def test_sensitive_to_order(self):
        a = [_pair("# ## A", "# ## A"), _pair("# ## B", "# ## B")]
        b = [_pair("# ## B", "# ## B"), _pair("# ## A", "# ## A")]
        assert correspondence_fingerprint(a) != correspondence_fingerprint(b)


class TestVerdictCodec:
    def test_round_trip(self):
        verdicts = {0: True, 1: False, 2: True}
        assert decode_verdicts(encode_verdicts(verdicts)) == verdicts

    def test_encode_sorts_keys(self):
        assert encode_verdicts({2: True, 0: False}) == '{"0":false,"2":true}'

    def test_decode_rejects_non_json(self):
        with pytest.raises(CorrespondenceInvalid):
            decode_verdicts("not json")

    def test_decode_rejects_non_object(self):
        with pytest.raises(CorrespondenceInvalid):
            decode_verdicts("[true, false]")

    def test_decode_rejects_non_bool_value(self):
        # An id-like string where a boolean is required must not slip through.
        with pytest.raises(CorrespondenceInvalid):
            decode_verdicts('{"0": "yes"}')

    def test_decode_rejects_non_int_key(self):
        with pytest.raises(CorrespondenceInvalid):
            decode_verdicts('{"a": true}')


class TestValidate:
    def test_total_map_passes(self):
        pairs = [_pair("a", "a"), _pair("b", "b")]
        verdicts = {0: True, 1: False}
        assert validate_correspondence(verdicts, pairs) == verdicts

    def test_missing_index_raises(self):
        pairs = [_pair("a", "a"), _pair("b", "b")]
        with pytest.raises(CorrespondenceInvalid):
            validate_correspondence({0: True}, pairs)

    def test_stray_index_raises(self):
        pairs = [_pair("a", "a")]
        with pytest.raises(CorrespondenceInvalid):
            validate_correspondence({0: True, 1: True}, pairs)


class TestStaticVerifier:
    def test_default_true_for_all(self):
        v = StaticCorrespondenceVerifier()
        pairs = [_pair("a", "a"), _pair("b", "b")]
        assert v.verify(pairs=pairs) == {0: True, 1: True}
        assert v.calls == 1

    def test_explicit_verdicts_override_default(self):
        v = StaticCorrespondenceVerifier(verdicts={1: False}, default=True)
        pairs = [_pair("a", "a"), _pair("b", "b"), _pair("c", "c")]
        assert v.verify(pairs=pairs) == {0: True, 1: False, 2: True}

    def test_raise_error_path(self):
        v = StaticCorrespondenceVerifier(raise_error=True)
        with pytest.raises(CorrespondenceError):
            v.verify(pairs=[_pair("a", "a")])


class TestCache:
    def test_put_get_round_trip(self, tmp_path: Path):
        cache = SyncCorrespondenceCache(tmp_path / "clm-llm.sqlite")
        try:
            cache.put("fp1", "correspond-v1", '{"0":true}')
            assert cache.get("fp1", "correspond-v1") == '{"0":true}'
        finally:
            cache.close()

    def test_miss_returns_none(self, tmp_path: Path):
        cache = SyncCorrespondenceCache(tmp_path / "clm-llm.sqlite")
        try:
            assert cache.get("absent", "correspond-v1") is None
            # A different prompt version is a miss even for the same fingerprint.
            cache.put("fp1", "correspond-v1", '{"0":true}')
            assert cache.get("fp1", "correspond-v2") is None
        finally:
            cache.close()
