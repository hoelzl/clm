"""The #520 Phase 2 corpus gate: the differ's noise floor over real decks.

Mirror of ``test_doc_lens_corpus.py`` (same corpus resolution, same two
scopes): every parsed pair is diffed against the snapshot of its **own**
current state. That diff must be silent everywhere the deck is internally
consistent — the only admissible items are the carried in-flight states the
parse already observes (a pending twin, a #443 id stamp, a shared
divergence). Phase 1 measured 33 observations corpus-wide; the ceiling
below pins that noise floor so it can only shrink.

The W10 replay itself (52 pairs at the pre-edit ref against the pre-
reconcile tree) needs two historical refs of the course repo and is run
manually via ``clm slides sync shadow`` — its result is recorded in the
Phase 2 exit notes on #520, not asserted here.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clm.slides.doc_lenses import load_bundle
from clm.slides.pairing import find_split_slide_files_recursive, iter_split_pairs
from clm.slides.sync_diff import (
    FRAMED_ACTIONS,
    MECHANICAL_ACTIONS,
    baseline_from_deck,
    diff_deck,
)

_BUNDLED_CORPUS = Path(__file__).parent.parent / "data" / "doc_corpus"

# Phase 1 measured 33 parse observations over 644 parsed pairs; carried
# in-flight states map ≤1 item per observation, plus a little headroom for
# ordinary authoring churn between corpus refreshes. A real rise means the
# differ started manufacturing noise — the exact failure v3 exists to end.
_REAL_SELF_DIFF_ITEM_CEILING = 45
_REAL_PARSED_FLOOR = 620


def _real_corpus_dir() -> Path | None:
    env = os.environ.get("CLM_SYNC_CORPUS_DIR")
    if env:
        path = Path(env)
        if path.is_dir():
            return path
    maintainer = Path(r"C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\slides")
    if maintainer.is_dir():
        return maintainer
    return None


def _discover(root: Path) -> list[tuple[Path, Path]]:
    pairs, solos = iter_split_pairs(find_split_slide_files_recursive(root))
    assert not solos
    return pairs


def _self_diff(de_path: Path):
    bundle = load_bundle(de_path)
    if bundle.outcome.deck is None:
        return None
    deck = bundle.outcome.deck
    return diff_deck(deck, baseline_from_deck(deck))


class TestBundledSelfDiff:
    """Fast-suite gate: the vendored fixtures pin the noise floor exactly."""

    def test_consistent_pairs_diff_clean_against_their_snapshot(self):
        pairs = {de.parent.name: de for de, _ in _discover(_BUNDLED_CORPUS)}
        for topic in ("topic_alpha", "topic_beta", "topic_gamma", "topic_epsilon"):
            diff = _self_diff(pairs[topic])
            assert diff is not None
            assert diff.is_clean, (
                topic,
                [(i.outcome, i.action, i.key) for i in diff.items],
            )

    def test_observation_fixture_yields_exactly_its_carried_states(self):
        pairs = {de.parent.name: de for de, _ in _discover(_BUNDLED_CORPUS)}
        diff = _self_diff(pairs["topic_delta"])
        assert diff is not None
        assert {(i.outcome, i.action) for i in diff.items} == {
            ("transition", "stamp_twin_id"),
            ("add", "translate_new"),
            ("conflict", "pending_divergence"),
        }
        # Carried state never produces a mechanical copy/remove that would
        # rewrite a file on apply out of nothing but parse observations.
        assert not any(i.action in ("mirror_remove", "propagate_shared_edit") for i in diff.items)


_real = _real_corpus_dir()


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(_real is None, reason="real course corpus not available")
class TestRealCorpusSelfDiff:
    """The Phase 2 noise-floor gate over the full real corpus."""

    def test_self_diff_noise_floor_over_the_full_corpus(self):
        assert _real is not None
        pairs = _discover(_real)
        parsed = 0
        total_items = 0
        noisy: list[tuple[str, list[tuple[str, str]]]] = []
        unregistered: set[str] = set()
        for de_path, _en_path in pairs:
            diff = _self_diff(de_path)
            if diff is None:  # normalize refusal — pinned by the lens gate
                continue
            parsed += 1
            if diff.items:
                total_items += len(diff.items)
                noisy.append((de_path.name, [(i.outcome, i.action) for i in diff.items]))
            for item in diff.items:
                if item.action not in MECHANICAL_ACTIONS | FRAMED_ACTIONS:
                    unregistered.add(item.action)
        assert parsed >= _REAL_PARSED_FLOOR
        assert not unregistered, f"actions outside the closed registry: {unregistered}"
        assert total_items <= _REAL_SELF_DIFF_ITEM_CEILING, (
            f"{total_items} self-diff items exceed the noise ceiling "
            f"{_REAL_SELF_DIFF_ITEM_CEILING}: {noisy[:10]}…"
        )
