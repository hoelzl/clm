"""The #520 Phase 1 exit gate: ``project ∘ parse`` byte-identity over a corpus.

Two scopes:

* **Bundled** (``tests/data/doc_corpus`` — always present, runs in the fast
  suite and CI): five normalized deck pairs covering the shapes the design
  names for Phase 1 — a ``voiceover/`` subdir companion (notes role), a
  sibling companion (voiceover role), the legacy inline-voiceover layout,
  an observation-carrying pair (shared divergence, one-sided member, the
  #443 id-stamp-pending shape), and a ``//``-token C++ deck. Zero refusals
  allowed here.
* **Real corpus** (``CLM_SYNC_CORPUS_DIR`` or the maintainer's PythonCourses
  checkout; ``integration + slow``, local/release-time only — the CLM repo
  CI carries no course content): byte-identity must hold for **every**
  parsed pair, refusals must be framed and bounded. Refusals are expected:
  the §3.4 normalize precondition is stricter than v2 (the legacy
  inherited-id companions and residual id-less localized cells are the
  standing normalize worklist from PythonCourses#78) — the ceiling pins
  that population so it can only shrink.

Following the ``test_sync_corpus_*`` conventions: corpus resolution via env
var, then the maintainer path; scale-dependent assertions only on the real
corpus; discovery through the prefix-agnostic pairing helpers (which prune
ignored/private dirs and never treat ``voiceover_*`` companions as decks).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clm.slides.doc_lenses import LoadedBundle, load_bundle, project
from clm.slides.pairing import find_split_slide_files_recursive, iter_split_pairs

_BUNDLED_CORPUS = Path(__file__).parent.parent / "data" / "doc_corpus"

# The known v3-precondition failures on the real corpus, measured 2026-07-02
# post-PythonCourses#78: 59 decks with legacy inherited-id companions
# (slide_id == for_slide duplicates the deck id in the ≤4-file namespace)
# + 3 decks with residual id-less localized cells = 62. The ceiling gives a
# little headroom for ordinary authoring churn; a real rise means id-less /
# duplicate-id content is being reintroduced. Refusal CODES are pinned too:
# a new code appearing on the real corpus means the parser started refusing
# a shape it used to accept — investigate before widening the set.
_REAL_REFUSAL_CEILING = 70
_REAL_PARSED_FLOOR = 620
_EXPECTED_REFUSAL_CODES = {
    "duplicate_id",
    "idless_localized",
    "idless_narrative",
    "idless_anchor",
    "legacy_title_companion",
}


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
    assert not solos, f"corpus contains unpaired deck halves: {solos}"
    return pairs


def _assert_byte_identity(bundle: LoadedBundle) -> None:
    deck = bundle.outcome.deck
    assert deck is not None
    for lang, part, path in (
        ("de", "deck", bundle.de_path),
        ("en", "deck", bundle.en_path),
        ("de", "companion", bundle.de_companion_path),
        ("en", "companion", bundle.en_companion_path),
    ):
        want = path.read_text(encoding="utf-8") if path else None
        got = project(deck, lang, part)  # type: ignore[arg-type]
        assert got == want, f"projection of {lang}/{part} diverges for {bundle.de_path.name}"


class TestBundledDocCorpus:
    """Fast-suite gate over the vendored normalized fixtures."""

    def test_every_bundled_pair_parses_and_round_trips(self):
        pairs = _discover(_BUNDLED_CORPUS)
        assert len(pairs) == 5
        for de_path, _en_path in pairs:
            bundle = load_bundle(de_path)
            assert bundle.outcome.ok, (
                f"{de_path.name}: {bundle.outcome.refusal.render()}"
                if bundle.outcome.refusal
                else de_path.name
            )
            _assert_byte_identity(bundle)

    def test_companion_layouts_are_both_exercised(self):
        pairs = {de.parent.name: de for de, _ in _discover(_BUNDLED_CORPUS)}
        alpha = load_bundle(pairs["topic_alpha"])
        assert alpha.de_companion_path is not None
        assert alpha.de_companion_path.parent.name == "voiceover"  # subdir layout
        beta = load_bundle(pairs["topic_beta"])
        assert beta.de_companion_path is not None
        assert beta.de_companion_path.parent.name == "topic_beta"  # sibling layout

    def test_observation_fixture_reports_expected_kinds(self):
        pairs = {de.parent.name: de for de, _ in _discover(_BUNDLED_CORPUS)}
        bundle = load_bundle(pairs["topic_delta"])
        deck = bundle.outcome.deck
        assert deck is not None
        assert {o.kind for o in deck.observations} == {
            "id_stamp_pending_twin",
            "shared_divergence",
            "one_sided_member",
        }


_real = _real_corpus_dir()


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(_real is None, reason="real course corpus not available")
class TestRealCorpus:
    """The full-corpus exit gate — local/release-time (integration + slow)."""

    def test_byte_identity_over_the_full_corpus(self):
        assert _real is not None
        pairs = _discover(_real)
        assert len(pairs) >= _REAL_PARSED_FLOOR
        parsed = 0
        refused: list[str] = []
        unexpected_codes: dict[str, set[str]] = {}
        for de_path, _en_path in pairs:
            bundle = load_bundle(de_path)
            if not bundle.outcome.ok:
                refusal = bundle.outcome.refusal
                assert refusal is not None and refusal.reasons, (
                    f"{de_path.name}: refusal must be framed with enumerated reasons"
                )
                refused.append(de_path.name)
                codes = {r.code for r in refusal.reasons}
                if not codes <= _EXPECTED_REFUSAL_CODES:
                    unexpected_codes[de_path.name] = codes - _EXPECTED_REFUSAL_CODES
                continue
            parsed += 1
            _assert_byte_identity(bundle)
        assert parsed >= _REAL_PARSED_FLOOR, f"only {parsed} pairs parsed"
        assert len(refused) <= _REAL_REFUSAL_CEILING, (
            f"{len(refused)} refusals exceed the ceiling {_REAL_REFUSAL_CEILING}: {refused[:10]}…"
        )
        assert not unexpected_codes, f"refusals with unexpected codes: {unexpected_codes}"
