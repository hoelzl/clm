"""Tests for :mod:`clm.slides.translate_bootstrap` — the file/orchestration layer.

Phase 2 of ``clm slides translate`` (Issue #232). Where Phase 1
(:mod:`clm.slides.translate_deck`) is the pure, offline engine, this layer adds
the side effects: resolving the twin path, the *idempotency dispatch* (twin
absent → bootstrap; twin present → delegate to the incremental sync engine),
EN-authority id minting, and the watermark seal that makes the next ``sync`` a
clean no-op.

The load-bearing properties under test are D2 (re-running converges to sync and
never doubles the deck) and the parity invariants (the written pair round-trips
and carries matching ``slide_id``\\ s). Everything is offline — driven through
the ``SlideTranslator`` / ``SyncJudge`` protocols with the static fakes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.slides.raw_cells import split_cells
from clm.slides.split import split_text, unify_texts
from clm.slides.sync_translate import StaticSlideTranslator
from clm.slides.translate_bootstrap import (
    BootstrapResult,
    TranslateBootstrapError,
    bootstrap_deck,
    derive_bootstrap_paths,
)

# ---------------------------------------------------------------------------
# Fixtures / building blocks (shared with tests/slides/test_translate_deck.py)
# ---------------------------------------------------------------------------

HEADER_PREAMBLE = (
    '# j2 from \'macros.j2\' import header\n# {{ header("Titel DE", "Title EN") }}\n\n'
)
TITLES = {"Titel DE": "Title EN"}


def _slide_pair(slug: str, de_title: str, en_title: str) -> str:
    return (
        f'# %% [markdown] lang="de" tags=["slide"] slide_id="{slug}"\n'
        f"#\n# ## {de_title}\n#\n# - DE Bullet\n\n"
        f'# %% [markdown] lang="en" tags=["slide"] slide_id="{slug}"\n'
        f"#\n# ## {en_title}\n#\n# - EN Bullet\n\n"
    )


def _idless_slide_pair(de_title: str, en_title: str) -> str:
    """A slide pair with NO slide_id — minting fodder for the bootstrap."""
    return (
        f'# %% [markdown] lang="de" tags=["slide"]\n'
        f"#\n# ## {de_title}\n#\n# - DE Bullet\n\n"
        f'# %% [markdown] lang="en" tags=["slide"]\n'
        f"#\n# ## {en_title}\n#\n# - EN Bullet\n\n"
    )


def _shared_code(name: str = "end") -> str:
    return f'# %% tags=["keep"]\n{name} = 1\n\n'


def _localized_bodies(text: str) -> list[str]:
    _, cells = split_cells(text)
    return [c.body.rstrip("\n") for c in cells if c.metadata.lang is not None]


def _mirror_translator(de_text: str, en_text: str) -> StaticSlideTranslator:
    """DE-body/title -> EN twin, built straight from the canonical split halves."""
    mapping = dict(zip(_localized_bodies(de_text), _localized_bodies(en_text)))
    mapping.update(TITLES)
    return StaticSlideTranslator(mapping=mapping)


def _reverse_translator(de_text: str, en_text: str) -> StaticSlideTranslator:
    mapping = dict(zip(_localized_bodies(en_text), _localized_bodies(de_text)))
    mapping["Title EN"] = "Titel DE"
    return StaticSlideTranslator(mapping=mapping)


def _split(text: str) -> tuple[str, str]:
    de, en = split_text(text)
    assert unify_texts(de, en) == text  # the fixture itself round-trips
    return de, en


def _slide_ids(text: str) -> list[str | None]:
    _, cells = split_cells(text)
    return [c.metadata.slide_id for c in cells if c.metadata.is_slide_start]


def _cell_count(text: str) -> int:
    _, cells = split_cells(text)
    return len(cells)


def _cache(tmp_path: Path) -> SyncWatermarkCache:
    return SyncWatermarkCache(tmp_path / "clm-llm.sqlite")


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


# A trailing-symmetric deck (ends on a shared cell) so the generated EN half
# byte-matches split's EN half exactly — see test_translate_deck §ByteExact.
_DECK = HEADER_PREAMBLE + _slide_pair("intro", "Einleitung", "Introduction") + _shared_code("end")
_IDLESS_DECK = (
    HEADER_PREAMBLE + _idless_slide_pair("Einleitung", "Introduction") + _shared_code("end")
)
# A deck that ends on a slide *pair* (no shared terminator) is trailing-blank
# *asymmetric* after split — exercises the harder path through assign_ids +
# watermark, where the generated half does not byte-match a hypothetical other.
_DECK_ASYMMETRIC = (
    HEADER_PREAMBLE
    + _slide_pair("intro", "Einleitung", "Introduction")
    + _slide_pair("more", "Mehr", "More")
)


# ---------------------------------------------------------------------------
# Absent twin -> bootstrap
# ---------------------------------------------------------------------------


class TestBootstrapWritesTwin:
    def test_writes_valid_round_tripping_pair(self, tmp_path: Path):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        cache = _cache(tmp_path)
        try:
            result = bootstrap_deck(
                de_path, translator=_mirror_translator(de, en), watermark_cache=cache
            )
        finally:
            cache.close()

        assert result.action == "bootstrapped"
        twin = tmp_path / "slides_x.en.py"
        assert twin.exists()
        twin_text = twin.read_text(encoding="utf-8")
        # Trailing-symmetric deck: the new half equals split's EN half exactly.
        assert twin_text == en
        # ... and the on-disk pair round-trips like any real split pair.
        de_text = de_path.read_text(encoding="utf-8")
        assert split_text(unify_texts(de_text, twin_text)) == (de_text, twin_text)
        assert result.watermark_recorded is True

    def test_records_watermark_for_the_pair(self, tmp_path: Path):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        cache = _cache(tmp_path)
        try:
            result = bootstrap_deck(
                de_path, translator=_mirror_translator(de, en), watermark_cache=cache
            )
            assert cache.has_pair(str(result.de_path), str(result.en_path))
        finally:
            cache.close()

    def test_no_cache_skips_watermark_but_still_writes(self, tmp_path: Path):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        result = bootstrap_deck(de_path, translator=_mirror_translator(de, en))
        assert result.action == "bootstrapped"
        assert result.watermark_recorded is False
        assert (tmp_path / "slides_x.en.py").read_text(encoding="utf-8") == en

    def test_asymmetric_deck_round_trips_and_is_idempotent(self, tmp_path: Path):
        # A deck ending on a slide pair is trailing-blank asymmetric after split:
        # the generated half does not byte-match a hypothetical other half, but it
        # must still form a valid, idempotent pair on disk.
        de, en = _split(_DECK_ASYMMETRIC)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        twin = tmp_path / "slides_x.en.py"
        cache = _cache(tmp_path)
        try:
            first = bootstrap_deck(
                de_path, translator=_mirror_translator(de, en), watermark_cache=cache
            )
            de_text = de_path.read_text(encoding="utf-8")
            en_text = twin.read_text(encoding="utf-8")
            # The written pair round-trips like any real split pair.
            assert split_text(unify_texts(de_text, en_text)) == (de_text, en_text)
            second = bootstrap_deck(
                de_path,
                translator=_mirror_translator(de, en),
                judge=StaticSyncJudge(
                    default_proposal=SyncProposal(verdict="in_sync", proposed_text="")
                ),
                watermark_cache=cache,
            )
        finally:
            cache.close()
        assert first.action == "bootstrapped"
        assert second.action == "synced"
        assert second.plan is not None and second.plan.proposals == []
        assert twin.read_text(encoding="utf-8") == en_text  # not doubled

    def test_reverse_direction_en_source_writes_de(self, tmp_path: Path):
        de, en = _split(_DECK)
        en_path = _write(tmp_path / "slides_x.en.py", en)
        result = bootstrap_deck(en_path, translator=_reverse_translator(de, en))
        assert result.action == "bootstrapped"
        assert result.source_lang == "en"
        assert result.target_lang == "de"
        twin = tmp_path / "slides_x.de.py"
        twin_text = twin.read_text(encoding="utf-8")
        assert twin_text == de
        assert split_text(unify_texts(twin_text, en)) == (twin_text, en)


# ---------------------------------------------------------------------------
# ID minting / preservation
# ---------------------------------------------------------------------------


class TestIdParity:
    def test_idless_source_mints_matching_ids_on_both_halves(self, tmp_path: Path):
        de, en = _split(_IDLESS_DECK)
        assert _slide_ids(de) == [None]  # the fixture really is id-less
        de_path = _write(tmp_path / "slides_x.de.py", de)
        result = bootstrap_deck(de_path, translator=_mirror_translator(de, en))

        de_ids = _slide_ids(de_path.read_text(encoding="utf-8"))
        en_ids = _slide_ids((tmp_path / "slides_x.en.py").read_text(encoding="utf-8"))
        # EN-authority slug "introduction" minted onto BOTH halves, in parity.
        assert de_ids == en_ids == ["introduction"]
        assert result.ids_assigned > 0

    def test_existing_ids_are_preserved_not_reslugged(self, tmp_path: Path):
        # The author's id differs from the content-derived slug; minting must
        # not overwrite it (force=False -> existing id wins).
        deck = (
            HEADER_PREAMBLE
            + _slide_pair("custom-intro", "Einleitung", "Introduction")
            + _shared_code("end")
        )
        de, en = _split(deck)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        bootstrap_deck(de_path, translator=_mirror_translator(de, en))

        assert _slide_ids(de_path.read_text(encoding="utf-8")) == ["custom-intro"]
        assert _slide_ids((tmp_path / "slides_x.en.py").read_text(encoding="utf-8")) == [
            "custom-intro"
        ]

    def test_idless_source_twice_is_a_sync_noop(self, tmp_path: Path):
        # The interesting idempotency case: minting rewrites BOTH halves on the
        # first run, so the watermark must capture the post-mint state for the
        # second run to see no change (not re-mint, not double).
        de, en = _split(_IDLESS_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        twin = tmp_path / "slides_x.en.py"
        cache = _cache(tmp_path)
        try:
            bootstrap_deck(de_path, translator=_mirror_translator(de, en), watermark_cache=cache)
            de_after = de_path.read_text(encoding="utf-8")
            en_after = twin.read_text(encoding="utf-8")
            second = bootstrap_deck(
                de_path,
                translator=_mirror_translator(de, en),
                judge=StaticSyncJudge(
                    default_proposal=SyncProposal(verdict="in_sync", proposed_text="")
                ),
                watermark_cache=cache,
            )
        finally:
            cache.close()
        assert second.action == "synced"
        assert second.apply_result is not None and not second.apply_result.has_errors
        assert second.plan is not None and second.plan.proposals == []
        # Both halves untouched by the second run (no re-mint, no doubling).
        assert de_path.read_text(encoding="utf-8") == de_after
        assert twin.read_text(encoding="utf-8") == en_after


# ---------------------------------------------------------------------------
# D2: idempotency by delegation — the central safety property
# ---------------------------------------------------------------------------


class TestIdempotencyByDelegation:
    def test_translate_twice_is_a_sync_noop(self, tmp_path: Path):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        twin = tmp_path / "slides_x.en.py"
        cache = _cache(tmp_path)
        try:
            first = bootstrap_deck(
                de_path, translator=_mirror_translator(de, en), watermark_cache=cache
            )
            assert first.action == "bootstrapped"
            after_first = twin.read_text(encoding="utf-8")
            cells_after_first = _cell_count(after_first)

            # Second run: twin now exists -> must degrade to incremental sync,
            # not re-translate. With the watermark recorded, sync sees no change.
            second = bootstrap_deck(
                de_path,
                translator=_mirror_translator(de, en),
                judge=StaticSyncJudge(
                    default_proposal=SyncProposal(verdict="in_sync", proposed_text="")
                ),
                watermark_cache=cache,
            )
        finally:
            cache.close()

        assert second.action == "synced"
        assert second.apply_result is not None
        assert not second.apply_result.has_errors
        assert second.apply_result.deferred == 0
        assert second.apply_result.applied == 0  # nothing to do
        assert second.plan is not None and second.plan.proposals == []
        # The deck was not doubled or otherwise rewritten.
        after_second = twin.read_text(encoding="utf-8")
        assert after_second == after_first
        assert _cell_count(after_second) == cells_after_first

    def test_present_twin_in_sync_delegates_without_doubling(self, tmp_path: Path):
        # Both halves already on disk, in parity, no prior watermark: a translate
        # must route through sync and report clean rather than re-bootstrap.
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _write(tmp_path / "slides_x.en.py", en)
        cache = _cache(tmp_path)
        try:
            result = bootstrap_deck(
                de_path,
                translator=_mirror_translator(de, en),
                judge=StaticSyncJudge(
                    default_proposal=SyncProposal(verdict="in_sync", proposed_text="")
                ),
                watermark_cache=cache,
            )
        finally:
            cache.close()
        assert result.action == "synced"
        assert result.apply_result is not None
        assert not result.apply_result.has_errors
        assert (tmp_path / "slides_x.en.py").read_text(encoding="utf-8") == en


# ---------------------------------------------------------------------------
# --force
# ---------------------------------------------------------------------------


class TestForce:
    def test_force_overwrites_an_existing_twin(self, tmp_path: Path):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        twin = _write(tmp_path / "slides_x.en.py", '# %% [markdown] lang="en"\n#\n# stale\n\n')
        result = bootstrap_deck(de_path, translator=_mirror_translator(de, en), force=True)
        assert result.action == "bootstrapped"
        # The stale hand-written twin is gone, replaced by the synthesized half.
        assert "stale" not in twin.read_text(encoding="utf-8")
        assert twin.read_text(encoding="utf-8") == en


# ---------------------------------------------------------------------------
# Path resolution / rejection
# ---------------------------------------------------------------------------


class TestResolution:
    def test_rejects_bilingual_stem(self, tmp_path: Path):
        with pytest.raises(TranslateBootstrapError, match="no .de/.en language tag"):
            derive_bootstrap_paths(tmp_path / "slides_x.py")

    def test_rejects_voiceover_companion(self, tmp_path: Path):
        with pytest.raises(TranslateBootstrapError, match="voiceover companion"):
            derive_bootstrap_paths(tmp_path / "voiceover_x.de.py")

    def test_to_override_same_language_rejected(self, tmp_path: Path):
        with pytest.raises(TranslateBootstrapError, match="same language"):
            derive_bootstrap_paths(tmp_path / "slides_x.de.py", target_lang="de")

    def test_to_override_unsupported_language_rejected(self, tmp_path: Path):
        with pytest.raises(TranslateBootstrapError, match="unsupported target"):
            derive_bootstrap_paths(tmp_path / "slides_x.de.py", target_lang="fr")

    def test_derives_twin_path_and_direction(self, tmp_path: Path):
        de_path = _write(tmp_path / "slides_x.de.py", _split(_DECK)[0])
        paths = derive_bootstrap_paths(de_path)
        assert paths.source_lang == "de"
        assert paths.target_lang == "en"
        assert paths.twin_path.name == "slides_x.en.py"
        assert paths.de_path == paths.source_path
        assert paths.en_path == paths.twin_path
        assert paths.twin_exists is False

    def test_prefix_and_extension_agnostic_twin(self, tmp_path: Path):
        # No slides_ prefix, non-.py extension: the .de/.en tag still drives it.
        src = tmp_path / "apis.de.cpp"
        _write(src, "")
        paths = derive_bootstrap_paths(src)
        assert paths.twin_path.name == "apis.en.cpp"

    def test_empty_twin_is_treated_as_absent(self, tmp_path: Path):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _write(tmp_path / "slides_x.en.py", "")  # stray empty file
        result = bootstrap_deck(de_path, translator=_mirror_translator(de, en))
        # An empty twin must not route to sync; it is bootstrapped over.
        assert result.action == "bootstrapped"
        assert (tmp_path / "slides_x.en.py").read_text(encoding="utf-8") == en


def test_result_type_is_exported():
    # Cheap guard that the public surface stays importable from one place.
    assert BootstrapResult.__module__ == "clm.slides.translate_bootstrap"
