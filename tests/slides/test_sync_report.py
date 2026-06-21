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
from clm.slides.sync_plan import PlanIssue, Proposal, SyncPlan
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
