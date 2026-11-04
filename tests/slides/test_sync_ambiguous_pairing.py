"""A pool whose pairing is a guess is suspended, not framed as a conflict
(#1130, #1144 review).

DE moves both code cells of a slide and edits both; EN moves only the
voiceovers and edits ``c2``. The ledger keeps fingerprints, not bodies, so
nothing tells which edited DE cell is the slot EN edited. Master framed a
``conflict_shared`` on a guessed pair (and livelocked); answering it could
overwrite the other cell's edit. Sync now suspends the pool with an
explanation and every pass leaves all three edits in place.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from clm.slides import doc_apply
from tests.slides import sync_probe as sp
from tests.slides import sync_probe_ext as ext

BASE = ("S0", "a", "c1", "b", "c2", "S1")
DE_ORDER = ("S0", "a", "c1", "c2", "b", "S1")
EN_ORDER = ("S0", "b", "c2", "a", "c1", "S1")


def test_a_guessed_pairing_with_a_both_sided_edit_is_suspended_and_loses_nothing():
    body = {n: sp.base_body(n) for n in BASE if sp.kind(n) == "code"}
    de_body = ext._bodies(body, ("c1", "c2"), "de")
    en_body = ext._bodies(body, ("c2",), "en")
    with tempfile.TemporaryDirectory() as tmp:
        de, en = Path(tmp) / "slides_t.de.py", Path(tmp) / "slides_t.en.py"
        de.write_text(ext._deck("de", BASE, body), encoding="utf-8")
        en.write_text(ext._deck("en", BASE, body), encoding="utf-8")
        sp._record(de, en)
        de.write_text(ext._deck("de", DE_ORDER, de_body), encoding="utf-8")
        en.write_text(ext._deck("en", EN_ORDER, en_body), encoding="utf-8")

        _, diff = sp._diff(de, en)
        code_rows = [i for i in diff.items if "/code/" in i.key]
        assert code_rows, [(i.key, i.action) for i in diff.items]
        assert {i.action for i in code_rows} == {"pool_pairing_shifted"}
        assert all("moved AND edited" in i.detail for i in code_rows)

        for _ in range(3):
            _, diff = sp._diff(de, en)
            decisions = {
                i.key: doc_apply.Decision(key=i.key, choice="en")
                for i in diff.items
                if i.action == "order_decision"
            }
            sp._apply(de, en, decisions)
            de_text = de.read_text(encoding="utf-8")
            en_text = en.read_text(encoding="utf-8")
            for edited in (de_body["c1"], de_body["c2"]):
                assert de_text.count(edited) == 1, de_text
            assert en_text.count(en_body["c2"]) == 1, en_text
