"""Regression tests for Issue #289 — tag-channel propagate-or-alert.

The 2026-06-09 architecture review (``docs/claude/sync-engine-architecture-assessment.md``
§2.1, probes in ``scripts/sync_matrix_probes.py``) found three silent drops, all in
the **tag metadata channel** — the one channel the body-hash detectors are blind to:

- a one-sided tag-only edit on an **id-less localized** cell under a **git-HEAD**
  baseline (Tier C was gated ``source == "watermark"``) — now **mirrored** (P1);
- a one-sided tag-only edit on a **language-neutral shared** cell, under either
  baseline (recorded by the watermark, read by nothing) — now **alerted** (P9);
- a tag-only id-less retag under a concurrent group reorder (#285) — was dropped
  *and* the watermark advanced over the loss — now **alerted, watermark held** (P5).

This file also promotes the review's clean probes (id-less localized remove,
intra-group neutral reorder, id-less localized add × git-HEAD) into committed
tests, and pins the **channel-coverage meta-test**: every channel the watermark
records must name a live detector or fail-safe, so the next recorded channel
cannot ship uncovered.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import clm.slides.sync_apply as sync_apply_mod
import clm.slides.sync_plan as sync_plan_mod
from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.slides.sync_apply import _record_watermark, apply_plan
from clm.slides.sync_plan import build_sync_plan
from clm.slides.sync_translate import StaticSlideTranslator

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


# ---------------------------------------------------------------------------
# Harness (the test_sync_issue_269 pattern: baseline -> one-sided edit -> sync)
# ---------------------------------------------------------------------------


def _title(lang: str, sid: str = "title", txt: str = "T") -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n# # {txt}\n'


def _slide(lang: str, sid: str, txt: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n# # {txt}\n'


def _ncode(body: str, tags: str = '["keep"]') -> str:
    return f"# %% tags={tags}\n{body}\n"


def _idless_code(lang: str, body: str, tags: str | None = None) -> str:
    t = f" tags={tags}" if tags else ""
    return f'# %% lang="{lang}"{t}\n{body}\n'


def _idless_md(lang: str, body: str, tags: str | None = None) -> str:
    t = f" tags={tags}" if tags else ""
    return f'# %% [markdown] lang="{lang}"{t}\n{body}\n'


def _vo(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="{sid}"\n{body}\n'


def _deck(*parts: str) -> str:
    return "\n".join(parts)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _sync(
    tmp: Path,
    baseline: str,
    de0: str,
    en0: str,
    de1: str,
    en1: str,
    *,
    mapping: dict[str, str] | None = None,
):
    db = tmp / "clm-llm.sqlite"
    de_path, en_path = tmp / "deck.de.py", tmp / "deck.en.py"
    de_path.write_text(de0, encoding="utf-8")
    en_path.write_text(en0, encoding="utf-8")
    if baseline == "git-head":
        _git(tmp, "init", "-q")
        _git(tmp, "config", "user.email", "t@example.com")
        _git(tmp, "config", "user.name", "Test")
        _git(tmp, "add", "-A")
        _git(tmp, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    else:
        wm = SyncWatermarkCache(db)
        _record_watermark(wm, de_path, en_path)
        wm.close()
    de_path.write_text(de1, encoding="utf-8")
    en_path.write_text(en1, encoding="utf-8")
    translator = StaticSlideTranslator(mapping=mapping or {}, default="<<XL>>")
    wm = SyncWatermarkCache(db)
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=wm)
        result = apply_plan(plan, judge=None, translator=translator, watermark_cache=wm)
    finally:
        wm.close()
    return plan, result, de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")


def _alerted(plan, result) -> bool:
    return (
        plan.has_errors
        or result.has_errors
        or result.deferred > 0
        or any(i.severity == "error" for i in plan.issues)
    )


def _falsely_consistent(plan, result, propagated: bool) -> bool:
    """The forbidden state: reported consistent while a change was NOT handled."""
    return plan.is_noop and not propagated and not _alerted(plan, result)


BASELINES = ["git-head", "watermark"]


# ---------------------------------------------------------------------------
# Id-less localized retag — now mirrored under BOTH baselines (#289 P1)
# ---------------------------------------------------------------------------


class TestIdlessRetagBothBaselines:
    @pytest.mark.parametrize("baseline", BASELINES)
    def test_idless_code_tag_add_is_mirrored(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _idless_code("de", 'print("hallo")'))
        en = _deck(_title("en"), _idless_code("en", 'print("hello")'))
        en1 = _deck(_title("en"), _idless_code("en", 'print("hello")', tags='["keep"]'))
        plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
        assert '# %% lang="de" tags=["keep"]' in de_after  # mirrored onto the DE twin
        assert result.applied_retag == 1
        assert result.watermark_recorded
        assert not _falsely_consistent(plan, result, True)

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_idless_markdown_tag_add_is_mirrored(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _idless_md("de", "# Hinweis"))
        en = _deck(_title("en"), _idless_md("en", "# Note"))
        de1 = _deck(_title("de"), _idless_md("de", "# Hinweis", tags='["alt"]'))
        plan, result, _, en_after = _sync(tmp_path, baseline, de, en, de1, en)
        assert '# %% [markdown] lang="en" tags=["alt"]' in en_after
        assert result.applied_retag == 1
        assert not _falsely_consistent(plan, result, True)


# ---------------------------------------------------------------------------
# Neutral shared cell, tag-only edit — alerted, never dropped (#289 P9)
# ---------------------------------------------------------------------------


class TestNeutralTagDrift:
    @pytest.mark.parametrize("baseline", BASELINES)
    def test_one_sided_neutral_tag_edit_alerts(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _ncode("import os"))
        en = _deck(_title("en"), _ncode("import os"))
        en1 = _deck(_title("en"), _ncode("import os", tags='["keep", "alt"]'))
        plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de, en1)
        assert _alerted(plan, result)
        assert not _falsely_consistent(plan, result, False)
        assert result.watermark_recorded is False  # never baseline the divergence
        assert '"alt"' not in de_after  # nothing written (buffered flush held)
        assert '"alt"' in en_after  # the author's own edit is intact

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_both_sides_updated_identically_is_clean(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _ncode("import os"))
        en = _deck(_title("en"), _ncode("import os"))
        de1 = _deck(_title("de"), _ncode("import os", tags='["keep", "alt"]'))
        en1 = _deck(_title("en"), _ncode("import os", tags='["keep", "alt"]'))
        plan, result, _, _ = _sync(tmp_path, baseline, de, en, de1, en1)
        assert not _alerted(plan, result)
        assert plan.is_noop

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_body_plus_tag_edit_propagates_both_no_false_alert(self, tmp_path: Path, baseline: str):
        """A combined body+tag edit rides the structural rebuild (header copied
        verbatim), so the tag-drift detector must NOT fire on it."""
        de = _deck(_title("de"), _ncode("import os"))
        en = _deck(_title("en"), _ncode("import os"))
        en1 = _deck(_title("en"), _ncode("import os  # EDIT", tags='["keep", "alt"]'))
        plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
        assert "import os  # EDIT" in de_after  # body propagated
        assert '"alt"' in de_after  # tags rode along on the verbatim header copy
        assert not _alerted(plan, result)
        assert result.watermark_recorded

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_neutral_tag_drift_alert_is_reorder_invariant(self, tmp_path: Path, baseline: str):
        """A one-sided group reorder + an opposite-half neutral tag edit must alert
        (group-keyed comparison), not mis-pair or drop."""
        de0 = _deck(_slide("de", "a", "A"), _ncode("import os"), _slide("de", "b", "B"))
        en0 = _deck(_slide("en", "a", "A"), _ncode("import os"), _slide("en", "b", "B"))
        # DE: tag-only edit on its neutral cell. EN: reorders the groups.
        de1 = _deck(
            _slide("de", "a", "A"),
            _ncode("import os", tags='["keep", "alt"]'),
            _slide("de", "b", "B"),
        )
        en1 = _deck(_slide("en", "b", "B"), _slide("en", "a", "A"), _ncode("import os"))
        plan, result, _, en_after = _sync(tmp_path, baseline, de0, en0, de1, en1)
        assert _alerted(plan, result)
        assert result.watermark_recorded is False
        assert '"alt"' not in en_after  # nothing silently healed over


# ---------------------------------------------------------------------------
# Id-less retag under a concurrent move — MIRRORED via the baseline twin (#285)
# ---------------------------------------------------------------------------


class TestIdlessRetagUnderMove:
    def test_target_half_tag_edit_under_move_is_mirrored(self, tmp_path: Path):
        """#285 closed: pre-#289 this silently dropped (and baselined the loss);
        #290 alerted; the baseline-twin route now MIRRORS the tag across the
        reorder — both changes land, clean pass, watermark advances."""
        de0 = _deck(
            _slide("de", "a", "A"), _idless_code("de", 'print("hallo")'), _slide("de", "b", "B")
        )
        en0 = _deck(
            _slide("en", "a", "A"), _idless_code("en", 'print("hello")'), _slide("en", "b", "B")
        )
        # DE: tag-only edit on its id-less cell. EN: reorders the groups.
        de1 = _deck(
            _slide("de", "a", "A"),
            _idless_code("de", 'print("hallo")', tags='["keep"]'),
            _slide("de", "b", "B"),
        )
        en1 = _deck(
            _slide("en", "b", "B"), _slide("en", "a", "A"), _idless_code("en", 'print("hello")')
        )
        plan, result, de_after, en_after = _sync(tmp_path, "watermark", de0, en0, de1, en1)
        assert not _alerted(plan, result)
        assert result.applied_retag == 1
        assert '# %% lang="en" tags=["keep"]' in en_after  # tag mirrored onto the EN twin
        assert 'tags=["keep"]' in de_after  # the author's own edit intact
        assert result.applied_move >= 1  # the reorder applied too
        assert de_after.index('slide_id="b"') < de_after.index('slide_id="a"')  # mirrored to DE
        assert result.watermark_recorded
        assert not _falsely_consistent(plan, result, True)

    def test_source_half_tag_edit_under_own_move_is_mirrored(self, tmp_path: Path):
        """The reordering half tag-edits its own id-less cell — same route, the
        twin is located on the unreordered half."""
        de0 = _deck(
            _slide("de", "a", "A"), _idless_code("de", 'print("hallo")'), _slide("de", "b", "B")
        )
        en0 = _deck(
            _slide("en", "a", "A"), _idless_code("en", 'print("hello")'), _slide("en", "b", "B")
        )
        de1 = _deck(
            _slide("de", "b", "B"),
            _slide("de", "a", "A"),
            _idless_code("de", 'print("hallo")', tags='["keep"]'),
        )
        plan, result, _, en_after = _sync(tmp_path, "watermark", de0, en0, de1, en0)
        assert not _alerted(plan, result)
        assert result.applied_retag == 1
        assert '# %% lang="en" tags=["keep"]' in en_after
        assert result.watermark_recorded

    def test_duplicate_body_under_move_alerts(self, tmp_path: Path):
        """Two byte-identical id-less cells defeat the hash anchor — the mirror
        declines and ALERTS (never guesses a twin, never drops)."""
        de0 = _deck(
            _slide("de", "a", "A"),
            _idless_code("de", 'print("x")'),
            _slide("de", "b", "B"),
            _idless_code("de", 'print("x")'),
        )
        en0 = _deck(
            _slide("en", "a", "A"),
            _idless_code("en", 'print("x")'),
            _slide("en", "b", "B"),
            _idless_code("en", 'print("x")'),
        )
        de1 = _deck(
            _slide("de", "a", "A"),
            _idless_code("de", 'print("x")', tags='["keep"]'),
            _slide("de", "b", "B"),
            _idless_code("de", 'print("x")'),
        )
        en1 = _deck(
            _slide("en", "b", "B"),
            _idless_code("en", 'print("x")'),
            _slide("en", "a", "A"),
            _idless_code("en", 'print("x")'),
        )
        plan, result, _, en_after = _sync(tmp_path, "watermark", de0, en0, de1, en1)
        assert _alerted(plan, result)
        assert result.watermark_recorded is False
        assert 'tags=["keep"]' not in en_after

    def test_both_sided_tag_drift_under_move_alerts(self, tmp_path: Path):
        """Both twins' tags drifted — a conflict no direction can resolve."""
        de0 = _deck(
            _slide("de", "a", "A"), _idless_code("de", 'print("hallo")'), _slide("de", "b", "B")
        )
        en0 = _deck(
            _slide("en", "a", "A"), _idless_code("en", 'print("hello")'), _slide("en", "b", "B")
        )
        de1 = _deck(
            _slide("de", "a", "A"),
            _idless_code("de", 'print("hallo")', tags='["keep"]'),
            _slide("de", "b", "B"),
        )
        en1 = _deck(
            _slide("en", "b", "B"),
            _slide("en", "a", "A"),
            _idless_code("en", 'print("hello")', tags='["alt"]'),
        )
        plan, result, de_after, en_after = _sync(tmp_path, "watermark", de0, en0, de1, en1)
        assert _alerted(plan, result)
        assert result.watermark_recorded is False
        assert '"alt"' not in de_after and '"keep"' not in en_after  # neither overwritten

    def test_move_with_concurrent_remove_alerts_not_mirrors(self, tmp_path: Path):
        """A coexisting remove reshapes the stream the retag applier targets —
        the mirror declines to today's alert rather than retag a shifted cell."""
        de0 = _deck(
            _slide("de", "a", "A"),
            _idless_code("de", 'print("hallo")'),
            _slide("de", "b", "B"),
            _vo("de", "b", "# N"),
        )
        en0 = _deck(
            _slide("en", "a", "A"),
            _idless_code("en", 'print("hello")'),
            _slide("en", "b", "B"),
            _vo("en", "b", "# N"),
        )
        # DE: tag edit. EN: reorders groups AND removes the voiceover companion.
        de1 = _deck(
            _slide("de", "a", "A"),
            _idless_code("de", 'print("hallo")', tags='["keep"]'),
            _slide("de", "b", "B"),
            _vo("de", "b", "# N"),
        )
        en1 = _deck(
            _slide("en", "b", "B"), _slide("en", "a", "A"), _idless_code("en", 'print("hello")')
        )
        plan, result, _, en_after = _sync(tmp_path, "watermark", de0, en0, de1, en1)
        assert _alerted(plan, result)
        assert result.watermark_recorded is False
        assert 'tags=["keep"]' not in en_after


