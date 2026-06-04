"""Tests for the release ledger (issue #208, step 2)."""

from clm.release.ledger import Ledger, partition_known


def test_load_missing_file_is_empty(tmp_path):
    assert Ledger.load(tmp_path / "nope.txt").released == []


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "release" / "jan.txt"
    ledger = Ledger(["introduction", "functions"])
    ledger.save(path)

    reloaded = Ledger.load(path)
    assert reloaded.released == ["introduction", "functions"]
    # The saved file carries the explanatory header.
    assert path.read_text(encoding="utf-8").startswith("# CLM release ledger")


def test_load_ignores_comments_blank_lines_and_dedups(tmp_path):
    path = tmp_path / "jan.txt"
    path.write_text(
        "# header\n\nintroduction\n  functions  \n# note\nintroduction\nvariables\n",
        encoding="utf-8",
    )
    assert Ledger.load(path).released == ["introduction", "functions", "variables"]


def test_add_appends_only_new_and_reports_added():
    ledger = Ledger(["introduction"])
    added = ledger.add(["introduction", "functions", "functions", "variables"])
    assert added == ["functions", "variables"]
    assert ledger.released == ["introduction", "functions", "variables"]
    assert ledger.released_set == {"introduction", "functions", "variables"}


def test_partition_known_splits_against_valid_ids():
    known, unknown = partition_known(
        ["intro", "typo", "functions"], valid_ids={"intro", "functions", "loops"}
    )
    assert known == ["intro", "functions"]
    assert unknown == ["typo"]
