"""Unit tests for :mod:`clm.slides.sync_recover` — bounded LLM alignment recovery.

The validation gate is the load-bearing safety net (Issue #190 §10): an
LLM-derived map is only applied if it is total over the current cells, draws ids
only from the base set, is injective on those ids, and pins every provably
unchanged cell to its old id. Every failure must safe-abort. These tests pin that
contract with no network (the :class:`StaticAlignmentRecoverer`).
"""

from __future__ import annotations

import pytest

from clm.slides.sync_recover import (
    NEW,
    NONE,
    AlignmentInvalid,
    OpenRouterAlignmentRecoverer,
    RecoveryError,
    RegionCell,
    StaticAlignmentRecoverer,
    build_recovery_user_prompt,
    decode_mapping,
    encode_mapping,
    region_fingerprint,
    validate_alignment,
)


def _cell(slide_id: str | None, construct: str | None, content_hash: str) -> RegionCell:
    return RegionCell(slide_id=slide_id, construct=construct, content_hash=content_hash)


# ---------------------------------------------------------------------------
# region_fingerprint
# ---------------------------------------------------------------------------


class TestRegionFingerprint:
    def test_stable_for_equal_regions(self):
        a = [_cell("x", "function-f", "h1"), _cell(None, "import-os", "h2")]
        b = [_cell("x", "function-f", "h1"), _cell(None, "import-os", "h2")]
        assert region_fingerprint(a) == region_fingerprint(b)

    def test_order_sensitive(self):
        a = [_cell("x", "function-f", "h1"), _cell(None, "import-os", "h2")]
        b = [_cell(None, "import-os", "h2"), _cell("x", "function-f", "h1")]
        assert region_fingerprint(a) != region_fingerprint(b)

    def test_body_sensitive_via_hash(self):
        a = [_cell("x", "function-f", "h1")]
        b = [_cell("x", "function-f", "h2")]  # only the content hash changed
        assert region_fingerprint(a) != region_fingerprint(b)

    def test_id_sensitive(self):
        a = [_cell("x", "function-f", "h1")]
        b = [_cell("y", "function-f", "h1")]
        assert region_fingerprint(a) != region_fingerprint(b)

    def test_empty_region(self):
        assert region_fingerprint([]) == region_fingerprint([])


# ---------------------------------------------------------------------------
# encode/decode mapping
# ---------------------------------------------------------------------------


class TestMappingCodec:
    def test_round_trip(self):
        mapping = {0: "def-my-fun", 1: NEW, 2: NONE}
        assert decode_mapping(encode_mapping(mapping)) == mapping

    def test_encode_sorts_keys(self):
        assert encode_mapping({2: "c", 0: "a", 1: "b"}) == '{"0":"a","1":"b","2":"c"}'

    def test_decode_rejects_non_object(self):
        with pytest.raises(AlignmentInvalid):
            decode_mapping("[1, 2, 3]")

    def test_decode_rejects_bad_json(self):
        with pytest.raises(AlignmentInvalid):
            decode_mapping("{not json")

    def test_decode_rejects_non_integer_key(self):
        with pytest.raises(AlignmentInvalid):
            decode_mapping('{"abc": "x"}')

    def test_decode_rejects_non_string_value(self):
        with pytest.raises(AlignmentInvalid):
            decode_mapping('{"0": 5}')


# ---------------------------------------------------------------------------
# validate_alignment — the safety net
# ---------------------------------------------------------------------------


