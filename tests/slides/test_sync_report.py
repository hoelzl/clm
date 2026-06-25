"""Tests for the tiered reconciliation report (`clm slides sync` agent contract).

``build_report`` projects a :class:`SyncPlan` into three tiers the agent acts on
differently: **mechanical** (engine applies, no model), **assisted** (a scoped
model task), **ambiguity** (the agent's judgement). The report rides in
``clm slides sync --dry-run --json`` under the ``report`` key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_cmd
from clm.slides.sync_plan import LOCALIZED_CODE_ROLE, PlanIssue, Proposal, SyncPlan
from clm.slides.sync_report import build_report


def _plan(**kw) -> SyncPlan:
    plan = SyncPlan(
        de_path=Path("a.de.py"),
        en_path=Path("a.en.py"),
        baseline_source=kw.pop("baseline_source", "watermark"),
        in_sync_count=kw.pop("in_sync_count", 0),
    )
    plan.proposals = kw.pop("proposals", [])
    plan.issues = kw.pop("issues", [])
    plan.anchor_direction = kw.pop("anchor_direction", None)
    return plan


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------


class TestTierMapping:
    @pytest.mark.parametrize("kind", ["move", "remove", "retag"])
    def test_mechanical_kinds(self, kind):
        report = build_report(
            _plan(proposals=[Proposal(kind=kind, role="slide", direction="de->en", slide_id="s1")])
        )
        assert [i.kind for i in report.mechanical] == [kind]
        assert not report.assisted and not report.ambiguity

    @pytest.mark.parametrize("kind", ["add", "edit", "rename", "mint", "adopt", "reconcile"])
    def test_assisted_kinds(self, kind):
        report = build_report(
            _plan(proposals=[Proposal(kind=kind, role="slide", direction="de->en", slide_id="s1")])
        )
        assert [i.kind for i in report.assisted] == [kind]
        assert not report.mechanical and not report.ambiguity

    def test_conflict_is_ambiguity(self):
        report = build_report(
            _plan(
                proposals=[Proposal(kind="conflict", role="slide", direction=None, slide_id="s1")]
            )
        )
        assert [i.kind for i in report.ambiguity] == ["conflict"]

    def test_refuse_disposition_is_ambiguity(self):
        # A 'refuse' disposition on any kind routes to ambiguity, never executed.
        p = Proposal(
            kind="edit", role="slide", direction="de->en", slide_id="s1", disposition="refuse"
        )
        report = build_report(_plan(proposals=[p]))
        assert [i.kind for i in report.ambiguity] == ["edit"]
        assert not report.assisted

    def test_issues_become_ambiguity_with_severity(self):
        report = build_report(
            _plan(issues=[PlanIssue(severity="error", slide_id="s9", reason="dup id")])
        )
        assert len(report.ambiguity) == 1
        item = report.ambiguity[0]
        assert item.kind == "issue"
        assert item.severity == "error"
        assert item.slide_id == "s9"

    def test_unknown_kind_defaults_to_ambiguity(self):
        # A future, un-categorised kind must surface to the agent, never be silently
        # trusted as a no-model mechanical op.
        report = build_report(
            _plan(
                proposals=[
                    Proposal(kind="future-op", role="slide", direction="de->en", slide_id="s1")
                ]
            )
        )
        assert [i.kind for i in report.ambiguity] == ["future-op"]
        assert not report.mechanical and not report.assisted

    def test_anchor_direction_is_mechanical(self):
        report = build_report(_plan(anchor_direction="en->de"))
        assert [i.kind for i in report.mechanical] == ["neutral-propagate"]
        assert report.mechanical[0].direction == "en->de"

    def test_positions_carried(self):
        p = Proposal(
            kind="edit",
            role="slide",
            direction="de->en",
            slide_id="s1",
            source_position=3,
            target_position=4,
        )
        item = build_report(_plan(proposals=[p])).assisted[0]
        assert (item.source_position, item.target_position) == (3, 4)


# ---------------------------------------------------------------------------
# Computed flags
# ---------------------------------------------------------------------------


class TestComputedFlags:
    def test_clean_plan(self):
        report = build_report(_plan(in_sync_count=10))
        assert report.is_clean is True
        assert report.needs_agent is False
        assert report.needs_model is False

    def test_mechanical_only_needs_nothing(self):
        report = build_report(
            _plan(
                proposals=[Proposal(kind="move", role="slide", direction="de->en", slide_id="s1")]
            )
        )
        assert report.is_clean is False
        assert report.needs_agent is False
        assert report.needs_model is False

    def test_assisted_needs_model_not_agent(self):
        report = build_report(
            _plan(proposals=[Proposal(kind="add", role="slide", direction="de->en", slide_id=None)])
        )
        assert report.needs_model is True
        assert report.needs_agent is False

    def test_ambiguity_needs_agent(self):
        report = build_report(
            _plan(
                proposals=[Proposal(kind="conflict", role="slide", direction=None, slide_id="s1")]
            )
        )
        assert report.needs_agent is True
        assert report.needs_model is True

    def test_computed_flags_serialize(self):
        payload = build_report(_plan(in_sync_count=3)).model_dump(mode="json")
        assert payload["is_clean"] is True
        assert payload["needs_agent"] is False
        assert payload["needs_model"] is False
        assert set(payload) >= {"mechanical", "assisted", "ambiguity", "baseline_source", "in_sync"}


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def _md(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


class TestReportInCli:
    @pytest.fixture
    def cli_runner(self) -> CliRunner:
        try:
            return CliRunner(mix_stderr=False)
        except TypeError:
            return CliRunner()

    def test_dry_run_json_carries_report(self, cli_runner, tmp_path):
        de = "\n".join([_md("de", "s1", "Hallo"), '# %% tags=["keep"]\nprint(1)\n'])
        en = "\n".join([_md("en", "s1", "Hello"), '# %% tags=["keep"]\nprint(1)\n'])
        de_path = tmp_path / "slides_a.de.py"
        en_path = tmp_path / "slides_a.en.py"
        de_path.write_text(de, encoding="utf-8")
        en_path.write_text(en, encoding="utf-8")

        res = cli_runner.invoke(
            slides_sync_cmd, ["--dry-run", "--json", "--no-cache", str(de_path)]
        )
        payload = json.loads(res.output[res.output.find("{") :])
        assert "report" in payload
        report = payload["report"]
        assert set(report) >= {
            "mechanical",
            "assisted",
            "ambiguity",
            "baseline_source",
            "in_sync",
            "is_clean",
            "needs_agent",
            "needs_model",
        }
        # An untracked, mutually-consistent pair has nothing to reconcile.
        assert report["is_clean"] is True
        # The legacy flat plan block is still present (back-compat).
        assert "plan" in payload and "proposals" in payload["plan"]

    def test_dry_run_carries_source_excerpt_for_assisted(self, cli_runner, tmp_path):
        # An id-less added slide on DE is an assisted 'add'; the dry-run resolves its
        # source cell bytes so a delegated model can translate without a Read.
        de = "\n".join(
            [
                _md("de", "s1", "Hallo Welt"),
                '# %% [markdown] lang="de" tags=["slide"]\nEine brandneue Folie\n',
            ]
        )
        en = _md("en", "s1", "Hello World")
        de_path = tmp_path / "deck_x.de.py"
        en_path = tmp_path / "deck_x.en.py"
        de_path.write_text(de, encoding="utf-8")
        en_path.write_text(en, encoding="utf-8")

        res = cli_runner.invoke(
            slides_sync_cmd, ["--dry-run", "--json", "--no-cache", str(de_path)]
        )
        payload = json.loads(res.output[res.output.find("{") :])
        assisted = payload["report"]["assisted"]
        assert [i["kind"] for i in assisted] == ["add"]
        item = assisted[0]
        assert item["source_lang"] == "de"
        assert "Eine brandneue Folie" in item["source_excerpt"]
        assert isinstance(item["source_line"], int)
        # The translated counterpart does not exist yet.
        assert item["target_excerpt"] is None

    def test_apply_mode_omits_excerpts(self, cli_runner, tmp_path, monkeypatch):
        # The resolver indexes the working-tree files, which match the plan's positions
        # only BEFORE an apply mutates them — so an apply run carries no excerpts even
        # though the assisted item is still listed. No key + --no-env-file keeps the add
        # deferred and the run offline.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        de = "\n".join(
            [
                _md("de", "s1", "Hallo Welt"),
                '# %% [markdown] lang="de" tags=["slide"]\nEine brandneue Folie\n',
            ]
        )
        en = _md("en", "s1", "Hello World")
        de_path = tmp_path / "deck_x.de.py"
        en_path = tmp_path / "deck_x.en.py"
        de_path.write_text(de, encoding="utf-8")
        en_path.write_text(en, encoding="utf-8")

        res = cli_runner.invoke(
            slides_sync_cmd, ["--json", "--no-cache", "--no-env-file", str(de_path)]
        )
        payload = json.loads(res.output[res.output.find("{") :])
        assert payload["mode"] == "apply"
        assisted = payload["report"]["assisted"]
        assert [i["kind"] for i in assisted] == ["add"]
        assert assisted[0]["source_excerpt"] is None


# ---------------------------------------------------------------------------
# Cell-text enrichment (dry-run only) — the position -> bytes resolver
# ---------------------------------------------------------------------------


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _idless_code(lang: str, body: str) -> str:
    return f'# %% lang="{lang}" tags=["keep"]\n{body}\n'


def _idd_code(lang: str, sid: str, body: str) -> str:
    # A localized id'd code cell → role_of returns CODE_ROLE ("code").
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


def _pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck_x.de.py"
    en_path = tmp_path / "deck_x.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _real_plan(de_path: Path, en_path: Path, *proposals: Proposal) -> SyncPlan:
    plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
    plan.proposals = list(proposals)
    return plan


class TestCellExcerpts:
    """``build_report(plan, with_excerpts=True)`` resolves positions to cell bytes.

    These directly exercise the risky part — the two position schemes — by handing
    the resolver real files and hand-built proposals with known positions.
    """

    def test_keyed_edit_resolves_both_sides(self, tmp_path):
        de = "\n".join([_slide("de", "s1", "erste Folie"), _slide("de", "s2", "zweite Folie")])
        en = "\n".join([_slide("en", "s1", "first slide"), _slide("en", "s2", "second slide")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="edit",
                role="slide",
                direction="de->en",
                slide_id="s2",
                source_position=1,
                target_position=1,
            ),
        )
        item = build_report(plan, with_excerpts=True).assisted[0]
        assert (item.source_lang, item.target_lang) == ("de", "en")
        assert "zweite Folie" in item.source_excerpt
        assert "second slide" in item.target_excerpt
        assert isinstance(item.source_line, int) and isinstance(item.target_line, int)

    def test_keyed_code_edit_resolves_by_key_not_position(self, tmp_path):
        # A keyed CODE edit has role "code" (the localized-code role), which the
        # positional path would resolve under the *localized* (non-j2) scheme while
        # the cell's position is a *sync* index. An interleaved id-less code cell
        # makes those indices diverge — by-key resolution stays correct. Same root
        # cause as the keyed-conflict fix (#451), kept consistent across kinds.
        de = "\n".join(
            [_idless_code("de", 'print("vorlauf")'), _idd_code("de", "cc", "def de_f(): ...")]
        )
        en = "\n".join(
            [_idless_code("en", 'print("prelude")'), _idd_code("en", "cc", "def en_f(): ...")]
        )
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            # `_edit` would set source_position=1 (sync index of the keyed cell); the
            # positional path under the localized scheme would (wrongly) pick index 1
            # there = the keyed cell on EN side too, but for the SOURCE it would pick
            # localized[1]. Either way an interleave can misalign — by-key cannot.
            Proposal(
                kind="edit",
                role="code",
                direction="de->en",
                slide_id="cc",
                source_position=1,
                target_position=1,
            ),
        )
        item = build_report(plan, with_excerpts=True).assisted[0]
        assert "def de_f()" in item.source_excerpt
        assert "def en_f()" in item.target_excerpt
        assert "vorlauf" not in (item.source_excerpt or "")

    def test_idless_localized_edit_uses_nonj2_scheme(self, tmp_path):
        de = "\n".join([_idless_code("de", 'print("eins")'), _idless_code("de", 'print("zwei")')])
        en = "\n".join([_idless_code("en", 'print("one")'), _idless_code("en", 'print("two")')])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="edit",
                role=LOCALIZED_CODE_ROLE,
                direction="de->en",
                slide_id=None,
                source_position=1,
                target_position=1,
            ),
        )
        item = build_report(plan, with_excerpts=True).assisted[0]
        assert 'print("zwei")' in item.source_excerpt
        assert 'print("two")' in item.target_excerpt

    def test_idless_localized_conflict_resolves_de_source_en_target(self, tmp_path):
        # A both-sided id-less localized conflict carries no direction, but the DE index
        # is in source_position and the EN index in target_position.
        de = "\n".join([_idless_code("de", 'print("eins")'), _idless_code("de", 'print("zwei")')])
        en = "\n".join([_idless_code("en", 'print("one")'), _idless_code("en", 'print("two")')])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="conflict",
                role=LOCALIZED_CODE_ROLE,
                direction=None,
                slide_id=None,
                source_position=0,
                target_position=0,
            ),
        )
        item = build_report(plan, with_excerpts=True).ambiguity[0]
        assert (item.source_lang, item.target_lang) == ("de", "en")
        assert 'print("eins")' in item.source_excerpt
        assert 'print("one")' in item.target_excerpt

    def test_add_resolves_source_only(self, tmp_path):
        de = "\n".join([_slide("de", "s1", "erste Folie")])
        en = "\n".join([_slide("en", "s1", "first slide")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="add",
                role="slide",
                direction="de->en",
                slide_id=None,
                source_position=0,
                target_position=None,
            ),
        )
        item = build_report(plan, with_excerpts=True).assisted[0]
        assert "erste Folie" in item.source_excerpt
        # The translated counterpart does not exist yet — no target excerpt.
        assert item.target_lang == "en"
        assert item.target_excerpt is None and item.target_line is None

    def test_out_of_range_position_yields_no_excerpt(self, tmp_path):
        de = "\n".join([_slide("de", "s1", "erste Folie")])
        en = "\n".join([_slide("en", "s1", "first slide")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="edit",
                role="slide",
                direction="de->en",
                slide_id="s9",
                source_position=99,
                target_position=99,
            ),
        )
        item = build_report(plan, with_excerpts=True).assisted[0]
        # The languages are still known; the bytes are not invented.
        assert item.source_lang == "de"
        assert item.source_excerpt is None and item.target_excerpt is None

    def test_keyed_conflict_resolves_both_sides_by_key(self, tmp_path):
        # Issue #451: a keyed conflict (both halves changed since baseline) now
        # carries both current cells, resolved by (slide_id, role), so an agent can
        # judge whether EN is already a faithful translation of DE.
        de = "\n".join([_slide("de", "s1", "erste Folie"), _slide("de", "s2", "zweite Folie")])
        en = "\n".join([_slide("en", "s1", "first slide"), _slide("en", "s2", "second slide")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(kind="conflict", role="slide", direction=None, slide_id="s2"),
        )
        item = build_report(plan, with_excerpts=True).ambiguity[0]
        assert (item.source_lang, item.target_lang) == ("de", "en")
        assert "zweite Folie" in item.source_excerpt
        assert "second slide" in item.target_excerpt
        assert isinstance(item.source_line, int) and isinstance(item.target_line, int)

    def test_keyed_conflict_code_role_resolves_by_key_not_position(self, tmp_path):
        # A keyed CODE conflict has role "code", which the positional scheme would
        # (wrongly) treat as the *localized* (non-j2) scheme — yet the cell's
        # position is a *sync* index. An interleaved id-less code cell makes those
        # two indices diverge, so a position-based resolve would pick the wrong
        # cell. Resolving by (slide_id, role) is immune. Issue #451.
        de = "\n".join(
            [_idless_code("de", 'print("vorlauf")'), _idd_code("de", "cc", "def de_f(): ...")]
        )
        en = "\n".join(
            [_idless_code("en", 'print("prelude")'), _idd_code("en", "cc", "def en_f(): ...")]
        )
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(kind="conflict", role="code", direction=None, slide_id="cc"),
        )
        item = build_report(plan, with_excerpts=True).ambiguity[0]
        assert "def de_f()" in item.source_excerpt
        assert "def en_f()" in item.target_excerpt
        # The interleaved id-less code must NOT be what we resolved.
        assert "vorlauf" not in (item.source_excerpt or "")
        assert "prelude" not in (item.target_excerpt or "")

    def test_keyed_conflict_with_positions_still_resolves_by_key(self, tmp_path):
        # Invariant guard: even if a (future / erroneous) producer set positions on a
        # keyed conflict, the keyed branch must win — never the position+scheme path,
        # which would mis-select the localized scheme for role "code". The bad
        # positions below point at the interleaved id-less cell under the localized
        # scheme; by-key must ignore them and resolve the id'd cell.
        de = "\n".join(
            [_idless_code("de", 'print("vorlauf")'), _idd_code("de", "cc", "def de_f(): ...")]
        )
        en = "\n".join(
            [_idless_code("en", 'print("prelude")'), _idd_code("en", "cc", "def en_f(): ...")]
        )
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="conflict",
                role="code",
                direction=None,
                slide_id="cc",
                source_position=0,  # localized index 0 = the id-less "vorlauf" cell
                target_position=0,  # localized index 0 = the id-less "prelude" cell
            ),
        )
        item = build_report(plan, with_excerpts=True).ambiguity[0]
        assert "def de_f()" in item.source_excerpt  # resolved by (slide_id, role), not pos
        assert "vorlauf" not in (item.source_excerpt or "")

    def test_item_languages_skips_keyed_conflict(self):
        # The positional path is id-less-only: a keyed conflict (slide_id set), even
        # with positions, must not be claimed by `_item_languages` (it is handled by
        # the by-key branch in `_enrich`). Guards the `_scheme_for("code")` footgun.
        from clm.slides.sync_report import ReconciliationItem, _item_languages

        keyed = ReconciliationItem(
            tier="ambiguity",
            kind="conflict",
            role="code",
            slide_id="cc",
            source_position=0,
            target_position=0,
        )
        assert _item_languages(keyed) == (None, None)
        # The id-less twin (slide_id None) is still resolved positionally.
        idless = ReconciliationItem(
            tier="ambiguity",
            kind="conflict",
            role="code",
            slide_id=None,
            source_position=0,
            target_position=0,
        )
        assert _item_languages(idless) == ("de", "en")

    def test_keyed_conflict_removed_side_has_no_excerpt(self, tmp_path):
        # remove-vs-edit: DE removed the cell, EN still has it. Only the surviving
        # (EN) side carries an excerpt; the removed side stays None.
        de = "\n".join([_slide("de", "s1", "nur DE")])  # s2 removed on DE
        en = "\n".join([_slide("en", "s1", "only EN"), _slide("en", "s2", "second slide")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(kind="conflict", role="slide", direction=None, slide_id="s2"),
        )
        item = build_report(plan, with_excerpts=True).ambiguity[0]
        assert item.source_excerpt is None  # DE removed it
        assert item.target_lang == "en" and "second slide" in item.target_excerpt

    def test_keyed_conflict_missing_cell_stays_unresolved(self, tmp_path):
        # A keyed conflict whose slide_id is in neither half (pathological) yields
        # no excerpt rather than a wrong one.
        de = "\n".join([_slide("de", "s1", "erste Folie")])
        en = "\n".join([_slide("en", "s1", "first slide")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(kind="conflict", role="slide", direction=None, slide_id="ghost"),
        )
        item = build_report(plan, with_excerpts=True).ambiguity[0]
        assert item.source_excerpt is None and item.target_excerpt is None

    def test_mechanical_items_are_not_enriched(self, tmp_path):
        de = "\n".join([_slide("de", "s1", "erste Folie"), _slide("de", "s2", "zweite Folie")])
        en = "\n".join([_slide("en", "s1", "first slide"), _slide("en", "s2", "second slide")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="move",
                role="slide",
                direction="de->en",
                slide_id="s2",
                old_position=0,
                new_position=1,
            ),
        )
        item = build_report(plan, with_excerpts=True).mechanical[0]
        assert item.source_lang is None and item.source_excerpt is None

    def test_with_excerpts_false_resolves_nothing(self, tmp_path):
        de = "\n".join([_slide("de", "s1", "erste Folie"), _slide("de", "s2", "zweite Folie")])
        en = "\n".join([_slide("en", "s1", "first slide"), _slide("en", "s2", "second slide")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="edit",
                role="slide",
                direction="de->en",
                slide_id="s2",
                source_position=1,
                target_position=1,
            ),
        )
        item = build_report(plan).assisted[0]  # default with_excerpts=False
        assert item.source_excerpt is None and item.source_lang is None

    def test_unreadable_files_degrade_to_no_excerpts(self, tmp_path):
        # Missing files must not crash report building — the tiers stay valid.
        plan = _real_plan(
            tmp_path / "absent.de.py",
            tmp_path / "absent.en.py",
            Proposal(
                kind="edit",
                role="slide",
                direction="de->en",
                slide_id="s2",
                source_position=1,
                target_position=1,
            ),
        )
        report = build_report(plan, with_excerpts=True)
        assert len(report.assisted) == 1
        assert report.assisted[0].source_excerpt is None
