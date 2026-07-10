"""Tests for the ``clm_cache.db`` path-column migrator.

The migrator rewrites the input-path lookup keys of the three cache tables so a
rename/renumber preserves cached work. Each test seeds rows through the *real*
cache managers, runs the migrator, then asserts the cache **hits at the new
path and misses at the old** — i.e. it verifies the observable outcome (a live
cache hit), not just row counts.
"""

from __future__ import annotations

import os
import sqlite3

import pytest
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from clm.cli.build_data_classes import BuildError, BuildWarning
from clm.infrastructure.database.cache_path_migration import (
    PathMapping,
    migrate_cache_paths,
    migrate_dir_rename,
    plan_dir_rename,
)
from clm.infrastructure.database.db_operations import DatabaseManager
from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
from clm.infrastructure.messaging.base_classes import Result

_META = "completed:python:en:html"


class _Res(Result):
    data: str
    metadata: str

    def result_bytes(self) -> bytes:
        return self.data.encode("utf-8")

    def output_metadata(self):
        return self.metadata


def _result(data: str = "payload") -> _Res:
    return _Res(
        output_file="o",
        input_file="i",
        content_hash="h",
        data=data,
        metadata=_META,
        correlation_id="c",
    )


def _notebook():
    nb = new_notebook()
    nb.cells = [new_markdown_cell("# t"), new_code_cell("print(1)")]
    return nb


@pytest.fixture
def cache_db(tmp_path):
    return tmp_path / "clm_cache.db"


def _seed_processed(db, path, content_hash="h1"):
    with DatabaseManager(db) as m:
        m.store_latest_result(str(path), content_hash, "corr", _result(), retain_count=3)


def _seed_issues(db, path, content_hash="h1"):
    with DatabaseManager(db) as m:
        m.store_error(
            str(path),
            content_hash,
            _META,
            BuildError(
                error_type="user",
                category="notebook_compilation",
                severity="error",
                file_path=str(path),
                message="boom",
                actionable_guidance="fix",
                job_id=1,
                correlation_id="c",
                details={},
            ),
        )
        m.store_warning(
            str(path),
            content_hash,
            _META,
            BuildWarning(
                category="slow_processing",
                message="slow",
                severity="low",
                file_path=str(path),
            ),
        )


def _seed_executed(db, path, content_hash="h1", language="en", prog_lang="python"):
    with ExecutedNotebookCache(db) as c:
        c.store(str(path), content_hash, language, prog_lang, _notebook())


# ---------------------------------------------------------------------------
# Per-table rewrites: cache hits move from old path to new path
# ---------------------------------------------------------------------------


def test_processed_files_hit_moves_to_new_path(cache_db, tmp_path):
    old = tmp_path / "topic_100_intro" / "slides_a.py"
    new = tmp_path / "topic_040_intro" / "slides_a.py"
    _seed_processed(cache_db, old)

    report = migrate_cache_paths(cache_db, [PathMapping(str(old), str(new))])

    assert report.rows_rewritten >= 1
    with DatabaseManager(cache_db) as m:
        assert m.get_result(str(new), "h1", _META) is not None
        assert m.get_result(str(old), "h1", _META) is None


def test_executed_notebooks_hit_moves_to_new_path(cache_db, tmp_path):
    old = tmp_path / "topic_100_intro" / "slides_a.py"
    new = tmp_path / "topic_040_intro" / "slides_a.py"
    _seed_executed(cache_db, old)

    migrate_cache_paths(cache_db, [PathMapping(str(old), str(new))])

    with ExecutedNotebookCache(cache_db) as c:
        assert c.get(str(new), "h1", "en", "python") is not None
        assert c.get(str(old), "h1", "en", "python") is None


