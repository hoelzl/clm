"""Engine tests for ``clm slides rename`` (issue #991): planning refusals,
the atomic move of halves + companions, and the ledger re-key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.slides import doc_ledger
from clm.slides.doc_ledger import deck_key_for, ledger_path_for
from clm.slides.rename_deck import (
    DeckRenameError,
    apply_deck_rename,
    plan_deck_rename,
    validate_new_stem,
)

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"
DE = HEADER_DE + '# %% [markdown] lang="de" tags=["slide"] slide_id="s0"\n#\n# # Titel\n'
EN = HEADER_EN + '# %% [markdown] lang="en" tags=["slide"] slide_id="s0"\n#\n# # Title\n'
VO_DE = '# %% [markdown] lang="de" tags=["notes"] for_slide="s0"\n# Sprich.\n'
VO_EN = '# %% [markdown] lang="en" tags=["notes"] for_slide="s0"\n# Speak.\n'


def _pair(tmp_path: Path, stem: str = "slides_old", ext: str = ".py") -> tuple[Path, Path]:
    de = tmp_path / f"{stem}.de{ext}"
    en = tmp_path / f"{stem}.en{ext}"
    de.write_text(DE, encoding="utf-8")
    en.write_text(EN, encoding="utf-8")
    return de, en


def _companions(tmp_path: Path, stem: str = "old", *, subdir: bool) -> tuple[Path, Path]:
    where = tmp_path / "voiceover" if subdir else tmp_path
    where.mkdir(exist_ok=True)
    de = where / f"voiceover_{stem}.de.py"
    en = where / f"voiceover_{stem}.en.py"
    de.write_text(VO_DE, encoding="utf-8")
    en.write_text(VO_EN, encoding="utf-8")
    return de, en


class TestValidateNewStem:
    def test_bare_stem_accepted(self, tmp_path: Path):
        de, _ = _pair(tmp_path)
        assert validate_new_stem("slides_new", old_half=de) == "slides_new"

    @pytest.mark.parametrize(
        ("stem", "fragment"),
        [
            ("", "non-empty"),
            ("slides_new.py", "source extension"),
            ("slides_new.de", "language tag"),
            ("sub/slides_new", "path separator"),
            ('slides "x"', "not a usable stem"),
            ("slides_old", "already the deck"),
            ("new_without_prefix", "routing prefix"),
        ],
    )
    def test_refusals(self, tmp_path: Path, stem: str, fragment: str):
        de, _ = _pair(tmp_path)
        with pytest.raises(DeckRenameError, match=fragment):
            validate_new_stem(stem, old_half=de)

    def test_unprefixed_deck_may_stay_unprefixed(self, tmp_path: Path):
        """``apis.de.py`` is a legal split half (prefix-agnostic pairing); a
        deck that never had a routing prefix is not forced to gain one."""
        de, _ = _pair(tmp_path, stem="apis")
        assert validate_new_stem("services", old_half=de) == "services"


class TestPlan:
    def test_plans_halves_and_subdir_companions(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        vo_de, vo_en = _companions(tmp_path, subdir=True)
        plan = plan_deck_rename(de, en, "slides_new")
        moves = {m.role: (m.old, m.new) for m in plan.moves}
        assert moves["de"] == (de, tmp_path / "slides_new.de.py")
        assert moves["en"] == (en, tmp_path / "slides_new.en.py")
        assert moves["de_companion"] == (vo_de, tmp_path / "voiceover" / "voiceover_new.de.py")
        assert moves["en_companion"] == (vo_en, tmp_path / "voiceover" / "voiceover_new.en.py")
        assert plan.ledger_has_section is False
        assert any("video-schedule.csv" in w for w in plan.warnings)

    def test_sibling_companions_stay_siblings(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        _companions(tmp_path, subdir=False)
        plan = plan_deck_rename(de, en, "slides_new")
        companions = {m.role: m.new for m in plan.companions}
        assert companions["de_companion"] == tmp_path / "voiceover_new.de.py"
        assert companions["en_companion"] == tmp_path / "voiceover_new.en.py"

    def test_extension_and_prefix_carry_over(self, tmp_path: Path):
        de, en = _pair(tmp_path, stem="topic_old", ext=".cs")
        plan = plan_deck_rename(de, en, "topic_new")
        assert plan.de is not None and plan.de.new.name == "topic_new.de.cs"
        assert plan.en is not None and plan.en.new.name == "topic_new.en.cs"

    def test_prefix_change_is_a_warning_not_a_refusal(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        plan = plan_deck_rename(de, en, "project_new")
        assert any("routing prefix changes" in w for w in plan.warnings)

    def test_target_half_collision_refused(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        (tmp_path / "slides_new.en.py").write_text("x", encoding="utf-8")
        with pytest.raises(DeckRenameError, match="already exists"):
            plan_deck_rename(de, en, "slides_new")

    def test_target_companion_collision_refused(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        _companions(tmp_path, subdir=True)
        (tmp_path / "voiceover" / "voiceover_new.de.py").write_text("x", encoding="utf-8")
        with pytest.raises(DeckRenameError, match="voiceover_new.de.py already exists"):
            plan_deck_rename(de, en, "slides_new")

    def test_companion_in_both_layouts_refused(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        _companions(tmp_path, subdir=True)
        _companions(tmp_path, subdir=False)
        with pytest.raises(DeckRenameError, match="both layouts"):
            plan_deck_rename(de, en, "slides_new")

    def test_single_half_refuses_a_foreign_twin_at_the_target(self, tmp_path: Path):
        """A lone DE half renamed onto a stem whose EN half exists would pair
        with a stranger."""
        de = tmp_path / "slides_old.de.py"
        de.write_text(DE, encoding="utf-8")
        (tmp_path / "slides_new.en.py").write_text(EN, encoding="utf-8")
        with pytest.raises(DeckRenameError, match="would pair"):
            plan_deck_rename(de, None, "slides_new")

    def test_ledger_section_under_new_stem_refused(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        ledger_path = ledger_path_for(de)
        ledger_path.parent.mkdir()
        ledger = doc_ledger.TopicLedger(decks={"slides_new": doc_ledger.DeckLedger()})
        doc_ledger.save(ledger, ledger_path)
        with pytest.raises(DeckRenameError, match="already holds a section"):
            plan_deck_rename(de, en, "slides_new")

    def test_nothing_is_touched_by_planning(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        _companions(tmp_path, subdir=True)
        before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
        plan_deck_rename(de, en, "slides_new")
        after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
        assert before == after


class TestApply:
    def test_moves_everything_and_rekeys_the_ledger(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        vo_de, vo_en = _companions(tmp_path, subdir=True)
        # A recorded section under the old stem, with a recognisable member.
        ledger_path = ledger_path_for(de)
        ledger_path.parent.mkdir()
        ledger_path.write_text(
            json.dumps(
                {
                    "schema": doc_ledger.SCHEMA_VERSION,
                    "decks": {deck_key_for(de): {"members": {}, "group_order": ["s0"]}},
                }
            ),
            encoding="utf-8",
        )
        plan = plan_deck_rename(de, en, "slides_new")
        assert plan.ledger_has_section is True

        used_git, migrated = apply_deck_rename(plan, use_git=False)

        assert used_git is False
        assert migrated is True
        assert not de.exists() and not en.exists()
        assert (tmp_path / "slides_new.de.py").read_text(encoding="utf-8") == DE
        assert (tmp_path / "slides_new.en.py").read_text(encoding="utf-8") == EN
        assert not vo_de.exists() and not vo_en.exists()
        assert (tmp_path / "voiceover" / "voiceover_new.de.py").read_text(encoding="utf-8") == VO_DE
        assert (tmp_path / "voiceover" / "voiceover_new.en.py").read_text(encoding="utf-8") == VO_EN
        after = doc_ledger.load(ledger_path)
        assert set(after.decks) == {"slides_new"}
        assert after.decks["slides_new"].group_order == ["s0"]

    def test_no_ledger_section_means_nothing_to_migrate(self, tmp_path: Path):
        de, en = _pair(tmp_path)
        plan = plan_deck_rename(de, en, "slides_new")
        _, migrated = apply_deck_rename(plan, use_git=False)
        assert migrated is False
        assert not ledger_path_for(de).exists()


class TestRenameDeckKey:
    def test_absent_ledger_or_section_is_a_noop(self, tmp_path: Path):
        path = tmp_path / ".clm" / "sync-ledger.json"
        assert doc_ledger.rename_deck_key(path, "a", "b") is False
        path.parent.mkdir()
        doc_ledger.save(doc_ledger.TopicLedger(decks={"x": doc_ledger.DeckLedger()}), path)
        assert doc_ledger.rename_deck_key(path, "a", "b") is False
        assert set(doc_ledger.load(path).decks) == {"x"}

    def test_rekey_drops_the_old_section(self, tmp_path: Path):
        """The one thing ``save`` cannot express (its merge keeps every disk
        section): after the re-key the old key is gone from the file."""
        path = tmp_path / ".clm" / "sync-ledger.json"
        path.parent.mkdir()
        ledger = doc_ledger.TopicLedger(
            decks={"a": doc_ledger.DeckLedger(group_order=["g"]), "x": doc_ledger.DeckLedger()}
        )
        doc_ledger.save(ledger, path)
        assert doc_ledger.rename_deck_key(path, "a", "b") is True
        after = doc_ledger.load(path)
        assert set(after.decks) == {"b", "x"}
        assert after.decks["b"].group_order == ["g"]

    def test_existing_target_section_refused(self, tmp_path: Path):
        path = tmp_path / ".clm" / "sync-ledger.json"
        path.parent.mkdir()
        ledger = doc_ledger.TopicLedger(
            decks={"a": doc_ledger.DeckLedger(), "b": doc_ledger.DeckLedger()}
        )
        doc_ledger.save(ledger, path)
        with pytest.raises(ValueError, match="already holds"):
            doc_ledger.rename_deck_key(path, "a", "b")
        assert set(doc_ledger.load(path).decks) == {"a", "b"}
