"""The ``uniform_drift_side`` observation — Q5's report-level hint.

The review-after-translate flow regenerates or hand-reviews one language half,
then reports. Every drifted member frames ``translate_edit`` pointing at the
*twin* ("the en variant was edited — adapt the twin"), so an agent reading item
by item sees N independent requests to go edit the other language — when what it
actually wants is to bank the half it just reviewed. The field report cost ~30
pointless decision items to exactly that.

The fix is aggregation, not new information: ``side`` and ``direction`` are
already on every item, but "these all moved on the *same* side" is a property of
the set that no single row shows. It is deliberately **not** a
``drift: source|twin`` field — the engine knows which side moved, never which
side is authoritative, and naming one "source" would assert something it cannot
observe. The observation reports the side and spells out both readings.

Pinned here: when it fires, when it must not, that it never changes a verdict,
and that it reaches both the JSON envelope and the human report.
"""

from __future__ import annotations

from clm.slides.bilingual_doc import BilingualDeck
from clm.slides.doc_lenses import parse_bundle
from clm.slides.sync_diff import DeckBaseline, DeckDiff, baseline_from_deck, diff_outcome

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


def _parse(de: str, en: str) -> BilingualDeck:
    outcome = parse_bundle(de, en)
    assert outcome.deck is not None, outcome.refusal.render() if outcome.refusal else "parse failed"
    return outcome.deck


def _snapshot(de: str, en: str) -> DeckBaseline:
    return baseline_from_deck(_parse(de, en))


def _diff(base: DeckBaseline, de: str, en: str) -> DeckDiff:
    return diff_outcome(parse_bundle(de, en), base)


def _obs(diff: DeckDiff):
    return [o for o in diff.observations if o.kind == "uniform_drift_side"]


# A deck with three localized members, so several can drift independently.
DE0 = _build(
    HEADER_DE,
    _slide("s0", "de", "Titel"),
    _localized("m1", "de", "DE eins"),
    _localized("m2", "de", "DE zwei"),
    _localized("m3", "de", "DE drei"),
)
EN0 = _build(
    HEADER_EN,
    _slide("s0", "en", "Title"),
    _localized("m1", "en", "EN one"),
    _localized("m2", "en", "EN two"),
    _localized("m3", "en", "EN three"),
)


