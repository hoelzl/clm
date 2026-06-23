"""Tests for clm.slides.tidy (the ``clm slides tidy`` reorg command)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from clm.slides.tidy import (
    CASSETTE_LEGACY_SUBDIR,
    CASSETTE_SUBDIR,
    apply_tidy,
    plan_tidy,
)


def _touch(p: Path, text: str = "x\n") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _flat_topic(tmp_path: Path) -> Path:
    t = tmp_path / "topic_070"
    _touch(t / "slides_010.de.py", "# de\n")
    _touch(t / "slides_010.en.py", "# en\n")
    _touch(t / "voiceover_010.de.py", '# %% tags=["voiceover"]\n')
    _touch(t / "voiceover_010.en.py", '# %% tags=["voiceover"]\n')
    _touch(t / "slides_010.de.http-cassette.yaml", "interactions: []\n")
    _touch(t / "slides_010.de.http-cassette.yaml.staging-123-abc", "partial\n")
    _touch(t / "slides_010.de.http-cassette.yaml.staging-123-abc.completed", "")
    return t


def test_plan_subdir_moves_and_deletes(tmp_path):
    t = _flat_topic(tmp_path)
    plan = plan_tidy(t, layout="subdir")
    moved = {(m.src.name, m.dst.relative_to(t).as_posix()) for m in plan.moves}
    assert ("voiceover_010.de.py", "voiceover/voiceover_010.de.py") in moved
    assert ("voiceover_010.en.py", "voiceover/voiceover_010.en.py") in moved
    assert (
        "slides_010.de.http-cassette.yaml",
        ".clm/cassettes/slides_010.de.http-cassette.yaml",
    ) in moved
    # core slide sources are never moved
    assert not any(m.src.name.startswith("slides_") and m.src.suffix == ".py" for m in plan.moves)
    # both transient staging markers are queued for deletion
    assert len(plan.deletes) == 2
    assert all(".staging-" in f.name for f in plan.deletes)


def test_apply_subdir_no_git(tmp_path):
    t = _flat_topic(tmp_path)
    apply_tidy(plan_tidy(t, layout="subdir"), use_git=False)
    assert (t / "voiceover" / "voiceover_010.de.py").exists()
    assert (t / ".clm" / "cassettes" / "slides_010.de.http-cassette.yaml").exists()
    assert not (t / "voiceover_010.de.py").exists()
    assert not (t / "slides_010.de.http-cassette.yaml").exists()
    assert not list(t.glob("*.staging-*"))  # markers deleted
    assert (t / "slides_010.de.py").exists()  # core sources untouched


def test_flatten_round_trips_and_prunes_dirs(tmp_path):
    t = _flat_topic(tmp_path)
    apply_tidy(plan_tidy(t, layout="subdir"), use_git=False)
    apply_tidy(plan_tidy(t, layout="sibling"), use_git=False)  # flatten back
    assert (t / "voiceover_010.de.py").exists()
    assert (t / "slides_010.de.http-cassette.yaml").exists()
    # emptied sidecar dirs are removed — including the .clm/cassettes/ home and the
    # now-empty .clm/ left behind by the cassette flatten (#453).
    assert not (t / "voiceover").exists()
    assert not (t / ".clm" / "cassettes").exists()
    assert not (t / ".clm").exists()


def test_idempotent(tmp_path):
    t = _flat_topic(tmp_path)
    apply_tidy(plan_tidy(t, layout="subdir"), use_git=False)
    assert plan_tidy(t, layout="subdir").is_noop  # nothing left to do


def test_legacy_cassettes_consolidated(tmp_path):
    t = tmp_path / "topic_070"
    _touch(t / "slides_010.py", "# x\n")
    _touch(t / CASSETTE_LEGACY_SUBDIR / "slides_010.http-cassette.yaml", "interactions: []\n")
    apply_tidy(plan_tidy(t, layout="subdir"), use_git=False)
    assert (t / CASSETTE_SUBDIR / "slides_010.http-cassette.yaml").exists()
    assert not (t / CASSETTE_LEGACY_SUBDIR).exists()  # emptied legacy dir pruned


def test_legacy_top_level_cassettes_migrated_to_clm(tmp_path):
    # The former top-level cassettes/ is now a migration SOURCE: tidy --layout
    # subdir relocates it under .clm/cassettes/ (#453) and prunes the emptied dir.
    t = tmp_path / "topic_070"
    _touch(t / "slides_010.py", "# x\n")
    _touch(t / "cassettes" / "slides_010.http-cassette.yaml", "interactions: []\n")
    apply_tidy(plan_tidy(t, layout="subdir"), use_git=False)
    assert (t / ".clm" / "cassettes" / "slides_010.http-cassette.yaml").exists()
    assert not (t / "cassettes").exists()  # emptied legacy top-level dir pruned


def test_conflict_when_both_layouts_present(tmp_path):
    t = tmp_path / "topic_070"
    _touch(t / "slides_010.py", "# x\n")
    _touch(t / "voiceover_010.py", "# sibling\n")
    _touch(t / "voiceover" / "voiceover_010.py", "# foldered\n")
    plan = plan_tidy(t, layout="subdir")
    # sibling -> subdir collides with the existing foldered copy: refuse the move
    assert plan.moves == []
    assert len(plan.conflicts) == 1
    src, dst = plan.conflicts[0]
    assert src == t / "voiceover_010.py"
    assert dst == t / "voiceover" / "voiceover_010.py"
    apply_tidy(plan, use_git=False)  # leaves both in place
    assert (t / "voiceover_010.py").exists()
    assert (t / "voiceover" / "voiceover_010.py").exists()


def test_single_file_scope(tmp_path):
    t = _flat_topic(tmp_path)
    plan = plan_tidy(t / "voiceover_010.de.py", layout="subdir")
    assert len(plan.moves) == 1
    assert plan.moves[0].src == t / "voiceover_010.de.py"
    assert plan.deletes == []


def test_no_cassettes_toggle(tmp_path):
    t = _flat_topic(tmp_path)
    plan = plan_tidy(t, layout="subdir", do_cassettes=False)
    assert plan.moves  # voiceover moves still present
    assert all(m.kind == "voiceover" for m in plan.moves)
    assert plan.deletes == []  # staging untouched when cassettes excluded


def test_git_mv_preserves_tracking(tmp_path):
    t = tmp_path / "repo"
    _touch(t / "topic_070" / "slides_010.py", "# x\n")
    vo = _touch(t / "topic_070" / "voiceover_010.py", "# vo\n")
    subprocess.run(["git", "init", "-q"], cwd=t, check=True)
    subprocess.run(["git", "add", "-A"], cwd=t, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=t,
        check=True,
    )
    apply_tidy(plan_tidy(t / "topic_070", layout="subdir"), use_git=True)
    new = t / "topic_070" / "voiceover" / "voiceover_010.py"
    assert new.exists()
    assert not vo.exists()
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=t, capture_output=True, text=True
    ).stdout.splitlines()
    assert "topic_070/voiceover/voiceover_010.py" in tracked
    assert "topic_070/voiceover_010.py" not in tracked


def test_cli_dry_run_and_json(tmp_path):
    from click.testing import CliRunner

    from clm.cli.main import cli

    t = _flat_topic(tmp_path)
    result = CliRunner().invoke(cli, ["slides", "tidy", str(t), "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{") : result.output.rindex("}") + 1])
    assert payload["dry_run"] is True
    assert any(m["kind"] == "cassette" for m in payload["moves"])
    assert any(m["kind"] == "voiceover" for m in payload["moves"])
    # dry-run must not touch disk
    assert (t / "voiceover_010.de.py").exists()
    assert not (t / "voiceover").exists()


def test_cli_conflict_exit_code(tmp_path):
    from click.testing import CliRunner

    from clm.cli.main import cli

    t = tmp_path / "topic_070"
    _touch(t / "slides_010.py", "# x\n")
    _touch(t / "voiceover_010.py", "# sibling\n")
    _touch(t / "voiceover" / "voiceover_010.py", "# foldered\n")
    result = CliRunner().invoke(cli, ["slides", "tidy", str(t), "--no-git"])
    assert result.exit_code == 2  # conflict -> non-zero
