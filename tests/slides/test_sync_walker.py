"""Tests for :mod:`clm.slides.sync_walker`."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncSnapshotCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.slides.sync import SyncOptions, sync_split_pair
from clm.slides.sync_walker import (
    APPLY,
    EDIT,
    QUIT,
    SKIP,
    WalkerOptions,
    run_interactive_walker,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _slide_cell(*, lang: str, slide_id: str, body: str, tag: str = "slide") -> str:
    return f'# %% [markdown] lang="{lang}" tags=["{tag}"] slide_id="{slide_id}"\n{body.strip()}\n'


def _write_pair(
    tmp_path: Path,
    *,
    de: str,
    en: str,
    stem: str = "slides_intro",
) -> tuple[Path, Path]:
    de_path = tmp_path / f"{stem}.de.py"
    en_path = tmp_path / f"{stem}.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _scripted_prompt(answers: list[str]):
    """Return a prompt_fn that yields each answer in order."""
    iterator: Iterator[str] = iter(answers)

    def prompt(_msg: str) -> str:
        return next(iterator)

    return prompt


def _propose(text: str, *, reason: str = "") -> SyncProposal:
    return SyncProposal(verdict="update", proposed_text=text, reason=reason)


def _run_sync_with_proposal(
    tmp_path: Path,
    *,
    de_body: str,
    en_body: str,
    proposal: SyncProposal,
    source_lang: str = "de",
):
    de_cell = _slide_cell(lang="de", slide_id="intro", body=de_body)
    en_cell = _slide_cell(lang="en", slide_id="intro", body=en_body)
    de_path, en_path = _write_pair(tmp_path, de=de_cell, en=en_cell)

    judge = StaticSyncJudge(default_proposal=proposal)
    result = sync_split_pair(de_path, en_path, SyncOptions(source_lang=source_lang, judge=judge))
    return de_path, en_path, result


# ---------------------------------------------------------------------------
# Action handling
# ---------------------------------------------------------------------------


class TestApply:
    def test_apply_writes_target_file_and_bumps_counter(self, tmp_path: Path):
        de_path, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## Einleitung\n# - Punkt eins\n# - Punkt zwei",
            en_body="# ## Introduction\n# - Point one",
            proposal=_propose("# ## Introduction\n# - Point one\n# - Point two"),
        )

        actions = run_interactive_walker(
            result,
            WalkerOptions(prompt_fn=_scripted_prompt(["a"])),
        )

        assert len(actions) == 1
        assert actions[0].action == APPLY
        assert actions[0].target_path == en_path
        assert result.pairs_accepted == 1
        assert result.pairs_skipped == 0

        en_text = en_path.read_text(encoding="utf-8")
        assert "Point two" in en_text
        # DE file is untouched.
        assert de_path.read_text(encoding="utf-8").endswith("Punkt zwei\n")

    def test_apply_preserves_header_and_trailing_blank(self, tmp_path: Path):
        de_path, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## Einleitung\n#\n# - Punkt eins",
            en_body="# ## Introduction\n#\n# - alt bullet",
            proposal=_propose("# ## Introduction\n#\n# - Point one"),
        )

        before_header = en_path.read_text(encoding="utf-8").splitlines()[0]
        run_interactive_walker(
            result,
            WalkerOptions(prompt_fn=_scripted_prompt(["a"])),
        )

        text = en_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        # Header byte-identical.
        assert lines[0] == before_header
        # File still ends with a trailing newline (cell-boundary handoff).
        assert text.endswith("\n")
        assert "Point one" in text

    def test_apply_en_to_de_writes_de_file(self, tmp_path: Path):
        de_path, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## Einleitung\n# - alt",
            en_body="# ## Introduction\n# - new bullet",
            proposal=_propose("# ## Einleitung\n# - neuer Punkt"),
            source_lang="en",
        )

        run_interactive_walker(
            result,
            WalkerOptions(prompt_fn=_scripted_prompt(["a"])),
        )

        assert "neuer Punkt" in de_path.read_text(encoding="utf-8")
        # EN file untouched.
        assert "new bullet" in en_path.read_text(encoding="utf-8")


class TestSkip:
    def test_skip_leaves_target_unchanged(self, tmp_path: Path):
        _, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## Einleitung\n# - x",
            en_body="# ## Introduction\n# - y",
            proposal=_propose("# ## Introduction\n# - changed"),
        )

        before = en_path.read_text(encoding="utf-8")
        actions = run_interactive_walker(
            result,
            WalkerOptions(prompt_fn=_scripted_prompt(["s"])),
        )

        assert actions[0].action == SKIP
        assert result.pairs_skipped == 1
        assert result.pairs_accepted == 0
        assert en_path.read_text(encoding="utf-8") == before


class TestEdit:
    def test_edit_replaces_text_with_editor_output(self, tmp_path: Path):
        _, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## Einleitung\n# - x",
            en_body="# ## Introduction\n# - y",
            proposal=_propose("# ## Introduction\n# - llm suggestion"),
        )

        def fake_edit(seed: str) -> str:
            assert "llm suggestion" in seed
            return "# ## Introduction\n# - human override"

        actions = run_interactive_walker(
            result,
            WalkerOptions(
                prompt_fn=_scripted_prompt(["e"]),
                edit_fn=fake_edit,
            ),
        )

        assert actions[0].action == EDIT
        assert result.pairs_edited == 1
        text = en_path.read_text(encoding="utf-8")
        assert "human override" in text
        assert "llm suggestion" not in text

    def test_edit_no_save_falls_back_to_skip(self, tmp_path: Path):
        _, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## Einleitung",
            en_body="# ## Introduction",
            proposal=_propose("# ## Introduction (new)"),
        )

        before = en_path.read_text(encoding="utf-8")
        actions = run_interactive_walker(
            result,
            WalkerOptions(
                prompt_fn=_scripted_prompt(["e"]),
                edit_fn=lambda _seed: None,
            ),
        )

        assert actions[0].action == SKIP
        assert result.pairs_skipped == 1
        assert result.pairs_edited == 0
        assert en_path.read_text(encoding="utf-8") == before


class TestQuit:
    def test_quit_stops_walker_and_counts_remaining(self, tmp_path: Path):
        # Two updates pending; quitting on the first leaves the second
        # unvisited but accounted for.
        de_cells = _slide_cell(lang="de", slide_id="a", body="# ## A\n# - x") + _slide_cell(
            lang="de", slide_id="b", body="# ## B\n# - x"
        )
        en_cells = _slide_cell(lang="en", slide_id="a", body="# ## A\n# - old") + _slide_cell(
            lang="en", slide_id="b", body="# ## B\n# - old"
        )
        de_path, en_path = _write_pair(tmp_path, de=de_cells, en=en_cells)

        judge = StaticSyncJudge(default_proposal=_propose("# ## new\n# - new"))
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))
        before = en_path.read_text(encoding="utf-8")

        actions = run_interactive_walker(
            result,
            WalkerOptions(prompt_fn=_scripted_prompt(["q"])),
        )

        assert len(actions) == 2  # both got telemetry rows
        assert actions[0].action == QUIT
        assert actions[1].action == QUIT
        assert result.pairs_quit == 2
        assert result.pairs_accepted == 0
        assert en_path.read_text(encoding="utf-8") == before


class TestPromptRetry:
    def test_unknown_input_reprompts(self, tmp_path: Path):
        _, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## A",
            en_body="# ## A current",
            proposal=_propose("# ## A new"),
        )

        actions = run_interactive_walker(
            result,
            WalkerOptions(
                prompt_fn=_scripted_prompt(["zzz", "?", "apply"]),
            ),
        )

        assert actions[0].action == APPLY
        assert "A new" in en_path.read_text(encoding="utf-8")

    def test_skip_on_empty_input(self, tmp_path: Path):
        _, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## A",
            en_body="# ## A current",
            proposal=_propose("# ## A new"),
        )

        before = en_path.read_text(encoding="utf-8")
        actions = run_interactive_walker(
            result,
            WalkerOptions(prompt_fn=_scripted_prompt([""])),
        )

        assert actions[0].action == SKIP
        assert en_path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Filtering: only "update" outcomes are walked
# ---------------------------------------------------------------------------


class TestVerdictFiltering:
    def test_in_sync_outcomes_skipped(self, tmp_path: Path):
        # The pair is in-sync — no prompt should fire.
        _, _, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## A",
            en_body="# ## A",
            proposal=SyncProposal(verdict="in_sync", proposed_text="# ## A"),
        )

        sentinel: list[str] = []

        def trip(_msg: str) -> str:
            sentinel.append("called")
            return "s"

        actions = run_interactive_walker(
            result,
            WalkerOptions(prompt_fn=trip),
        )

        assert actions == []
        assert sentinel == []  # prompt never fired

    def test_error_outcomes_skipped(self, tmp_path: Path):
        de_cell = _slide_cell(lang="de", slide_id="intro", body="# ## A")
        en_cell = _slide_cell(lang="en", slide_id="intro", body="# ## A")
        de_path, en_path = _write_pair(tmp_path, de=de_cell, en=en_cell)
        # No judge → every pair becomes an error.
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=None))

        actions = run_interactive_walker(
            result,
            WalkerOptions(prompt_fn=_scripted_prompt(["a"])),
        )

        assert actions == []
        assert result.pairs_accepted == 0


# ---------------------------------------------------------------------------
# Snapshot writes
# ---------------------------------------------------------------------------


class TestSnapshotCache:
    @pytest.fixture
    def snapshot(self, tmp_path: Path):
        c = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            yield c
        finally:
            c.close()

    def test_apply_writes_snapshot_row(self, tmp_path, snapshot):
        de_path, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## Einleitung",
            en_body="# ## Introduction",
            proposal=_propose("# ## Introduction (new)"),
        )

        run_interactive_walker(
            result,
            WalkerOptions(
                prompt_fn=_scripted_prompt(["a"]),
                snapshot_cache=snapshot,
            ),
        )

        row = snapshot.get(str(de_path), str(en_path), "intro", "slide")
        assert row is not None
        de_hash, en_hash, direction = row
        assert direction == "de->en"
        assert de_hash and en_hash  # both non-empty

    def test_skip_does_not_write_snapshot(self, tmp_path, snapshot):
        de_path, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## A",
            en_body="# ## B",
            proposal=_propose("# ## new"),
        )

        run_interactive_walker(
            result,
            WalkerOptions(
                prompt_fn=_scripted_prompt(["s"]),
                snapshot_cache=snapshot,
            ),
        )

        assert snapshot.get(str(de_path), str(en_path), "intro", "slide") is None

    def test_edit_writes_snapshot_with_post_edit_hash(self, tmp_path, snapshot):
        de_path, en_path, result = _run_sync_with_proposal(
            tmp_path,
            de_body="# ## Einleitung",
            en_body="# ## Introduction",
            proposal=_propose("# ## proposed"),
        )

        run_interactive_walker(
            result,
            WalkerOptions(
                prompt_fn=_scripted_prompt(["e"]),
                edit_fn=lambda _seed: "# ## human edit",
                snapshot_cache=snapshot,
            ),
        )

        row = snapshot.get(str(de_path), str(en_path), "intro", "slide")
        assert row is not None
        # On-disk EN now matches the human edit; recompute the hash and
        # compare to the row.
        import hashlib

        expected_en_hash = hashlib.sha256(b"# ## human edit").hexdigest()
        _de_hash, en_hash, _direction = row
        assert en_hash == expected_en_hash


# ---------------------------------------------------------------------------
# Multiple updates on the same file
# ---------------------------------------------------------------------------


class TestMultipleAccepts:
    def test_two_accepts_on_same_target_file(self, tmp_path: Path):
        de_cells = _slide_cell(lang="de", slide_id="a", body="# ## A") + _slide_cell(
            lang="de", slide_id="b", body="# ## B"
        )
        en_cells = _slide_cell(lang="en", slide_id="a", body="# ## A old") + _slide_cell(
            lang="en", slide_id="b", body="# ## B old"
        )
        _, en_path = _write_pair(tmp_path, de=de_cells, en=en_cells)

        judge = StaticSyncJudge(default_proposal=_propose("# ## new"))
        result = sync_split_pair(
            tmp_path / "slides_intro.de.py",
            tmp_path / "slides_intro.en.py",
            SyncOptions(source_lang="de", judge=judge),
        )

        actions = run_interactive_walker(
            result,
            WalkerOptions(prompt_fn=_scripted_prompt(["a", "a"])),
        )

        assert [a.action for a in actions] == [APPLY, APPLY]
        assert result.pairs_accepted == 2
        text = en_path.read_text(encoding="utf-8")
        # Both targets replaced; both header lines preserved.
        assert text.count("# ## new") == 2
        assert 'slide_id="a"' in text and 'slide_id="b"' in text
