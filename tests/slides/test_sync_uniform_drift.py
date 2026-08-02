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
from clm.slides.sync_diff import (
    _UNIFORM_DRIFT_MIN,
    DeckBaseline,
    DeckDiff,
    baseline_from_deck,
    diff_outcome,
)
from clm.slides.sync_wire import WIRE_SCHEMA

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


# Four localized members: enough to clear the threshold (3) and still leave one
# member free to carry an unrelated row.
DE0 = _build(
    HEADER_DE,
    _slide("s0", "de", "Titel"),
    _localized("m1", "de", "DE eins"),
    _localized("m2", "de", "DE zwei"),
    _localized("m3", "de", "DE drei"),
    _localized("m4", "de", "DE vier"),
)
EN0 = _build(
    HEADER_EN,
    _slide("s0", "en", "Title"),
    _localized("m1", "en", "EN one"),
    _localized("m2", "en", "EN two"),
    _localized("m3", "en", "EN three"),
    _localized("m4", "en", "EN four"),
)


def _edit_en(*members: str) -> str:
    """Drift the EN half of the named members (the review-after-translate shape)."""
    text = EN0
    for m in members:
        text = text.replace(f"EN {m}", f"EN {m}, revised")
    return text


THREE_EN = ("one", "two", "three")


class TestItFires:
    def test_same_side_edits_are_summarized(self):
        """The shape the field hit: one half reviewed, several members drift."""
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0, _edit_en(*THREE_EN))

        edits = [i for i in diff.items if i.action == "translate_edit"]
        assert len(edits) == 3
        assert {i.side for i in edits} == {"en"}

        obs = _obs(diff)
        assert len(obs) == 1
        assert obs[0].side == "en"
        assert "all 3 translate_edit items drift on the en side" in obs[0].detail

    def test_the_count_is_the_translate_edit_count_not_the_item_count(self):
        """Pins N against a deck whose item list is WIDER than its translate_edit rows.

        Every other fixture here happens to make the two numbers equal, so without
        this a `len(self.items)` slip would print "all 4 translate_edit items" over
        three of them — exactly the kind of false summary this feature exists to
        prevent — and nothing would fail.
        """
        base = _snapshot(DE0, EN0)
        # m4 gains a new EN-only sibling → an extra non-translate_edit row.
        en = _edit_en(*THREE_EN).replace(
            '# %% [markdown] lang="en" slide_id="m4"',
            _localized("m5", "en", "EN five").rstrip("\n") + "\n\n"
            '# %% [markdown] lang="en" slide_id="m4"',
        )
        diff = _diff(base, DE0, en)

        edits = [i for i in diff.items if i.action == "translate_edit"]
        assert len(edits) == 3
        assert len(diff.items) > len(edits), "fixture must make the two counts differ"
        detail = _obs(diff)[0].detail
        assert "all 3 translate_edit items" in detail
        # The extra rows are not `verify_translation`, so the tail must stay absent.
        # Without this, counting "every non-translate_edit item" as two-sided passes.
        assert not [i for i in diff.items if i.action == "verify_translation"]
        assert "further member(s)" not in detail

    def test_the_detail_names_keep_twin_and_both_readings(self):
        """Neither reading may be the silent default — the engine cannot tell them apart."""
        base = _snapshot(DE0, EN0)
        detail = _obs(_diff(base, DE0, _edit_en(*THREE_EN)))[0].detail

        assert "keep_twin" in detail, "the answer that collapses the whole set must be named"
        assert "faithful rendering" in detail  # the keep_twin precondition
        assert "source of truth" in detail  # the opposite reading
        assert "adapted de bodies" in detail  # ...and what it costs
        # The reason neither reading can be picked for the agent.
        assert "never which side is authoritative" in detail

    def test_it_fires_for_de_drift_too(self):
        """Nothing about the rule privileges a language — the engine is symmetric."""
        base = _snapshot(DE0, EN0)
        de = (
            DE0.replace("DE eins", "DE eins, neu")
            .replace("DE zwei", "DE zwei, neu")
            .replace("DE drei", "DE drei, neu")
        )
        obs = _obs(_diff(base, de, EN0))
        assert len(obs) == 1
        assert obs[0].side == "de"
        assert "all 3 translate_edit items drift on the de side" in obs[0].detail
        assert "adapted en bodies" in obs[0].detail

    def test_verify_translation_rows_are_counted_so_the_claim_is_not_overread(self):
        """`verify_translation` members are outside the set and reject `keep_twin`.

        Without the tail, "all drift is on the en side" invites a blanket sweep that
        picks up a member the answer is not even valid for.
        """
        base = _snapshot(DE0, EN0)
        de = DE0.replace("DE vier", "DE vier, geaendert")
        en = _edit_en(*THREE_EN).replace("EN four", "EN four, revised")
        diff = _diff(base, de, en)

        assert [i.action for i in diff.items if i.key == "id:m4"] == ["verify_translation"]
        obs = _obs(diff)
        assert len(obs) == 1
        # The literal count, so an off-by-one or a "every non-translate_edit row"
        # slip cannot pass.
        assert "(1 further member(s) need two-sided verification" in obs[0].detail
        assert "do not take `keep_twin`" in obs[0].detail

    def test_the_tail_does_not_claim_both_halves_moved(self):
        """`verify_translation` also fires when a side with NO baseline fp merely landed.

        That member's other half never moved, so glossing the tail as "moved on both
        sides" would contradict the item's own detail in the same report.
        """
        import attrs

        base = _snapshot(DE0, EN0)
        entry = base.members["id:m4"]
        base = attrs.evolve(
            base,
            members={**base.members, "id:m4": attrs.evolve(entry, en_fp=None, en_body_fp=None)},
        )
        diff = _diff(base, DE0, _edit_en(*THREE_EN))

        m4 = [i for i in diff.items if i.key == "id:m4"]
        assert [i.action for i in m4] == ["verify_translation"]
        assert "landed since base" in m4[0].detail, "the pending-variant site, not both-moved"

        detail = _obs(diff)[0].detail
        assert "moved on both sides" not in detail
        assert "need two-sided verification" in detail