class TestValidateAlignment:
    def test_canonical_rename_split_is_valid(self):
        # The §10 lead example: base def-my-fun (function-my-fun) split; current[0]
        # is a new import, current[1] is the renamed def. Both bodies changed, so
        # nothing is pinned and the model is free to align by construct similarity.
        base = [_cell("def-my-fun", "function-my-fun", "h0")]
        current = [
            _cell("def-my-fun", "import-time", "h1"),  # id left on the import
            _cell(None, "function-my-function", "h2"),  # the renamed def, id-less
        ]
        mapping = {0: NEW, 1: "def-my-fun"}
        assert validate_alignment(mapping, base, current) == mapping

    def test_missing_index_aborts(self):
        base = [_cell("x", "function-f", "h0")]
        current = [_cell("x", "function-f", "h0"), _cell(None, "g", "h1")]
        with pytest.raises(AlignmentInvalid, match="cover current indices"):
            validate_alignment({0: "x"}, base, current)

    def test_extra_index_aborts(self):
        base = [_cell("x", "function-f", "h0")]
        current = [_cell("x", "function-f", "h0")]
        with pytest.raises(AlignmentInvalid, match="cover current indices"):
            validate_alignment({0: "x", 1: NONE}, base, current)

    def test_unknown_base_id_aborts(self):
        base = [_cell("x", "function-f", "h0")]
        current = [_cell(None, "function-f", "h0")]
        with pytest.raises(AlignmentInvalid, match="unknown base id"):
            validate_alignment({0: "invented"}, base, current)

    def test_duplicate_base_id_aborts(self):
        base = [_cell("x", "function-f", "h0"), _cell("y", "function-g", "h1")]
        current = [_cell(None, "function-f", "h2"), _cell(None, "function-g", "h3")]
        with pytest.raises(AlignmentInvalid, match="multiple current cells"):
            validate_alignment({0: "x", 1: "x"}, base, current)

    def test_unchanged_cell_must_keep_its_id(self):
        # current[0] is byte-identical (h0) to base id x → it MUST map to x.
        base = [_cell("x", "function-f", "h0"), _cell("y", "function-g", "h1")]
        current = [_cell("x", "function-f", "h0"), _cell(None, "function-g2", "h2")]
        with pytest.raises(AlignmentInvalid, match="byte-identical"):
            validate_alignment({0: "y", 1: NONE}, base, current)

    def test_pinned_assignment_passes(self):
        base = [_cell("x", "function-f", "h0")]
        current = [_cell("x", "function-f", "h0")]
        assert validate_alignment({0: "x"}, base, current) == {0: "x"}

    def test_unchanged_idless_cell_must_stay_idless(self):
        # current[0] is byte-identical (h0) to an unchanged *id-less* base cell, so
        # it must map to NONE — the model may not mint a spurious id onto unchanged
        # content (that would re-introduce the churn the design avoids).
        base = [_cell(None, "function-f", "h0"), _cell("x", "function-g", "h1")]
        current = [_cell(None, "function-f", "h0")]
        with pytest.raises(AlignmentInvalid, match="pinned to 'none'"):
            validate_alignment({0: "x"}, base, current)

    def test_unchanged_idless_cell_mapped_none_passes(self):
        base = [_cell(None, "function-f", "h0")]
        current = [_cell(None, "function-f", "h0")]
        assert validate_alignment({0: NONE}, base, current) == {0: NONE}

    def test_new_on_constructless_cell_aborts(self):
        base = [_cell("x", "function-f", "h0")]
        current = [_cell(None, None, "h1")]  # unnameable (no construct)
        with pytest.raises(AlignmentInvalid, match="no construct"):
            validate_alignment({0: NEW}, base, current)

    def test_none_on_constructless_cell_is_fine(self):
        base = [_cell("x", "function-f", "h0")]
        current = [_cell(None, None, "h1")]
        assert validate_alignment({0: NONE}, base, current) == {0: NONE}

    def test_ambiguous_hash_is_not_pinned(self):
        # Two base cells share a hash (one id'd, one id-less): the hash cannot
        # pin, so the model is free — no AlignmentInvalid for a non-pinning hash.
        base = [_cell("x", "function-f", "hsame"), _cell(None, "function-f", "hsame")]
        current = [_cell(None, "function-f", "hsame")]
        # Mapping to NONE is allowed because hsame does not unambiguously pin x.
        assert validate_alignment({0: NONE}, base, current) == {0: NONE}

    def test_empty_current_region_is_trivially_valid(self):
        assert validate_alignment({}, [_cell("x", "f", "h0")], []) == {}


# ---------------------------------------------------------------------------
# StaticAlignmentRecoverer
# ---------------------------------------------------------------------------


class TestStaticRecoverer:
    def test_returns_mapping_copy(self):
        rec = StaticAlignmentRecoverer(mapping={0: "x"})
        out = rec.recover(base_region=[], current_region=[])
        assert out == {0: "x"}
        out[0] = "mutated"
        assert rec.mapping == {0: "x"}  # internal mapping untouched

    def test_counts_calls(self):
        rec = StaticAlignmentRecoverer(mapping={0: NONE})
        rec.recover(base_region=[], current_region=[])
        rec.recover(base_region=[], current_region=[])
        assert rec.calls == 2

    def test_raise_error_mode(self):
        rec = StaticAlignmentRecoverer(raise_error=True)
        with pytest.raises(RecoveryError):
            rec.recover(base_region=[], current_region=[])


# ---------------------------------------------------------------------------
# Prompt building — must be body-free
# ---------------------------------------------------------------------------


class TestPromptBuilding:
    def test_prompt_has_both_regions_and_no_source(self):
        base = [_cell("def-my-fun", "function-my-fun", "h0")]
        current = [_cell(None, "function-my-function", "h2")]
        prompt = build_recovery_user_prompt(base, current)
        assert "BASE:" in prompt and "CURRENT:" in prompt
        # The anchor components are present...
        assert "function-my-fun" in prompt
        assert "h0" in prompt and "h2" in prompt
        # ...but never any cell source (only hashes stand in for bodies).
        assert "def " not in prompt
        assert "print(" not in prompt

    def test_openrouter_recoverer_has_prompt_version(self):
        # The default model/version are wired for the cache key.
        rec = OpenRouterAlignmentRecoverer()
        assert rec.prompt_version
        assert rec.model
