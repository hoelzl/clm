"""Shadow-mode tests (#520 Phase 2): v2 and v3 over the same git baseline.

Each scenario builds a throwaway git repo, commits the base state, mutates
the working tree, and runs :func:`clm.slides.sync_shadow.shadow_pair` — so
both engines exercise their real baseline paths (v2 ``baseline_ref``
materialization, v3 ``bundle_texts_at_ref`` + snapshot). The verdict
comparison is the migration contract: v3 must flag exactly the genuine
change, correctly classified, wherever v2 flags anything at all.

The full-corpus shadow sweep lives in ``test_sync_diff_corpus.py``
(integration + slow); these scenarios keep the harness honest in the fast
suite.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.slides.sync_shadow import bundle_texts_at_ref, shadow_pair, shadow_scope

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"

DE = (
    HEADER_DE
    + '# %% [markdown] lang="de" tags=["slide"] slide_id="s0"\n#\n# # Titel\n\n'
    + '# %% tags=["keep"]\nx = 1\n\n'
    + '# %% [markdown] lang="de" slide_id="s0-m"\n# DE Text\n'
)
EN = (
    HEADER_EN
    + '# %% [markdown] lang="en" tags=["slide"] slide_id="s0"\n#\n# # Title\n\n'
    + '# %% tags=["keep"]\nx = 1\n\n'
    + '# %% [markdown] lang="en" slide_id="s0-m"\n# EN text\n'
)
DE_C = '# %% [markdown] lang="de" tags=["notes"] for_slide="s0" slide_id="s0-vo"\n#\n# - DE Notiz\n'
EN_C = '# %% [markdown] lang="en" tags=["notes"] for_slide="s0" slide_id="s0-vo"\n#\n# - EN note\n'


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=None,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    topic = tmp_path / "topic_x"
    topic.mkdir()
    (topic / "slides_x.de.py").write_text(DE, encoding="utf-8")
    (topic / "slides_x.en.py").write_text(EN, encoding="utf-8")
    vo = topic / "voiceover"
    vo.mkdir()
    (vo / "voiceover_x.de.py").write_text(DE_C, encoding="utf-8")
    (vo / "voiceover_x.en.py").write_text(EN_C, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _pair(repo_dir: Path):
    topic = repo_dir / "topic_x"
    return topic / "slides_x.de.py", topic / "slides_x.en.py"


class TestBundleTextsAtRef:
    def test_reads_all_four_files(self, repo: Path):
        de_path, en_path = _pair(repo)
        de, en, de_c, en_c = bundle_texts_at_ref(de_path, en_path, "HEAD")
        assert de == DE
        assert en == EN
        assert de_c == DE_C
        assert en_c == EN_C

    def test_absent_companion_is_none(self, repo: Path):
        de_path, en_path = _pair(repo)
        shutil.rmtree(repo / "topic_x" / "voiceover")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "drop companions")
        _, _, de_c, en_c = bundle_texts_at_ref(de_path, en_path, "HEAD")
        assert de_c is None
        assert en_c is None

    def test_unknown_ref_yields_nothing(self, repo: Path):
        de_path, en_path = _pair(repo)
        assert bundle_texts_at_ref(de_path, en_path, "no-such-ref") == (
            None,
            None,
            None,
            None,
        )


class TestShadowScenarios:
    def test_noop_agrees_clean(self, repo: Path):
        pair = shadow_pair(*_pair(repo), "HEAD")
        assert pair.v2_error is None and pair.v3_error is None
        assert pair.agrees_clean, (pair.v2_items, pair.v3 and pair.v3.to_payload())

    def test_shared_edit_both_engines_flag_v3_classifies(self, repo: Path):
        de_path, en_path = _pair(repo)
        de_path.write_text(DE.replace("x = 1", "x = 2"), encoding="utf-8")
        pair = shadow_pair(de_path, en_path, "HEAD")
        assert pair.v2_count >= 1  # v2 flags it (kind/tier vocabulary)
        assert pair.v3 is not None
        assert [(i.action, i.direction) for i in pair.v3.items] == [
            ("propagate_shared_edit", "de_to_en")
        ]

    def test_localized_edit_frames_translation(self, repo: Path):
        de_path, en_path = _pair(repo)
        en_path.write_text(EN.replace("# EN text", "# EN text v2"), encoding="utf-8")
        pair = shadow_pair(de_path, en_path, "HEAD")
        assert pair.v3 is not None
        assert [(i.action, i.direction) for i in pair.v3.items] == [("translate_edit", "en_to_de")]

    def test_companion_edit_is_seen_through_the_ref_bundle(self, repo: Path):
        de_path, en_path = _pair(repo)
        comp = repo / "topic_x" / "voiceover" / "voiceover_x.de.py"
        comp.write_text(DE_C.replace("DE Notiz", "DE Notiz v2"), encoding="utf-8")
        pair = shadow_pair(de_path, en_path, "HEAD")
        assert pair.v3 is not None
        assert [(i.key, i.action) for i in pair.v3.items] == [("id:s0-vo", "translate_edit")]

    def test_v2_crash_does_not_kill_the_sweep(self, repo: Path, monkeypatch):
        import clm.slides.sync_plan as sync_plan

        def boom(*args, **kwargs):
            raise RuntimeError("v2 exploded")

        monkeypatch.setattr(sync_plan, "build_sync_plan", boom)
        pair = shadow_pair(*_pair(repo), "HEAD")
        assert pair.v2_error is not None and "v2 exploded" in pair.v2_error
        assert pair.v3 is not None  # v3 still ran

    def test_scope_sweeps_a_directory(self, repo: Path):
        report = shadow_scope(repo, "HEAD")
        assert len(report.pairs) == 1
        summary = report.summary()
        assert summary["pairs"] == 1
        assert summary["agree_clean"] == 1
        payload = report.to_payload()
        assert payload["schema"] == 3
        assert payload["mode"] == "shadow"

    def test_render_text_names_engines_and_totals(self, repo: Path):
        de_path, en_path = _pair(repo)
        de_path.write_text(DE.replace("x = 1", "x = 3"), encoding="utf-8")
        report = shadow_scope(repo, "HEAD")
        text = report.render_text()
        assert "v2=" in text and "v3=" in text
        assert "propagate_shared_edit" in text
        assert "TOTAL:" in text
