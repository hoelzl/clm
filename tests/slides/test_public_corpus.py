"""The pinned public-corpus gates (#682) — CI-runnable, exact, attributable.

The private-corpus modules (``test_doc_lens_corpus`` / ``test_sync_diff_corpus``
``TestRealCorpus*``) measure ceilings against a moving checkout and can only
run on the maintainer's machine. These gates run against the **pinned**
`ClmTestCourse <https://github.com/hoelzl/ClmTestCourse>`_ checkout
(``python scripts/fetch_test_corpus.py``), so they run anywhere — including
CI — and assert **exact** numbers, not ceilings: the corpus cannot move, so
any drift is a CLM behavior change. Bump the pin and these numbers together,
deliberately (see ``public_corpus_pin.py``).

The corpus was curated to preserve, deck for deck, the structural properties
the private originals measured (16/16 parity-clean at curation; see
``scripts/curate_test_course.py``): the refusal-code population, the parse
observations, byte-identity of ``project ∘ parse``, and the honest self-diff
noise floor of the observation-carrying decks.
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from clm.core.slide_text.pairing import find_split_slide_files_recursive, iter_split_pairs
from clm.slides.doc_identity import baseline_from_deck
from clm.slides.doc_lenses import load_bundle, project
from clm.slides.sync_diff import FRAMED_ACTIONS, MECHANICAL_ACTIONS, diff_deck

from .public_corpus_pin import PUBLIC_CORPUS_PIN

# The pinned corpus, measured 2026-08-05 at c536d5bb. Exact, not ceilings.
_EXPECTED_PAIRS = 16
_EXPECTED_PARSED = 11
_EXPECTED_REFUSED = 5
_EXPECTED_REFUSAL_CODES = {
    "duplicate_id",
    "idless_anchor",
    "idless_localized",
    "idless_narrative",
    "legacy_title_companion",
}
_EXPECTED_OBSERVATIONS = {
    "slides_functions_very_short.de.py": ["one_sided_member", "shared_divergence"],
    "slides_signatures.de.py": ["shared_divergence"],
}
#: Self-diffing a parsed deck against its own complete snapshot is 0 items for
#: every clean deck; the two observation carriers contribute an honest noise
#: floor (one-sided members frame adds, diverged shared cells frame
#: pending_divergence) that the bundled fixtures are too small to show.
_EXPECTED_SELF_DIFF_ITEMS = {
    "slides_functions_very_short.de.py": 11,
    "slides_signatures.de.py": 1,
}


def _corpus_dir() -> Path | None:
    env = os.environ.get("CLM_PUBLIC_CORPUS_DIR")
    if env:
        path = Path(env)
        return path if path.is_dir() else None
    default = Path(__file__).parent.parent.parent / ".clm-test-corpus"
    return default if (default / "slides").is_dir() else None


_corpus = _corpus_dir()


@pytest.mark.integration
@pytest.mark.skipif(
    _corpus is None,
    reason="public corpus not fetched — run `python scripts/fetch_test_corpus.py`",
)
class TestPublicCorpus:
    def test_checkout_is_at_the_pin(self):
        """A checkout at the wrong revision must FAIL, not measure — the pin
        is what makes a gate failure attributable to CLM."""
        assert _corpus is not None
        completed = subprocess.run(
            ["git", "-C", str(_corpus), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("corpus dir is not a git checkout (env-provided) — pin not checkable")
        assert completed.stdout.strip() == PUBLIC_CORPUS_PIN, (
            "corpus checkout is not at the pin — re-run `python scripts/fetch_test_corpus.py`"
        )

    def test_population_refusals_and_byte_identity(self):
        assert _corpus is not None
        pairs, solos = iter_split_pairs(find_split_slide_files_recursive(_corpus / "slides"))
        assert not solos
        assert len(pairs) == _EXPECTED_PAIRS
        parsed = 0
        codes: set[str] = set()
        observations: dict[str, list[str]] = {}
        for de_path, _en_path in pairs:
            bundle = load_bundle(de_path)
            if bundle.outcome.refusal is not None:
                codes |= {r.code for r in bundle.outcome.refusal.reasons}
                continue
            parsed += 1
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
                assert got == want, f"projection diverges: {de_path.name} {lang}/{part}"
            kinds = sorted({o.kind for o in deck.observations})
            if kinds:
                observations[de_path.name] = kinds
        assert parsed == _EXPECTED_PARSED
        assert len(pairs) - parsed == _EXPECTED_REFUSED
        assert codes == _EXPECTED_REFUSAL_CODES
        assert observations == _EXPECTED_OBSERVATIONS

    def test_self_diff_noise_floor_is_exact(self):
        assert _corpus is not None
        pairs, _solos = iter_split_pairs(find_split_slide_files_recursive(_corpus / "slides"))
        noise: Counter = Counter()
        unregistered: set[str] = set()
        for de_path, _en_path in pairs:
            bundle = load_bundle(de_path)
            if bundle.outcome.deck is None:
                continue
            diff = diff_deck(bundle.outcome.deck, baseline_from_deck(bundle.outcome.deck))
            if diff.items:
                noise[de_path.name] = len(diff.items)
            for item in diff.items:
                if item.action not in MECHANICAL_ACTIONS | FRAMED_ACTIONS:
                    unregistered.add(item.action)
        assert not unregistered, f"actions outside the closed registry: {unregistered}"
        assert dict(noise) == _EXPECTED_SELF_DIFF_ITEMS