# ---------------------------------------------------------------------------
# Promoted clean probes (review §2.1 P3 / P4 / P6 — previously uncovered cells)
# ---------------------------------------------------------------------------


class TestPromotedCleanProbes:
    @pytest.mark.parametrize("baseline", BASELINES)
    def test_idless_localized_remove_propagates(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _idless_code("de", 'print("hallo")'), _ncode("import os"))
        en = _deck(_title("en"), _idless_code("en", 'print("hello")'), _ncode("import os"))
        en1 = _deck(_title("en"), _ncode("import os"))
        plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
        assert "hallo" not in de_after  # the removal mirrored to DE
        assert not _falsely_consistent(plan, result, True)

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_intra_group_neutral_reorder_propagates(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _ncode("import os"), _ncode("import sys"))
        en = _deck(_title("en"), _ncode("import os"), _ncode("import sys"))
        en1 = _deck(_title("en"), _ncode("import sys"), _ncode("import os"))
        plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
        assert de_after.find("import sys") < de_after.find("import os")
        assert not _falsely_consistent(plan, result, True)

    def test_idless_localized_add_propagates_git_head(self, tmp_path: Path):
        de = _deck(_title("de"), _ncode("import os"))
        en = _deck(_title("en"), _ncode("import os"))
        en1 = _deck(_title("en"), _ncode("import os"), _idless_code("en", 'print("new")'))
        plan, result, de_after, _ = _sync(
            tmp_path, "git-head", de, en, de, en1, mapping={'print("new")': 'print("neu")'}
        )
        assert "neu" in de_after
        assert not _falsely_consistent(plan, result, True)