def test_processing_issues_move_to_new_path(cache_db, tmp_path):
    old = tmp_path / "topic_100" / "s.py"
    new = tmp_path / "topic_040" / "s.py"
    _seed_issues(cache_db, old)

    migrate_cache_paths(cache_db, [PathMapping(str(old), str(new))])

    with DatabaseManager(cache_db) as m:
        errs_new, warns_new = m.get_issues(str(new), "h1", _META)
        assert len(errs_new) == 1
        assert len(warns_new) == 1
        errs_old, warns_old = m.get_issues(str(old), "h1", _META)
        assert errs_old == []
        assert warns_old == []


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_reports_but_does_not_mutate(cache_db, tmp_path):
    old = tmp_path / "topic_100" / "s.py"
    new = tmp_path / "topic_040" / "s.py"
    _seed_processed(cache_db, old)
    _seed_executed(cache_db, old)

    report = migrate_cache_paths(cache_db, [PathMapping(str(old), str(new))], dry_run=True)

    assert report.dry_run is True
    assert report.rows_rewritten >= 2  # one processed_files row + one executed_notebooks row
    # Nothing actually moved: still a hit at the OLD path, miss at the new.
    with DatabaseManager(cache_db) as m:
        assert m.get_result(str(old), "h1", _META) is not None
        assert m.get_result(str(new), "h1", _META) is None
    with ExecutedNotebookCache(cache_db) as c:
        assert c.get(str(old), "h1", "en", "python") is not None
        assert c.get(str(new), "h1", "en", "python") is None


# ---------------------------------------------------------------------------
# UNIQUE-collision handling on executed_notebooks
# ---------------------------------------------------------------------------


def test_executed_notebooks_collision_drops_duplicate(cache_db, tmp_path):
    old = tmp_path / "topic_100" / "s.py"
    new = tmp_path / "topic_040" / "s.py"
    # Both paths already hold a row for the SAME (content_hash, lang, prog_lang):
    # migrating old onto new would violate UNIQUE — the duplicate is dropped.
    _seed_executed(cache_db, old, content_hash="h1")
    _seed_executed(cache_db, new, content_hash="h1")

    report = migrate_cache_paths(cache_db, [PathMapping(str(old), str(new))])

    exec_report = next(t for t in report.tables if t.table == "executed_notebooks")
    assert exec_report.collisions_dropped == 1
    with ExecutedNotebookCache(cache_db) as c:
        assert c.get(str(new), "h1", "en", "python") is not None
        assert c.get(str(old), "h1", "en", "python") is None
    with sqlite3.connect(str(cache_db)) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM executed_notebooks "
            "WHERE input_file=? AND content_hash=? AND language=? AND prog_lang=?",
            (str(new), "h1", "en", "python"),
        ).fetchone()[0]
    assert count == 1


def test_executed_notebooks_different_hash_keeps_both(cache_db, tmp_path):
    old = tmp_path / "topic_100" / "s.py"
    new = tmp_path / "topic_040" / "s.py"
    # Different content_hash => different UNIQUE key => no collision, both survive.
    _seed_executed(cache_db, old, content_hash="hOLD")
    _seed_executed(cache_db, new, content_hash="hNEW")

    report = migrate_cache_paths(cache_db, [PathMapping(str(old), str(new))])

    exec_report = next(t for t in report.tables if t.table == "executed_notebooks")
    assert exec_report.collisions_dropped == 0
    assert exec_report.rows_rewritten == 1
    with ExecutedNotebookCache(cache_db) as c:
        assert c.get(str(new), "hOLD", "en", "python") is not None  # migrated
        assert c.get(str(new), "hNEW", "en", "python") is not None  # pre-existing
        assert c.get(str(old), "hOLD", "en", "python") is None


# ---------------------------------------------------------------------------
# Directory-rename planning + end-to-end
# ---------------------------------------------------------------------------


def test_plan_dir_rename_only_maps_paths_under_old_dir(cache_db, tmp_path):
    old_dir = tmp_path / "module_550" / "topic_100_intro"
    new_dir = tmp_path / "module_550" / "topic_040_intro"
    inside = old_dir / "slides_a.py"
    sibling = tmp_path / "module_550" / "topic_200_other" / "slides_b.py"
    _seed_processed(cache_db, inside)
    _seed_executed(cache_db, inside)
    _seed_processed(cache_db, sibling)

    mappings = plan_dir_rename(cache_db, old_dir, new_dir)

    olds = {m.old for m in mappings}
    assert str(inside) in olds
    assert str(sibling) not in olds
    mapping = next(m for m in mappings if m.old == str(inside))
    assert mapping.new == str(new_dir / "slides_a.py")