class TestItStaysQuiet:
    def test_below_the_threshold_is_not_ceremony(self):
        """Two rows collapse almost nothing, and land one-sided by chance about half
        the time — advice that fires on noise gets ignored."""
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0, _edit_en("one", "two"))
        assert len([i for i in diff.items if i.action == "translate_edit"]) == 2
        assert _obs(diff) == []

    def test_a_single_edit_says_nothing(self):
        """One row's own ``side`` already says everything the observation would."""
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0, _edit_en("one"))
        assert len([i for i in diff.items if i.action == "translate_edit"]) == 1
        assert _obs(diff) == []

    def test_mixed_sides_produce_no_summary(self):
        """There is no single reading to offer, so offering one would mislead."""
        base = _snapshot(DE0, EN0)
        de = DE0.replace("DE eins", "DE eins, neu")
        en = _edit_en("two", "three", "four")
        diff = _diff(base, de, en)

        edits = [i for i in diff.items if i.action == "translate_edit"]
        assert len(edits) >= _UNIFORM_DRIFT_MIN, "must clear the threshold to isolate the cause"
        assert {i.side for i in edits} == {"de", "en"}
        assert _obs(diff) == []

    def test_a_clean_deck_says_nothing(self):
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0, EN0)
        assert diff.is_clean
        assert _obs(diff) == []

    def test_an_unattributable_row_suppresses_the_whole_summary(self):
        """A ``translate_edit`` without a ``side`` must silence it, not be filtered out.

        Both emit sites pass a concrete side today, so this is defensive code — which
        is exactly the kind that has to be exercised directly or it merely looks safe.

        **Four** rows, not three: with three, dropping one leaves two, which the
        threshold rejects anyway, so a silent filter would be indistinguishable from
        the guard. At four, a filter yields three attributable rows, clears the
        threshold, and fires "all 3 translate_edit items" while the agent is looking
        at four — the false summary this observation exists to avoid.
        """
        import attrs

        from clm.slides.sync_diff import _Differ

        base = _snapshot(DE0, EN0)
        differ = _Differ(_parse(DE0, EN0), base)
        differ.items = [
            i
            for i in _diff(base, DE0, _edit_en("one", "two", "three", "four")).items
            if i.action == "translate_edit"
        ]
        assert len(differ.items) == 4
        control = differ._uniform_drift_observation()
        assert len(control) == 1 and "all 4 translate_edit items" in control[0].detail

        differ.items = [*differ.items[:-1], attrs.evolve(differ.items[-1], side=None)]
        assert differ._uniform_drift_observation() == [], (
            "an unattributable row must suppress the summary, never be filtered out of the count"
        )


