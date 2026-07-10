"""Tests for spec-order topic renumbering (planning + apply; issue #589)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from clm.core.course_renumber import (
    RenumberError,
    apply_renumber,
    plan_renumber,
)
from clm.core.course_spec import CourseSpec


def _spec(tmp_path: Path, topics_by_section: list[list[str]]) -> CourseSpec:
    sections = "\n".join(
        "<section><name><de>S</de><en>S</en></name><topics>"
        + "".join(f"<topic>{t}</topic>" for t in topics)
        + "</topics></section>"
        for topics in topics_by_section
    )
    xml = dedent(
        f"""\
        <course>
            <name><de>K</de><en>C</en></name>
            <prog-lang>python</prog-lang>
            <sections>{sections}</sections>
        </course>
        """
    )
    spec_file = tmp_path / "course-specs" / "test.xml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(xml, encoding="utf-8")
    return CourseSpec.from_file(spec_file)


def _make_topics(slides: Path, module: str, names: list[str]) -> Path:
    module_dir = slides / module
    for name in names:
        topic = module_dir / name
        topic.mkdir(parents=True)
        (topic / "slides_010_x.py").write_text("# %%\n", encoding="utf-8")
    return module_dir


def _plan(tmp_path, spec, **kwargs):
    return plan_renumber(spec, tmp_path / "slides", spec_name="test.xml", **kwargs)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def test_plan_orders_topics_by_spec_not_by_directory(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_050_b", "topic_310_a", "topic_120_c"])
    spec = _spec(tmp_path, [["a", "b", "c"]])

    plan = _plan(tmp_path, spec)

    (module,) = plan.modules
    assert [(op.old_path.name, op.new_path.name) for op in module.renames] == [
        ("topic_310_a", "topic_010_a"),
        ("topic_050_b", "topic_020_b"),
        ("topic_120_c", "topic_030_c"),
    ]


def test_plan_preserves_existing_ordinal_width(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_0300_a", "topic_020_b"])
    spec = _spec(tmp_path, [["a", "b"]])

    plan = _plan(tmp_path, spec)

    names = {op.new_path.name for op in plan.modules[0].renames}
    assert names == {"topic_0010_a", "topic_0020_b"}  # widest existing = 4 digits


def test_plan_explicit_width_and_overflow_error(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_10_a", "topic_20_b"])
    spec = _spec(tmp_path, [["a", "b"]])

    plan = _plan(tmp_path, spec, width=3)
    assert plan.modules[0].renames[0].new_path.name == "topic_010_a"

    with pytest.raises(RenumberError, match="does not fit width"):
        _plan(tmp_path, spec, start=100, step=10, width=2)


def test_plan_in_order_topics_are_unchanged(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_010_a", "topic_020_b"])
    spec = _spec(tmp_path, [["a", "b"]])

    plan = _plan(tmp_path, spec)

    assert plan.renames == ()
    assert [p.name for p in plan.modules[0].unchanged] == ["topic_010_a", "topic_020_b"]


def test_plan_skips_non_canonical_names_that_would_change_identity(tmp_path):
    # "topic_extras_bonus" has topic id "bonus"; renumbering it would change
    # the id to "extras_bonus". It must be skipped, never renamed.
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_310_a", "topic_extras_bonus"])
    spec = _spec(tmp_path, [["a", "bonus"]])

    plan = _plan(tmp_path, spec)

    (module,) = plan.modules
    assert [op.old_path.name for op in module.renames] == ["topic_310_a"]
    (skipped,) = module.skipped
    assert skipped.path.name == "topic_extras_bonus"
    assert "topic id" in skipped.reason


def test_plan_leaves_orphans_untouched(tmp_path):
    # topic_020_b_old is on disk but NOT in the spec (orphan): it gets no
    # rename op and no assigned ordinal, and stays exactly where it is.
    _make_topics(
        tmp_path / "slides", "module_100_m", ["topic_300_a", "topic_400_b", "topic_020_b_old"]
    )
    spec = _spec(tmp_path, [["a", "b"]])

    plan = _plan(tmp_path, spec)

    assert {op.new_path.name for op in plan.renames} == {"topic_010_a", "topic_020_b"}
    apply_renumber(plan, use_git=False)
    assert (tmp_path / "slides" / "module_100_m" / "topic_020_b_old").exists()


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive path collision is Windows-only")
def test_plan_fails_when_target_collides_with_orphan_case_insensitively(tmp_path):
    # An orphan whose name differs from a planned target only by case has a
    # DIFFERENT topic id (ids are case-sensitive), so ambiguity does not catch
    # it — but the rename would collide on a case-insensitive filesystem.
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_400_b", "topic_010_B"])
    spec = _spec(tmp_path, [["b"]])

    with pytest.raises(RenumberError, match="already exists"):
        _plan(tmp_path, spec, start=10, step=10, width=3)


def test_plan_rejects_ambiguous_topics(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_010_a"])
    _make_topics(tmp_path / "slides", "module_200_n", ["topic_050_a"])
    spec = _spec(tmp_path, [["a"]])

    with pytest.raises(RenumberError, match="ambiguous"):
        _plan(tmp_path, spec)


def test_plan_module_filter_and_unknown_module(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_050_a"])
    _make_topics(tmp_path / "slides", "module_200_n", ["topic_050_b"])
    spec = _spec(tmp_path, [["a", "b"]])

    plan = _plan(tmp_path, spec, module="module_200_n")
    assert [m.module for m in plan.modules] == ["module_200_n"]

    with pytest.raises(RenumberError, match="no spec-referenced topics"):
        _plan(tmp_path, spec, module="module_999_zzz")


def test_plan_reports_missing_topics(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_050_a"])
    spec = _spec(tmp_path, [["a", "ghost_topic"]])

    plan = _plan(tmp_path, spec)

    assert plan.missing == ("ghost_topic",)


def test_plan_numbers_follow_spec_order_across_sections(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_900_a", "topic_100_b"])
    spec = _spec(tmp_path, [["a"], ["b"]])  # a in section 1, b in section 2

    plan = _plan(tmp_path, spec)

    assert [(op.old_path.name, op.new_path.name) for op in plan.modules[0].renames] == [
        ("topic_900_a", "topic_010_a"),
        ("topic_100_b", "topic_020_b"),
    ]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def test_apply_renames_dirs_and_carries_contents(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_310_a", "topic_050_b"])
    # Untracked sidecar content must ride along with the physical rename.
    ledger = tmp_path / "slides" / "module_100_m" / "topic_310_a" / ".clm" / "sync-ledger.json"
    ledger.parent.mkdir()
    ledger.write_text("{}", encoding="utf-8")
    spec = _spec(tmp_path, [["a", "b"]])

    plan = _plan(tmp_path, spec)
    apply_renumber(plan, use_git=False)

    module_dir = tmp_path / "slides" / "module_100_m"
    assert (module_dir / "topic_010_a" / "slides_010_x.py").exists()
    assert (module_dir / "topic_010_a" / ".clm" / "sync-ledger.json").exists()
    assert (module_dir / "topic_020_b" / "slides_010_x.py").exists()
    assert not (module_dir / "topic_310_a").exists()
    assert not any(module_dir.glob("*.clm-renumber-tmp-*"))


def test_apply_handles_targets_overlapping_sources(tmp_path):
    # b currently holds ordinal 010 while a must take 010 and b move to 020:
    # phase-1 parking makes the overlap safe.
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_020_a", "topic_010_b"])
    spec = _spec(tmp_path, [["a", "b"]])

    plan = _plan(tmp_path, spec)
    apply_renumber(plan, use_git=False)

    module_dir = tmp_path / "slides" / "module_100_m"
    assert (module_dir / "topic_010_a").is_dir()
    assert (module_dir / "topic_020_b").is_dir()


@pytest.mark.serial
def test_apply_uses_git_mv_inside_a_repo(tmp_path):
    _make_topics(tmp_path / "slides", "module_100_m", ["topic_310_a"])
    spec = _spec(tmp_path, [["a"]])
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=tmp_path,
        check=True,
    )

    plan = _plan(tmp_path, spec)
    used_git = apply_renumber(plan)

    assert used_git is True
    module_dir = tmp_path / "slides" / "module_100_m"
    assert (module_dir / "topic_010_a" / "slides_010_x.py").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout
    assert "topic_010_a" in status  # staged rename


def test_apply_file_topic_preserves_extension(tmp_path):
    module_dir = tmp_path / "slides" / "module_100_m"
    module_dir.mkdir(parents=True)
    (module_dir / "topic_310_a.py").write_text("# %%\n", encoding="utf-8")
    spec = _spec(tmp_path, [["a"]])

    plan = _plan(tmp_path, spec)
    (op,) = plan.renames
    assert op.new_path.name == "topic_010_a.py"
    apply_renumber(plan, use_git=False)
    assert (module_dir / "topic_010_a.py").exists()
