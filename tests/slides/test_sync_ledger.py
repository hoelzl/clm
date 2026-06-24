"""Issue #448 P1 — the per-slide consistency ledger (trust overlay).

Covers the pure model (load/save/trusts), the gated write path (``record_pair``),
the overlay post-pass (``_apply_ledger_overlay``), and the end-to-end behaviour
through ``build_sync_plan``: a slide recorded in-sync is not re-litigated against an
older baseline, while a slide that drifted since its confirmation still surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides import sync_ledger
from clm.slides.sync_ledger import (
    LEDGER_FILENAME,
    LedgerEntry,
    SyncLedger,
    ledger_path_for,
    load,
    record_pair,
    save,
)
from clm.slides.sync_plan import (
    Proposal,
    SyncPlan,
    _apply_ledger_overlay,
    build_sync_plan,
    ordered_sync_cells,
)


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _write_pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck.de.py"
    en_path = tmp_path / "deck.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _seed_watermark(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    for lang, path in (("de", de_path), ("en", en_path)):
        cells = ordered_sync_cells(parse_cells(path.read_text(encoding="utf-8")), lang)
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=[(c.position, c.slide_id, c.role, c.content_hash, c.construct) for c in cells],
        )


# ---------------------------------------------------------------------------
# Model: path / save / load / trusts
# ---------------------------------------------------------------------------


def test_ledger_path_is_under_clm(tmp_path: Path) -> None:
    de_path, _en = _write_pair(tmp_path, _slide("de", "s", "x"), _slide("en", "s", "y"))
    assert ledger_path_for(de_path) == tmp_path / ".clm" / LEDGER_FILENAME


def test_save_load_roundtrip_canonical(tmp_path: Path) -> None:
    led = SyncLedger()
    led.entries[("s2", "slide")] = LedgerEntry("d2", "e2", None, "c2", "bless", "structural")
    led.entries[("s1", "slide")] = LedgerEntry("d1", "e1", "fn", "c1", "apply", "structural")
    path = tmp_path / ".clm" / LEDGER_FILENAME
    save(led, path)
    # Canonical: schema present, slide keys sorted (s1 before s2).
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["schema"] == sync_ledger.SCHEMA_VERSION
    assert list(payload["slides"]) == ["s1", "s2"]
    assert payload["slides"]["s1"]["slide"]["construct"] == "fn"
    # Round-trips back to the same entries.
    assert load(path).entries == led.entries


def test_load_missing_or_malformed_is_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.json").entries == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load(bad).entries == {}
    wrong_schema = tmp_path / "old.json"
    wrong_schema.write_text('{"schema": 99, "slides": {}}', encoding="utf-8")
    assert load(wrong_schema).entries == {}


def test_trusts_exact_match_only() -> None:
    led = SyncLedger()
    led.entries[("s1", "slide")] = LedgerEntry("dh", "eh", None, "c", "apply", "structural")
    assert led.trusts("s1", "slide", "dh", "eh")
    assert not led.trusts("s1", "slide", "dh", "OTHER")  # en drifted
    assert not led.trusts("s1", "slide", "OTHER", "eh")  # de drifted
    assert not led.trusts("s1", "voiceover", "dh", "eh")  # wrong role
    assert not led.trusts("nope", "slide", "dh", "eh")  # no entry


# ---------------------------------------------------------------------------
# record_pair — the gated write path
# ---------------------------------------------------------------------------


def test_record_pair_writes_localized_entries(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    result = record_pair(de_path, en_path, confirmed_by="bless", commit="abc123")
    assert not result.refused
    assert result.recorded == 1
    led = load(result.path)
    entry = led.entries[("s1", "slide")]
    assert entry.confirmed_by == "bless"
    assert entry.confirmed_oracle == "structural"
    assert entry.confirmed_commit == "abc123"
    # The recorded hashes are the current halves' reflow-insensitive content hashes.
    de_cur = ordered_sync_cells(parse_cells(de_path.read_text(encoding="utf-8")), "de")
    en_cur = ordered_sync_cells(parse_cells(en_path.read_text(encoding="utf-8")), "en")
    assert entry.de_hash == de_cur[0].content_hash
    assert entry.en_hash == en_cur[0].content_hash


def test_record_pair_refuses_structural_corruption(tmp_path: Path) -> None:
    # A mismatched slide_id (de s1, en s2) is an id-asymmetry the structural gate fails.
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s2", "Hello")
    )
    result = record_pair(de_path, en_path, confirmed_by="bless")
    assert result.refused
    assert result.reasons
    assert not result.path.exists()  # nothing written on refusal


def test_record_pair_merges_other_decks_entries(tmp_path: Path) -> None:
    # An existing ledger entry for a slide NOT in this pair is preserved.
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    path = ledger_path_for(de_path)
    seed = SyncLedger()
    seed.entries[("other", "slide")] = LedgerEntry("d", "e", None, "c", "apply", "structural")
    save(seed, path)
    record_pair(de_path, en_path, confirmed_by="apply", commit="z")
    led = load(path)
    assert ("other", "slide") in led.entries  # preserved
    assert ("s1", "slide") in led.entries  # added


# ---------------------------------------------------------------------------
# Overlay post-pass — kind restriction
# ---------------------------------------------------------------------------


def test_overlay_suppresses_edit_but_not_structural(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    de_cur = ordered_sync_cells(parse_cells(de_path.read_text(encoding="utf-8")), "de")
    en_cur = ordered_sync_cells(parse_cells(en_path.read_text(encoding="utf-8")), "en")
    led = SyncLedger()
    led.entries[("s1", "slide")] = LedgerEntry(
        de_cur[0].content_hash, en_cur[0].content_hash, None, "c", "bless", "structural"
    )
    plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
    plan.proposals = [
        Proposal(kind="edit", role="slide", direction="de->en", slide_id="s1"),
        Proposal(kind="move", role="slide", direction="de->en", slide_id="s1"),
    ]
    skipped = _apply_ledger_overlay(plan, led, de_cur, en_cur)
    assert skipped == 1
    # The content edit is gone; the structural move survives (ledger certifies content).
    assert [p.kind for p in plan.proposals] == ["move"]


# ---------------------------------------------------------------------------
# End-to-end through build_sync_plan
# ---------------------------------------------------------------------------


def test_ledger_suppresses_relitigation_against_old_baseline(tmp_path: Path) -> None:
    # Baseline (watermark) records the OLD de body; then de is edited. Against the
    # watermark the classifier emits an edit. Recording the ledger at the CURRENT state
    # and re-planning with that ledger suppresses the edit (slide byte-stable since sync).
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    cache = SyncWatermarkCache(tmp_path / "wm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        de_path.write_text(_slide("de", "s1", "Hallo erweitert"), encoding="utf-8")

        # Without the ledger: the edit surfaces.
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        assert any(p.kind == "edit" and p.slide_id == "s1" for p in plan.proposals)

        # Record the ledger at the current (post-edit) state, then re-plan with it.
        record_pair(de_path, en_path, confirmed_by="bless", commit="now")
        led = load(ledger_path_for(de_path))
        plan2 = build_sync_plan(
            de_path, en_path, watermark_cache=cache, allow_git_fallback=False, ledger=led
        )
        assert [p for p in plan2.proposals if p.kind == "edit"] == []
        assert plan2.ledger_skipped == 1
    finally:
        cache.close()


def test_ledger_does_not_suppress_drift_since_confirmation(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    cache = SyncWatermarkCache(tmp_path / "wm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        # Confirm at this state, then edit AGAIN — the new body is not the recorded one.
        record_pair(de_path, en_path, confirmed_by="bless", commit="now")
        led = load(ledger_path_for(de_path))
        de_path.write_text(_slide("de", "s1", "Hallo NEU"), encoding="utf-8")
        plan = build_sync_plan(
            de_path, en_path, watermark_cache=cache, allow_git_fallback=False, ledger=led
        )
        assert any(p.kind == "edit" and p.slide_id == "s1" for p in plan.proposals)
        assert plan.ledger_skipped == 0
    finally:
        cache.close()


# ---------------------------------------------------------------------------
# CLI surface — bless --ledger writes; report/apply --ledger consult; batch refuses
# ---------------------------------------------------------------------------


def _cli():
    from clm.cli.main import cli

    return cli


def test_cli_bless_ledger_writes_and_reports(tmp_path: Path) -> None:
    from click.testing import CliRunner

    de_path, _en = _write_pair(tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello"))
    res = CliRunner().invoke(
        _cli(),
        [
            "slides",
            "sync",
            "baseline",
            "bless",
            str(de_path),
            "--ledger",
            "--cache-dir",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "ledger: 1 slide(s) confirmed in-sync" in res.output
    assert ledger_path_for(de_path.resolve()).is_file()


def test_cli_bless_ledger_json(tmp_path: Path) -> None:
    from click.testing import CliRunner

    de_path, _en = _write_pair(tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello"))
    res = CliRunner().invoke(
        _cli(),
        [
            "slides",
            "sync",
            "baseline",
            "bless",
            str(de_path),
            "--ledger",
            "--json",
            "--cache-dir",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output[res.output.index("{") : res.output.rindex("}") + 1])
    assert payload["ledger_recorded"] == 1


def test_cli_report_ledger_skips_relitigation(tmp_path: Path) -> None:
    from click.testing import CliRunner

    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    # Seed the watermark at the original state, edit, then bless --ledger to confirm the
    # current state. A report --baseline against the original watermark would re-litigate
    # the edit, but the ledger (matching current) suppresses it.
    (tmp_path / ".clm-cache").mkdir()
    cache = SyncWatermarkCache(tmp_path / ".clm-cache" / "clm-llm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
    finally:
        cache.close()
    de_path.write_text(_slide("de", "s1", "Hallo erweitert"), encoding="utf-8")
    record_pair(de_path, en_path, confirmed_by="bless", commit="now")

    runner = CliRunner()
    common = ["--use-watermark", "--cache-dir", str(tmp_path / ".clm-cache")]
    without = runner.invoke(_cli(), ["slides", "sync", "report", str(de_path), *common])
    with_ledger = runner.invoke(
        _cli(), ["slides", "sync", "report", str(de_path), "--ledger", *common]
    )
    assert without.exit_code == 1  # an edit is pending
    assert with_ledger.exit_code == 0  # the ledger trusts the slide → clean
    assert "skipped 1 slide" in with_ledger.output

    # The skip count is also in the JSON contract (so a --json consumer can tell
    # "0 real changes" from "N trusted away" — not falsely consistent).
    as_json = runner.invoke(
        _cli(), ["slides", "sync", "report", str(de_path), "--ledger", "--json", *common]
    )
    payload = json.loads(as_json.output[as_json.output.index("{") : as_json.output.rindex("}") + 1])
    assert payload["plan"]["ledger_skipped"] == 1


# ---------------------------------------------------------------------------
# apply --ledger auto-writes on a fully-clean pass (the "emit the watermark as
# the ledger" follow-up). Records nothing when residue remains.
# ---------------------------------------------------------------------------


def _seed_cache(tmp_path: Path, de_path: Path, en_path: Path) -> Path:
    """Seed the watermark at the current state in a CLI-resolvable cache dir."""
    cache_dir = tmp_path / ".clm-cache"
    cache_dir.mkdir(exist_ok=True)
    cache = SyncWatermarkCache(cache_dir / "clm-llm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
    finally:
        cache.close()
    return cache_dir


def test_cli_apply_ledger_records_on_clean_pass(tmp_path: Path) -> None:
    from click.testing import CliRunner

    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    cache_dir = _seed_cache(tmp_path, de_path, en_path)  # in sync → a clean apply
    res = CliRunner().invoke(
        _cli(),
        ["slides", "sync", "apply", str(de_path), "--ledger", "--cache-dir", str(cache_dir)],
    )
    assert res.exit_code == 0, res.output
    assert "recorded 1 slide(s) confirmed in-sync (confirmed_by=apply)" in res.output
    led = load(ledger_path_for(de_path))
    assert led.entries[("s1", "slide")].confirmed_by == "apply"


def test_cli_apply_ledger_skips_record_with_residue(tmp_path: Path) -> None:
    from click.testing import CliRunner

    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    cache_dir = _seed_cache(tmp_path, de_path, en_path)
    # A localized edit on DE is model residue for the deterministic apply (deferred),
    # so the deck is NOT fully reconciled → the ledger must record nothing.
    de_path.write_text(_slide("de", "s1", "Hallo erweitert"), encoding="utf-8")
    res = CliRunner().invoke(
        _cli(),
        ["slides", "sync", "apply", str(de_path), "--ledger", "--cache-dir", str(cache_dir)],
    )
    assert res.exit_code == 1  # residue pending
    assert "recorded" not in res.output  # nothing banked
    assert not ledger_path_for(de_path).exists()  # no ledger written


def test_cli_apply_ledger_json_block(tmp_path: Path) -> None:
    from click.testing import CliRunner

    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    cache_dir = _seed_cache(tmp_path, de_path, en_path)
    res = CliRunner().invoke(
        _cli(),
        [
            "slides",
            "sync",
            "apply",
            str(de_path),
            "--ledger",
            "--json",
            "--cache-dir",
            str(cache_dir),
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output[res.output.index("{") : res.output.rindex("}") + 1])
    assert payload["ledger"] == {"skipped": 0, "recorded": 1}


# ---------------------------------------------------------------------------
# Batch --ledger over a directory (report overlay + apply auto-write per pair)
# ---------------------------------------------------------------------------


def _make_batch(tmp_path: Path) -> tuple[Path, Path, list[tuple[Path, Path]]]:
    """A course root with two in-sync slide-pair topics + a seeded watermark cache.

    Returns ``(root, cache_dir, pairs)``. Files are named ``slides_NNN.*`` so the
    batch walk (``find_split_slide_files_recursive``) discovers them.
    """
    from clm.slides.pairing import find_split_slide_files_recursive, iter_split_pairs

    root = tmp_path / "course"
    for i in range(1, 3):
        topic = root / f"topic_{i}"
        topic.mkdir(parents=True)
        (topic / f"slides_{i:03d}.de.py").write_text(
            _slide("de", f"s{i}", "Hallo"), encoding="utf-8"
        )
        (topic / f"slides_{i:03d}.en.py").write_text(
            _slide("en", f"s{i}", "Hello"), encoding="utf-8"
        )
    pairs, _solos = iter_split_pairs(find_split_slide_files_recursive(root))
    cache_dir = tmp_path / ".clm-cache"
    cache_dir.mkdir()
    cache = SyncWatermarkCache(cache_dir / "clm-llm.sqlite")
    try:
        for de, en in pairs:
            _seed_watermark(cache, de, en)
    finally:
        cache.close()
    return root, cache_dir, pairs


def test_cli_report_ledger_batch_overlays_per_pair(tmp_path: Path) -> None:
    from click.testing import CliRunner

    root, cache_dir, pairs = _make_batch(tmp_path)
    de_a, en_a = pairs[0]
    # Drift pair A's DE, then record the ledger at the current state (watermark stays
    # at the original) — so against the watermark A would `edit`, but the ledger trusts it.
    de_a.write_text(_slide("de", "s1", "Hallo erweitert"), encoding="utf-8")
    record_pair(de_a, en_a, confirmed_by="bless", commit="now")

    runner = CliRunner()
    common = ["--use-watermark", "--cache-dir", str(cache_dir)]
    # Without --ledger the drifted pair needs review (exit 1).
    without = runner.invoke(_cli(), ["slides", "sync", "report", str(root), *common])
    assert without.exit_code == 1
    # With --ledger A is trusted → both pairs clean → exit 0, and no UsageError (batch ok).
    with_ledger = runner.invoke(
        _cli(), ["slides", "sync", "report", str(root), "--ledger", *common]
    )
    assert with_ledger.exit_code == 0, with_ledger.output
    assert "1 slide(s) skipped" in with_ledger.output


def test_cli_apply_ledger_batch_records_each_pair(tmp_path: Path) -> None:
    from click.testing import CliRunner

    root, cache_dir, pairs = _make_batch(tmp_path)  # both in sync
    res = CliRunner().invoke(
        _cli(),
        [
            "slides",
            "sync",
            "apply",
            str(root),
            "--ledger",
            "--yes",
            "--json",
            "--cache-dir",
            str(cache_dir),
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output[res.output.index("{") : res.output.rindex("}") + 1])
    assert payload["ledger"] == {"skipped": 0, "recorded": 2}  # one slide per pair
    for de, _en in pairs:
        assert ledger_path_for(de).is_file()


# ---------------------------------------------------------------------------
# Seeding the ledger from the existing watermark (baseline seed / --seed, §11.5)
# ---------------------------------------------------------------------------


def test_seed_from_watermark_stamps_assume(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    cache = SyncWatermarkCache(tmp_path / "wm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        cache.set_synced_commit(str(de_path), str(en_path), "deadbeef")
        result = sync_ledger.seed_from_watermark(de_path, en_path, cache)
    finally:
        cache.close()
    assert not result.refused
    assert result.recorded == 1
    entry = load(ledger_path_for(de_path)).entries[("s1", "slide")]
    assert entry.confirmed_oracle == "assume"
    assert entry.confirmed_by == "seed"
    assert entry.confirmed_commit == "deadbeef"
    # The seeded hashes are the watermark's (== current, since the deck is unchanged).
    de_cur = ordered_sync_cells(parse_cells(de_path.read_text(encoding="utf-8")), "de")
    assert entry.de_hash == de_cur[0].content_hash


def test_seed_is_stale_safe_drifted_slide_not_trusted(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    cache = SyncWatermarkCache(tmp_path / "wm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        sync_ledger.seed_from_watermark(de_path, en_path, cache)
    finally:
        cache.close()
    # Drift DE after seeding: the seeded entry holds the OLD hash, so the slide is no
    # longer trusted at its current halves — it re-checks (never a silent mis-sync).
    de_path.write_text(_slide("de", "s1", "Hallo NEU"), encoding="utf-8")
    led = load(ledger_path_for(de_path))
    new_de = ordered_sync_cells(parse_cells(de_path.read_text(encoding="utf-8")), "de")[0]
    en_cur = ordered_sync_cells(parse_cells(en_path.read_text(encoding="utf-8")), "en")[0]
    assert not led.trusts("s1", "slide", new_de.content_hash, en_cur.content_hash)


def test_seed_fill_gaps_only_never_downgrades(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    # A real structural confirmation already exists; seeding must not downgrade it.
    pre = SyncLedger()
    pre.entries[("s1", "slide")] = LedgerEntry("dh", "eh", None, "c", "bless", "structural")
    save(pre, ledger_path_for(de_path))
    cache = SyncWatermarkCache(tmp_path / "wm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        result = sync_ledger.seed_from_watermark(de_path, en_path, cache)
    finally:
        cache.close()
    assert result.recorded == 0  # the only slide already had a real entry
    entry = load(ledger_path_for(de_path)).entries[("s1", "slide")]
    assert entry.confirmed_oracle == "structural"  # not downgraded to assume


def test_seed_no_watermark_records_nothing(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "s1", "Hallo"), _slide("en", "s1", "Hello")
    )
    cache = SyncWatermarkCache(tmp_path / "wm.sqlite")  # never seeded
    try:
        result = sync_ledger.seed_from_watermark(de_path, en_path, cache)
    finally:
        cache.close()
    assert result.recorded == 0
    assert not ledger_path_for(de_path).exists()


def test_cli_baseline_seed_single_and_directory(tmp_path: Path) -> None:
    from click.testing import CliRunner

    root, cache_dir, pairs = _make_batch(tmp_path)  # two in-sync pairs + seeded watermark
    de_a, _en_a = pairs[0]
    runner = CliRunner()
    # Single pair.
    single = runner.invoke(
        _cli(), ["slides", "sync", "baseline", "seed", str(de_a), "--cache-dir", str(cache_dir)]
    )
    assert single.exit_code == 0, single.output
    assert load(ledger_path_for(de_a)).entries[("s1", "slide")].confirmed_oracle == "assume"
    # Directory (the other pair) via JSON.
    batch = runner.invoke(
        _cli(),
        ["slides", "sync", "baseline", "seed", str(root), "--cache-dir", str(cache_dir), "--json"],
    )
    assert batch.exit_code == 0, batch.output
    payload = json.loads(batch.output[batch.output.index("{") : batch.output.rindex("}") + 1])
    # Pair A already seeded (fill-gaps → 0), pair B newly seeded (1) → total 1.
    assert payload["seeded"] == 1