# ---------------------------------------------------------------------------
# Channel-coverage meta-test (#289): no recorded channel without a named check
# ---------------------------------------------------------------------------

# Every (partition, field) the watermark records, mapped to the functions that
# keep its propagate-or-alert invariant. Adding a partition or widening the row
# tuple in ``_record_watermark`` fails this test until the new channel is
# registered here WITH a live detector or fail-safe — the #289 class closer
# (the P9 drop was exactly a recorded-but-unconsumed channel: shared tags).
_ROW_FIELDS = ("position", "slide_id", "role", "content_hash", "construct")

CHANNEL_COVERAGE: dict[tuple[str, str], list[tuple[object, str]]] = {
    # body hashes: the keyed diff + the anchor passes + the post-apply parity nets
    ("de", "content_hash"): [
        (sync_plan_mod, "classify_changes"),
        (sync_plan_mod, "_classify_idless_localized_drift"),
        (sync_apply_mod, "_flag_idless_localized_divergence"),
    ],
    ("en", "content_hash"): [
        (sync_plan_mod, "classify_changes"),
        (sync_plan_mod, "_classify_idless_localized_drift"),
        (sync_apply_mod, "_flag_idless_localized_divergence"),
    ],
    ("shared", "content_hash"): [
        (sync_plan_mod, "align_anchored"),
        (sync_apply_mod, "_flag_shared_cell_divergence"),
    ],
    # order: move detection + the structural/order fail-safes
    ("de", "position"): [(sync_plan_mod, "_moved_keys")],
    ("en", "position"): [(sync_plan_mod, "_moved_keys")],
    ("shared", "position"): [
        (sync_plan_mod, "align_anchored"),
        (sync_apply_mod, "_flag_shared_cell_divergence"),
    ],
    # identity
    ("de", "slide_id"): [(sync_plan_mod, "classify_changes")],
    ("en", "slide_id"): [(sync_plan_mod, "classify_changes")],
    ("shared", "slide_id"): [(sync_apply_mod, "_migrate_drifted_ids")],
    ("de", "role"): [(sync_plan_mod, "classify_changes")],
    ("en", "role"): [(sync_plan_mod, "classify_changes")],
    ("shared", "role"): [(sync_apply_mod, "_shared_region")],
    # construct anchors (id-migration + verbatim reuse)
    ("de", "construct"): [(sync_apply_mod, "_baseline_anchor_hashes")],
    ("en", "construct"): [(sync_apply_mod, "_baseline_anchor_hashes")],
    ("shared", "construct"): [(sync_apply_mod, "_migrate_drifted_ids")],
    # tags (#198 / #289): keyed retag, Tier C (+ its under-move alert), neutral drift
    ("de", "tags"): [
        (sync_plan_mod, "_maybe_retag"),
        (sync_plan_mod, "_classify_localized_idless_retags"),
        (sync_plan_mod, "_classify_idless_retags_under_move"),
    ],
    ("en", "tags"): [
        (sync_plan_mod, "_maybe_retag"),
        (sync_plan_mod, "_classify_localized_idless_retags"),
        (sync_plan_mod, "_classify_idless_retags_under_move"),
    ],
    # narrative anchors (#403 Phase B): the anchor classifier keys voiceover/notes by
    # (owning_slide_id, role, anchor) and detects edit/add/remove/conflict, and the
    # apply locates the twin by anchor — so the recorded anchor channel is consumed.
    ("de", "anchor"): [
        (sync_plan_mod, "_classify_narratives"),
        (sync_apply_mod, "_find_narrative_cell"),
    ],
    ("en", "anchor"): [
        (sync_plan_mod, "_classify_narratives"),
        (sync_apply_mod, "_find_narrative_cell"),
    ],
    ("shared", "tags"): [
        (sync_plan_mod, "_classify_neutral_tag_drift"),
        (sync_apply_mod, "_flag_shared_cell_divergence"),
    ],
    # j2 deck headers (#269): alert-only by design
    ("de-header", "content_hash"): [(sync_plan_mod, "_classify_header_drift")],
    ("en-header", "content_hash"): [(sync_plan_mod, "_classify_header_drift")],
    ("de-header", "position"): [(sync_plan_mod, "_classify_header_drift")],
    ("en-header", "position"): [(sync_plan_mod, "_classify_header_drift")],
    ("de-header", "slide_id"): [(sync_plan_mod, "_classify_header_drift")],
    ("en-header", "slide_id"): [(sync_plan_mod, "_classify_header_drift")],
    ("de-header", "role"): [(sync_plan_mod, "_classify_header_drift")],
    ("en-header", "role"): [(sync_plan_mod, "_classify_header_drift")],
    ("de-header", "construct"): [(sync_plan_mod, "_classify_header_drift")],
    ("en-header", "construct"): [(sync_plan_mod, "_classify_header_drift")],
}


