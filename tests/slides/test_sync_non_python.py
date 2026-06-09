"""Engine-level sync tests on a non-Python (C#, ``//``-token) split pair (#289 P4).

The sync engine is comment-token-aware end to end (`comment_token_for_path` is
plumbed through parsing, the watermark, twin building, and cell construction),
and the translator prompts cover C#/C++/Java/TS — but until this file the engine
itself was only ever exercised on ``.de.py`` / ``.en.py`` decks (architecture
review #288, coverage gap (d)). These tests drive the same propagate-or-alert
scenarios through a ``deck.de.cs`` / ``deck.en.cs`` pair: neutral verbatim copy,
id-less localized translation, add-with-insert (the built twin must carry the
``// %%`` header family), tag mirroring, and the #289 neutral tag-drift alert.

Both baselines are exercised (watermark and committed git-HEAD), mirroring the
``test_sync_issue_269`` harness.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.slides.sync_apply import _record_watermark, apply_plan
from clm.slides.sync_plan import build_sync_plan
from clm.slides.sync_translate import StaticSlideTranslator

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


# ---------------------------------------------------------------------------
# C# deck builders (``//`` comment family)
# ---------------------------------------------------------------------------


def _title(lang: str, sid: str = "title", txt: str = "T") -> str:
    return f'// %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n// # {txt}\n'


def _ncode(body: str, tags: str = '["keep"]') -> str:
    return f"// %% tags={tags}\n{body}\n"


def _idless_code(lang: str, body: str, tags: str | None = None) -> str:
    t = f" tags={tags}" if tags else ""
    return f'// %% lang="{lang}"{t}\n{body}\n'


def _idd_code(lang: str, sid: str, body: str) -> str:
    return f'// %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


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
    de_path, en_path = tmp / "deck.de.cs", tmp / "deck.en.cs"
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
    return plan.is_noop and not propagated and not _alerted(plan, result)


BASELINES = ["git-head", "watermark"]


class TestCSharpPair:
    @pytest.mark.parametrize("baseline", BASELINES)
    def test_neutral_code_edit_propagates_verbatim(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _ncode("int x = 1;"))
        en = _deck(_title("en"), _ncode("int x = 1;"))
        en1 = _deck(_title("en"), _ncode("int x = 2;  // EDIT"))
        plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
        assert "int x = 2;  // EDIT" in de_after
        assert not _falsely_consistent(plan, result, True)
        assert result.watermark_recorded

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_idless_localized_edit_is_retranslated(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _idless_code("de", 'Console.WriteLine("hallo");'))
        en = _deck(_title("en"), _idless_code("en", 'Console.WriteLine("hello");'))
        en1 = _deck(_title("en"), _idless_code("en", 'Console.WriteLine("hello there");'))
        plan, result, de_after, _ = _sync(
            tmp_path,
            baseline,
            de,
            en,
            de,
            en1,
            mapping={'Console.WriteLine("hello there");': 'Console.WriteLine("hallo dort");'},
        )
        assert 'Console.WriteLine("hallo dort");' in de_after
        assert not _falsely_consistent(plan, result, True)

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_idless_slide_add_mints_id_and_builds_slash_header(self, tmp_path: Path, baseline: str):
        # The built twin must carry the deck's ``//`` comment family — both the
        # ``// %%`` header (`_build_cell(comment_token=...)`) and the markdown body.
        de = _deck(_title("de"))
        en = _deck(_title("en"))
        en1 = _deck(
            _title("en"),
            '// %% [markdown] lang="en" tags=["slide"]\n// # New Topic\n',
        )
        # The translator receives the RAW percent-format body — comment prefix
        # included (the C#/C++ prompts instruct the model to keep the ``// ``
        # prefix) — so the mapping is keyed on the ``// ``-prefixed line.
        plan, result, de_after, en_after = _sync(
            tmp_path, baseline, de, en, de, en1, mapping={"// # New Topic": "// # Neues Thema"}
        )
        assert result.applied_add == 1
        # EN-authority id minted from the EN heading, stamped on BOTH halves.
        assert 'slide_id="new-topic"' in en_after
        assert 'slide_id="new-topic"' in de_after
        # The DE twin was built with the ``//`` family, not Python's ``#``.
        assert '// %% [markdown] lang="de" tags=["slide"] slide_id="new-topic"' in de_after
        assert "// # Neues Thema" in de_after

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_idd_code_retag_mirrors(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _idd_code("de", "c1", "var a = 1;"))
        en = _deck(_title("en"), _idd_code("en", "c1", "var a = 1;"))
        en1 = _deck(
            _title("en"), '// %% lang="en" tags=["keep", "alt"] slide_id="c1"\nvar a = 1;\n'
        )
        plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
        assert '"alt"' in de_after
        assert result.applied_retag == 1
        assert not _falsely_consistent(plan, result, True)

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_neutral_tag_only_edit_alerts(self, tmp_path: Path, baseline: str):
        # The #289 neutral tag-drift detector on a ``//`` deck (its cell-naming
        # snippet helper must strip the ``// `` prefix, not Python's ``# ``).
        de = _deck(_title("de"), _ncode("int x = 1;"))
        en = _deck(_title("en"), _ncode("int x = 1;"))
        en1 = _deck(_title("en"), _ncode("int x = 1;", tags='["keep", "alt"]'))
        plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
        assert _alerted(plan, result)
        assert result.watermark_recorded is False
        assert '"alt"' not in de_after

    @pytest.mark.parametrize("baseline", BASELINES)
    def test_clean_pair_is_noop(self, tmp_path: Path, baseline: str):
        de = _deck(_title("de"), _ncode("int x = 1;"), _idless_code("de", 'var s = "hallo";'))
        en = _deck(_title("en"), _ncode("int x = 1;"), _idless_code("en", 'var s = "hello";'))
        plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de, en)
        assert plan.is_noop
        assert result.applied == 0
        assert de_after == de and en_after == en  # zero churn