class TestItFires:
    def test_two_edits_on_the_same_side_are_summarized(self):
        """The shape the field hit: one half reviewed, several members drift."""
        base = _snapshot(DE0, EN0)
        en = EN0.replace("EN one", "EN one, revised").replace("EN two", "EN two, revised")
        diff = _diff(base, DE0, en)

        edits = [i for i in diff.items if i.action == "translate_edit"]
        assert len(edits) == 2
        assert {i.side for i in edits} == {"en"}

        obs = _obs(diff)
        assert len(obs) == 1
        assert obs[0].side == "en"
        assert "all 2 translate_edit items drift on the en side" in obs[0].detail

    def test_the_detail_names_keep_twin_and_both_readings(self):
        """Neither reading may be the silent default — the engine cannot tell them apart."""
        base = _snapshot(DE0, EN0)
        en = EN0.replace("EN one", "EN 1x").replace("EN two", "EN 2x")
        detail = _obs(_diff(base, DE0, en))[0].detail

        assert "keep_twin" in detail, "the answer that collapses the whole set must be named"
        assert "faithful rendering" in detail  # the keep_twin precondition
        assert "source of truth" in detail  # the opposite reading
        assert "adapted de bodies" in detail  # ...and what it costs

    def test_it_fires_for_de_drift_too(self):
        """Nothing about the rule privileges a language — the engine is symmetric."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace("DE eins", "DE eins, neu").replace("DE zwei", "DE zwei, neu")
        obs = _obs(_diff(base, de, EN0))
        assert len(obs) == 1
        assert obs[0].side == "de"
        assert "drift on the de side" in obs[0].detail
        assert "adapted en bodies" in obs[0].detail

    def test_both_sided_rows_are_counted_so_the_claim_is_not_overread(self):
        """A `verify_translation` member moved on BOTH sides — say so, or the hint lies.

        Without this, "all drift is on the en side" invites an agent to answer the
        whole deck `keep_twin`, including a member whose German also moved.
        """
        base = _snapshot(DE0, EN0)
        de = DE0.replace("DE drei", "DE drei, geaendert")
        en = (
            EN0.replace("EN one", "EN one, revised")
            .replace("EN two", "EN two, revised")
            .replace("EN three", "EN three, revised")
        )
        diff = _diff(base, de, en)

        assert [i.action for i in diff.items if i.key == "id:m3"] == ["verify_translation"]
        obs = _obs(diff)
        assert len(obs) == 1
        assert "moved on both sides" in obs[0].detail
        assert "verify_translation" in obs[0].detail


class TestItStaysQuiet:
    def test_a_single_edit_is_not_ceremony(self):
        """One row's own ``side`` already says everything the observation would."""
        base = _snapshot(DE0, EN0)
        en = EN0.replace("EN one", "EN one, revised")
        diff = _diff(base, DE0, en)
        assert len([i for i in diff.items if i.action == "translate_edit"]) == 1
        assert _obs(diff) == []

    def test_mixed_sides_produce_no_summary(self):
        """There is no single reading to offer, so offering one would mislead."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace("DE eins", "DE eins, neu")
        en = EN0.replace("EN two", "EN two, revised")
        diff = _diff(base, de, en)

        edits = [i for i in diff.items if i.action == "translate_edit"]
        assert {i.side for i in edits} == {"de", "en"}
        assert _obs(diff) == []

    def test_a_clean_deck_says_nothing(self):
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0, EN0)
        assert diff.is_clean
        assert _obs(diff) == []


class TestItChangesNoVerdict:
    """The observation is informational. It must not move a single classification."""

    def test_items_are_identical_with_and_without_the_summary(self):
        """Two drifts (fires) vs one (does not) must classify the shared member the same."""
        base = _snapshot(DE0, EN0)
        one = _diff(base, DE0, EN0.replace("EN one", "EN one, revised"))
        two = _diff(
            base,
            DE0,
            EN0.replace("EN one", "EN one, revised").replace("EN two", "EN two, revised"),
        )
        assert _obs(one) == [] and len(_obs(two)) == 1

        m1_one = [(i.action, i.direction, i.side) for i in one.items if i.key == "id:m1"]
        m1_two = [(i.action, i.direction, i.side) for i in two.items if i.key == "id:m1"]
        assert m1_one == m1_two

    def test_is_clean_is_untouched(self):
        """Unlike ``group_order_divergence`` (#654), this kind never suppresses clean.

        It can only appear alongside two or more items, so ``is_clean`` is already
        False — but the coupling must be absent by construction, not by luck.
        """
        base = _snapshot(DE0, EN0)
        en = EN0.replace("EN one", "EN one, revised").replace("EN two", "EN two, revised")
        diff = _diff(base, DE0, en)
        assert not diff.is_clean  # because of the items

        stripped = DeckDiff(
            items=[],
            in_sync_count=diff.in_sync_count,
            observations=_obs(diff),
        )
        assert stripped.is_clean, "uniform_drift_side must not suppress is_clean"


class TestItReachesTheSurfaces:
    def test_the_json_envelope_carries_it(self):
        base = _snapshot(DE0, EN0)
        en = EN0.replace("EN one", "EN one, revised").replace("EN two", "EN two, revised")
        payload = _diff(base, DE0, en).to_payload()

        rows = [o for o in payload["observations"] if o["kind"] == "uniform_drift_side"]
        assert len(rows) == 1
        assert rows[0]["side"] == "en"
        assert rows[0]["member"] is None  # deck-level, not member-level
        assert "keep_twin" in rows[0]["detail"]

    def test_the_human_report_prints_it_after_the_items(self):
        """A summary belongs below what it summarizes.

        ``_render_pair`` reads only ``bundle.de_path.name``, so a stub keeps this a
        rendering test rather than a bundle-loading one.
        """
        from pathlib import Path
        from types import SimpleNamespace

        from clm.cli.commands.slides import sync_v3

        base = _snapshot(DE0, EN0)
        en = EN0.replace("EN one", "EN one, revised").replace("EN two", "EN two, revised")
        bundle = SimpleNamespace(de_path=Path("slides_t.de.py"))
        text = sync_v3._render_pair(bundle, _diff(base, DE0, en))  # type: ignore[arg-type]

        assert "observation/uniform_drift_side" in text
        lines = text.splitlines()
        obs_at = next(i for i, ln in enumerate(lines) if "uniform_drift_side" in ln)
        # Match the item row's `outcome/action` prefix, not the bare action name —
        # the observation's own detail mentions "translate_edit" too.
        last_item = max(i for i, ln in enumerate(lines) if ln.startswith("  edit/translate_edit"))
        assert obs_at > last_item

    def test_the_per_item_detail_also_names_keep_twin(self):
        """The summary is the aggregate route; the row itself must still be answerable."""
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0, EN0.replace("EN one", "EN one, revised"))
        item = next(i for i in diff.items if i.action == "translate_edit")
        assert "keep_twin" in item.detail