class _RecordingCache:
    """Captures exactly which channels ``_record_watermark`` writes."""

    def __init__(self) -> None:
        self.channels: set[tuple[str, str]] = set()

    def put_deck(self, *, de_path, en_path, lang, cells, tags=None, anchors=None):  # noqa: ANN001
        for row in cells:
            assert len(row) == len(_ROW_FIELDS), (
                "watermark row widened — register the new field's detector/fail-safe "
                "in CHANNEL_COVERAGE (test_sync_tag_drift.py) before recording it"
            )
            for field in _ROW_FIELDS:
                self.channels.add((lang, field))
        if tags is not None:
            self.channels.add((lang, "tags"))
        if anchors is not None:
            self.channels.add((lang, "anchor"))

    def set_synced_commit(self, de_path, en_path, commit):  # noqa: ANN001
        # Pair-level metadata (Fix D), not a per-cell channel — nothing to record here.
        pass


class TestChannelCoverage:
    def test_every_recorded_channel_has_a_named_check(self, tmp_path: Path):
        de_path = tmp_path / "deck.de.py"
        en_path = tmp_path / "deck.en.py"
        # One cell of every class, so every partition records rows.
        de_path.write_text(
            _deck(
                '# j2 from \'macros.j2\' import header_de\n# {{ header_de("T", "01") }}\n',
                _title("de"),
                _ncode("import os"),
                _idless_code("de", 'print("hallo")', tags='["keep"]'),
            ),
            encoding="utf-8",
        )
        en_path.write_text(
            _deck(
                '# j2 from \'macros.j2\' import header_en\n# {{ header_en("T", "01") }}\n',
                _title("en"),
                _ncode("import os"),
                _idless_code("en", 'print("hello")', tags='["keep"]'),
            ),
            encoding="utf-8",
        )
        cache = _RecordingCache()
        _record_watermark(cache, de_path, en_path)  # type: ignore[arg-type]

        unregistered = sorted(cache.channels - set(CHANNEL_COVERAGE))
        assert not unregistered, (
            f"watermark records channel(s) {unregistered} with no registered "
            "detector/fail-safe — a recorded-but-unconsumed channel is exactly how "
            "the #289 tag drops shipped; add the covering check, then register it"
        )
        for channel in cache.channels:
            for module, name in CHANNEL_COVERAGE[channel]:
                assert hasattr(module, name), (
                    f"channel {channel} names {name}, which no longer exists — "
                    "update CHANNEL_COVERAGE to the function that now owns it"
                )