class TestItChangesNoVerdict:
    """The observation is informational. It must not move a single classification."""

    def test_items_are_identical_with_and_without_the_summary(self):
        """Below-threshold (quiet) vs above (fires) must classify a shared member the same."""
        base = _snapshot(DE0, EN0)
        quiet = _diff(base, DE0, _edit_en("one"))
        loud = _diff(base, DE0, _edit_en(*THREE_EN))
        assert _obs(quiet) == [] and len(_obs(loud)) == 1

        m1_quiet = [(i.action, i.direction, i.side) for i in quiet.items if i.key == "id:m1"]
        m1_loud = [(i.action, i.direction, i.side) for i in loud.items if i.key == "id:m1"]
        assert m1_quiet == m1_loud

    def test_is_clean_is_untouched(self):
        """Unlike ``group_order_divergence`` (#654), this kind never suppresses clean.

        It can only appear alongside three or more items, so ``is_clean`` is already
        False — but the coupling must be absent by construction, not by luck, so this
        strips the items and checks the observation alone leaves a clean verdict.
        """
        base = _snapshot(DE0, EN0)
        diff = _diff(base, DE0, _edit_en(*THREE_EN))
        assert not diff.is_clean  # because of the items

        observations = _obs(diff)
        # Without this the test degenerates: with no observation to strip, the
        # assertion below is `DeckDiff(items=[], observations=[]).is_clean`, which
        # is trivially true and survives deleting the entire feature.
        assert len(observations) == 1

        stripped = DeckDiff(
            items=[],
            in_sync_count=diff.in_sync_count,
            observations=observations,
        )
        assert stripped.is_clean, "uniform_drift_side must not suppress is_clean"


class TestItReachesTheSurfaces:
    def test_the_json_envelope_carries_it(self):
        base = _snapshot(DE0, EN0)
        payload = _diff(base, DE0, _edit_en(*THREE_EN)).to_payload()

        rows = [o for o in payload["observations"] if o["kind"] == "uniform_drift_side"]
        assert len(rows) == 1
        assert rows[0]["side"] == "en"
        assert rows[0]["member"] is None  # deck-level, not member-level
        assert "keep_twin" in rows[0]["detail"]
        # Branchable without parsing prose — the point of it being an observation.
        assert payload["schema"] == WIRE_SCHEMA

    def test_the_human_report_prints_it_after_the_items(self):
        """A summary belongs below what it summarizes.

        ``_render_pair`` reads only ``bundle.de_path.name``, so a stub keeps this a
        rendering test rather than a bundle-loading one.
        """
        from pathlib import Path
        from types import SimpleNamespace

        from clm.cli.commands.slides import sync_v3

        base = _snapshot(DE0, EN0)
        bundle = SimpleNamespace(de_path=Path("slides_t.de.py"))
        text = sync_v3._render_pair(bundle, _diff(base, DE0, _edit_en(*THREE_EN)))  # type: ignore[arg-type]

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
        diff = _diff(base, DE0, _edit_en("one"))
        item = next(i for i in diff.items if i.action == "translate_edit")
        assert "keep_twin" in item.detail
