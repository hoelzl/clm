"""Issue #403 fix #3 — `clm slides reconcile-vo-ids` voiceover-id symmetrizer.

When a split deck's two halves disagree on whether their paired voiceover cells carry a
`slide_id` (one id-less, one id'd), this command makes them agree by occurrence-under-slide
pairing — strip the id'd side (default) or stamp the id'd side's existing id onto the
id-less side — without ever deriving an id from per-file content (the `assign-ids` hazard).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.reconcile_vo_ids import reconcile_vo_ids_cmd
from clm.slides.reconcile_vo_ids import TO_IDLESS, TO_IDS, reconcile_voiceover_ids


def _title(lang: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="intro"\n# # T\n'


def _code(lang: str, body: str, sid: str) -> str:
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


def _vo(lang: str, body: str, sid: str | None = None) -> str:
    s = f' slide_id="{sid}"' if sid else ""
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"]{s}\n{body}\n'


def _deck(*parts: str) -> str:
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core: reconcile_voiceover_ids
# ---------------------------------------------------------------------------


class TestCore:
    def test_to_idless_strips_the_idd_side(self):
        de = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# Hallo"))
        en = _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# Hello", sid="intro"))
        de_out, en_out, result = reconcile_voiceover_ids(de, en, "#", "#", direction=TO_IDLESS)
        assert de_out == de  # DE already id-less — byte-identical, untouched
        assert 'tags=["voiceover"]\n' in en_out and "slide_id" not in en_out.split("voiceover")[1]
        assert [c.action for c in result.changes] == ["strip"]
        assert result.changes[0].lang == "en"
        assert result.changes[0].old_id == "intro"

    def test_to_ids_stamps_the_twins_existing_id(self):
        de = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# Hallo"))
        en = _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# Hello", sid="intro"))
        de_out, _en_out, result = reconcile_voiceover_ids(de, en, "#", "#", direction=TO_IDS)
        # The DE voiceover gets the EN twin's *existing* id — never a per-file slug.
        assert '# %% [markdown] lang="de" tags=["voiceover"] slide_id="intro"' in de_out
        assert result.changes[0].action == "stamp"
        assert result.changes[0].new_id == "intro"

    def test_both_symmetric_is_noop(self):
        de = _deck(_title("de"), _vo("de", "# Hallo"))
        en = _deck(_title("en"), _vo("en", "# Hello"))
        de_out, en_out, result = reconcile_voiceover_ids(de, en, "#", "#")
        assert result.is_noop and result.already_symmetric == 1
        assert de_out == de and en_out == en  # byte-identical

    def test_only_the_header_line_changes(self):
        # Byte-preservation: stripping an id rewrites exactly the header line, nothing else.
        de = _deck(_title("de"), _vo("de", "# Hallo\n#\n# mehr text"))
        en = _deck(_title("en"), _vo("en", "# Hello\n#\n# more text", sid="intro"))
        _de_out, en_out, _result = reconcile_voiceover_ids(de, en, "#", "#")
        assert "# Hello\n#\n# more text" in en_out  # body verbatim

    def test_occurrence_pairs_several_voiceovers_under_one_slide(self):
        # Two voiceovers under the slide: the n-th DE pairs with the n-th EN.
        de = _deck(
            _title("de"),
            _code("de", "print(1)", "c1"),
            _vo("de", "# eins"),
            _code("de", "print(2)", "c2"),
            _vo("de", "# zwei"),
        )
        en = _deck(
            _title("en"),
            _code("en", "print(1)", "c1"),
            _vo("en", "# one", sid="intro"),
            _code("en", "print(2)", "c2"),
            _vo("en", "# two", sid="intro"),
        )
        _de_out, en_out, result = reconcile_voiceover_ids(de, en, "#", "#")
        assert len(result.changes) == 2  # both EN voiceovers stripped
        # The slide + the two code cells keep their ids; neither voiceover does.
        assert en_out.count("slide_id") == 3
        assert 'voiceover"] slide_id' not in en_out

    def test_unpaired_narrative_is_left_alone(self):
        # A voiceover present on EN only (a genuine add) is not an id-symmetry issue.
        de = _deck(_title("de"), _vo("de", "# Hallo"))
        en = _deck(_title("en"), _vo("en", "# Hello"), _vo("en", "# extra", sid="intro"))
        de_out, en_out, result = reconcile_voiceover_ids(de, en, "#", "#")
        assert result.unpaired == 1
        assert "extra" in en_out and result.is_noop  # the extra EN voiceover untouched
        assert de_out == de

    def test_mirror_direction_de_idd_en_idless(self):
        # The asymmetry can point either way; the id'd side is detected, not assumed.
        de = _deck(_title("de"), _vo("de", "# Hallo", sid="intro"))
        en = _deck(_title("en"), _vo("en", "# Hello"))
        de_out, _en_out, result = reconcile_voiceover_ids(de, en, "#", "#", direction=TO_IDLESS)
        assert result.changes[0].lang == "de" and result.changes[0].action == "strip"
        assert "slide_id" not in de_out.split("voiceover")[1]

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError, match="direction must be"):
            reconcile_voiceover_ids("", "", "#", "#", direction="sideways")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_pair(tmp: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path, en_path = tmp / "deck.de.py", tmp / "deck.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


class TestCli:
    def test_single_half_resolves_twin_and_applies(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _deck(_title("de"), _vo("de", "# Hallo")),
            _deck(_title("en"), _vo("en", "# Hello", sid="intro")),
        )
        res = CliRunner().invoke(reconcile_vo_ids_cmd, [str(de_path)])
        assert res.exit_code == 0, res.output
        assert "slide_id" not in en_path.read_text().split("voiceover")[1]

    def test_dry_run_does_not_write(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _deck(_title("de"), _vo("de", "# Hallo")),
            _deck(_title("en"), _vo("en", "# Hello", sid="intro")),
        )
        before = en_path.read_text()
        res = CliRunner().invoke(reconcile_vo_ids_cmd, [str(de_path), "--dry-run"])
        assert res.exit_code == 0
        assert "would change" in res.output
        assert en_path.read_text() == before  # unchanged

    def test_to_ids_via_cli(self, tmp_path: Path):
        de_path, _en = _write_pair(
            tmp_path,
            _deck(_title("de"), _vo("de", "# Hallo")),
            _deck(_title("en"), _vo("en", "# Hello", sid="intro")),
        )
        res = CliRunner().invoke(reconcile_vo_ids_cmd, [str(de_path), "--to", TO_IDS])
        assert res.exit_code == 0, res.output
        assert 'tags=["voiceover"] slide_id="intro"' in de_path.read_text()

    def test_directory_batch(self, tmp_path: Path):
        for name in ("a", "b"):
            (tmp_path / f"slides_{name}.de.py").write_text(
                _deck(_title("de"), _vo("de", "# Hallo")), encoding="utf-8"
            )
            (tmp_path / f"slides_{name}.en.py").write_text(
                _deck(_title("en"), _vo("en", "# Hello", sid="intro")), encoding="utf-8"
            )
        res = CliRunner().invoke(reconcile_vo_ids_cmd, [str(tmp_path)])
        assert res.exit_code == 0, res.output
        assert "2 pair(s)" in res.output
        assert "slide_id" not in (tmp_path / "slides_a.en.py").read_text().split("voiceover")[1]

    def test_json_output(self, tmp_path: Path):
        import json

        de_path, _en = _write_pair(
            tmp_path,
            _deck(_title("de"), _vo("de", "# Hallo")),
            _deck(_title("en"), _vo("en", "# Hello", sid="intro")),
        )
        res = CliRunner().invoke(reconcile_vo_ids_cmd, [str(de_path), "--json", "--dry-run"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["direction"] == TO_IDLESS
        assert payload["pairs"][0]["changes"][0]["action"] == "strip"

    def test_missing_twin_is_usage_error(self, tmp_path: Path):
        solo = tmp_path / "deck.de.py"
        solo.write_text(_deck(_title("de"), _vo("de", "# Hallo")), encoding="utf-8")
        res = CliRunner().invoke(reconcile_vo_ids_cmd, [str(solo)])
        assert res.exit_code == 2
        assert "no EN twin" in res.output


# ---------------------------------------------------------------------------
# Integration: a reconciled deck syncs cleanly (the whole point of fix #3)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
class TestUnblocksSync:
    def test_reconciled_pair_syncs_without_change(self, tmp_path: Path):
        from clm.infrastructure.llm.cache import SyncWatermarkCache
        from clm.slides.sync_apply import _record_watermark, apply_plan
        from clm.slides.sync_plan import build_sync_plan
        from clm.slides.sync_translate import StaticSlideTranslator

        # Asymmetric pair (DE id-less, EN id'd). Reconcile to id-less, then sync.
        de_path, en_path = _write_pair(
            tmp_path,
            _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# Hallo")),
            _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# Hello", sid="intro")),
        )
        assert CliRunner().invoke(reconcile_vo_ids_cmd, [str(de_path)]).exit_code == 0

        db = tmp_path / "clm-llm.sqlite"
        wm = SyncWatermarkCache(db)
        _record_watermark(wm, de_path, en_path)
        plan = build_sync_plan(de_path, en_path, watermark_cache=wm)
        result = apply_plan(
            plan, judge=None, translator=StaticSlideTranslator(default="<<XL>>"), watermark_cache=wm
        )
        wm.close()
        assert plan.is_noop  # no spurious add/edit after reconciliation
        assert result.errors == []
        assert en_path.read_text().count('tags=["voiceover"]') == 1  # not doubled


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)
