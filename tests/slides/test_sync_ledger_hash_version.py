"""Issue #458: the consistency ledger carries a per-entry ``hash_version``.

A hash-form change (#458 threaded the comment token into markdown hashing) must not
let a stale entry — whose hashes a newer engine would compute differently — be trusted.
``trusts`` requires the current version; a pre-#458 entry (no field) defaults to current
on load, so ``#`` decks (whose hashes are unchanged) keep their trust, while a future
bump cleanly invalidates.
"""

from __future__ import annotations

import json
from pathlib import Path

from clm.infrastructure.llm.cache import WATERMARK_HASH_VERSION
from clm.slides.sync_ledger import LedgerEntry, SyncLedger, load, save


def _entry(**kw) -> LedgerEntry:
    base = {
        "de_hash": "aaa",
        "en_hash": "bbb",
        "construct": None,
        "confirmed_commit": "c1",
        "confirmed_by": "bless",
        "confirmed_oracle": "structural",
    }
    base.update(kw)
    return LedgerEntry(**base)


def test_new_entry_records_current_hash_version(tmp_path: Path) -> None:
    led = SyncLedger()
    led.entries[("intro", "slide")] = _entry()
    path = tmp_path / "sync-ledger.json"
    save(led, path)
    rec = json.loads(path.read_text(encoding="utf-8"))["slides"]["intro"]["slide"]
    assert rec["hash_version"] == WATERMARK_HASH_VERSION
    back = load(path)
    assert back.entries[("intro", "slide")].hash_version == WATERMARK_HASH_VERSION
    assert back.trusts("intro", "slide", "aaa", "bbb")


def test_stale_hash_version_is_not_trusted() -> None:
    led = SyncLedger()
    led.entries[("old", "slide")] = _entry(hash_version=WATERMARK_HASH_VERSION - 1)
    assert not led.trusts("old", "slide", "aaa", "bbb")


def test_pre_versioned_entry_defaults_to_current_on_load(tmp_path: Path) -> None:
    # A ledger written before #458 has no hash_version field; it must load as current
    # (so a # deck, whose hashes did not change, keeps its trust).
    path = tmp_path / "sync-ledger.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "slides": {
                    "intro": {
                        "slide": {
                            "de_hash": "aaa",
                            "en_hash": "bbb",
                            "construct": None,
                            "confirmed_commit": "c1",
                            "confirmed_by": "bless",
                            "confirmed_oracle": "structural",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    back = load(path)
    assert back.entries[("intro", "slide")].hash_version == WATERMARK_HASH_VERSION
    assert back.trusts("intro", "slide", "aaa", "bbb")


def test_idless_entry_round_trips_hash_version(tmp_path: Path) -> None:
    led = SyncLedger()
    led.idless[(None, "voiceover", 0)] = _entry()
    path = tmp_path / "sync-ledger.json"
    save(led, path)
    back = load(path)
    assert back.idless[(None, "voiceover", 0)].hash_version == WATERMARK_HASH_VERSION
    assert back.trusts_idless((None, "voiceover", 0), "aaa", "bbb")