def test_migrate_dir_rename_end_to_end(cache_db, tmp_path):
    old_dir = tmp_path / "topic_310_what_is_ml"
    new_dir = tmp_path / "topic_040_what_is_ml"
    f1 = old_dir / "slides_10_a.py"
    f2 = old_dir / "slides_20_b.py"
    sibling = tmp_path / "topic_050_history" / "slides_10_c.py"
    for f in (f1, f2):
        _seed_processed(cache_db, f)
        _seed_issues(cache_db, f)
        _seed_executed(cache_db, f)
    _seed_processed(cache_db, sibling)

    report = migrate_dir_rename(cache_db, old_dir, new_dir)

    assert report.changed
    with DatabaseManager(cache_db) as m:
        for f in (f1, f2):
            new_f = new_dir / f.name
            assert m.get_result(str(new_f), "h1", _META) is not None
            assert m.get_result(str(f), "h1", _META) is None
            errs, warns = m.get_issues(str(new_f), "h1", _META)
            assert len(errs) == 1
            assert len(warns) == 1
        # A sibling topic's cache is untouched.
        assert m.get_result(str(sibling), "h1", _META) is not None
    with ExecutedNotebookCache(cache_db) as c:
        for f in (f1, f2):
            assert c.get(str(new_dir / f.name), "h1", "en", "python") is not None


def test_sibling_topic_rows_are_not_touched(cache_db, tmp_path):
    old_dir = tmp_path / "topic_100_intro"
    new_dir = tmp_path / "topic_040_intro"
    inside = old_dir / "slides_a.py"
    # A sibling whose name shares a prefix with old_dir but is NOT under it.
    sibling = tmp_path / "topic_100_intro_extra" / "slides_b.py"
    _seed_processed(cache_db, inside)
    _seed_processed(cache_db, sibling)

    migrate_dir_rename(cache_db, old_dir, new_dir)

    with DatabaseManager(cache_db) as m:
        assert m.get_result(str(new_dir / "slides_a.py"), "h1", _META) is not None
        assert m.get_result(str(sibling), "h1", _META) is not None  # untouched


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_nonexistent_db_is_noop_and_not_created(tmp_path):
    missing = tmp_path / "nope.db"
    report = migrate_dir_rename(missing, tmp_path / "a", tmp_path / "b")
    assert report.tables == ()
    assert not report.changed
    assert not missing.exists()


def test_self_mapping_is_noop(cache_db, tmp_path):
    path = tmp_path / "topic_100" / "s.py"
    _seed_processed(cache_db, path)
    report = migrate_cache_paths(cache_db, [PathMapping(str(path), str(path))])
    assert not report.changed


def test_plan_tolerates_trailing_separator_on_old_dir(cache_db, tmp_path):
    old_dir = tmp_path / "topic_100"
    new_dir = tmp_path / "topic_040"
    f = old_dir / "s.py"
    _seed_processed(cache_db, f)

    mappings = plan_dir_rename(cache_db, str(old_dir) + os.sep, new_dir)

    assert any(m.old == str(f) for m in mappings)


def test_summary_names_the_touched_table(cache_db, tmp_path):
    old = tmp_path / "topic_100" / "s.py"
    new = tmp_path / "topic_040" / "s.py"
    _seed_processed(cache_db, old)
    report = migrate_cache_paths(cache_db, [PathMapping(str(old), str(new))])
    text = report.summary()
    assert "rewrote" in text
    assert "processed_files" in text


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive matching is Windows-only")
def test_windows_case_insensitive_dir_match(cache_db, tmp_path):
    old_dir = tmp_path / "Topic_100_Intro"
    new_dir = tmp_path / "Topic_040_Intro"
    f = old_dir / "Slides_A.py"
    _seed_processed(cache_db, f)

    # Old dir supplied with different casing than the stored path.
    mappings = plan_dir_rename(cache_db, str(old_dir).lower(), new_dir)

    mapping = next(m for m in mappings if m.old == str(f))
    assert mapping.new == str(new_dir / "Slides_A.py")
