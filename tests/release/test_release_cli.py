"""Tests for the ``clm release`` CLI (issue #208, step 2)."""

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.release import release_group
from clm.core.provenance_manifest import MANIFEST_FILENAME
from clm.release.frozen_manifest import FROZEN_FILENAME, FrozenManifest
from clm.release.ledger import Ledger

SPEC = Path(__file__).parent.parent / "test-data" / "course-specs" / "test-spec-1.xml"
KNOWN_TOPIC = "some_topic_from_test_1"


def test_add_validates_and_appends(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"

    result = runner.invoke(release_group, ["add", str(SPEC), KNOWN_TOPIC, "--ledger", str(ledger)])
    assert result.exit_code == 0, result.output
    assert Ledger.load(ledger).released == [KNOWN_TOPIC]

    # Re-adding the same topic is a reported no-op.
    again = runner.invoke(release_group, ["add", str(SPEC), KNOWN_TOPIC, "--ledger", str(ledger)])
    assert again.exit_code == 0
    assert "Already released" in again.output
    assert Ledger.load(ledger).released == [KNOWN_TOPIC]


def test_add_rejects_unknown_topic(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"
    result = runner.invoke(
        release_group, ["add", str(SPEC), "definitely_not_a_topic", "--ledger", str(ledger)]
    )
    assert result.exit_code != 0
    assert "Unknown topic" in result.output
    # The ledger is never touched when validation fails.
    assert not ledger.exists()


def test_status_reports_released_and_pending(tmp_path):
    runner = CliRunner()
    ledger = tmp_path / "jan.txt"
    Ledger([KNOWN_TOPIC]).save(ledger)

    result = runner.invoke(release_group, ["status", str(SPEC), "--ledger", str(ledger)])
    assert result.exit_code == 0, result.output
    assert "released" in result.output
    assert KNOWN_TOPIC in result.output


def _write_source(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "source_commit": "abc",
        "source_dirty": False,
        "built_at": "t",
        "target": "src",
        "files": [
            {
                "path": "Sec/01 Intro.ipynb",
                "topic_id": "intro",
                "section_id": "w01",
                "kind": "completed",
                "format": "notebook",
                "language": "en",
                "content_hash": "sha256:a",
            },
            {
                "path": "shared/data.csv",
                "topic_id": None,
                "section_id": None,
                "kind": None,
                "format": "dir-group",
                "language": "en",
                "content_hash": "sha256:c",
            },
        ],
    }
    (root / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    for entry in manifest["files"]:
        path = root / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry["content_hash"], encoding="utf-8")


def test_sync_copies_released_topic_and_freezes(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    dest = tmp_path / "jan"
    _write_source(source)
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)

    result = runner.invoke(
        release_group,
        ["sync", "--ledger", str(ledger), "--source", str(source), "--dest", str(dest)],
    )
    assert result.exit_code == 0, result.output
    assert (dest / "Sec/01 Intro.ipynb").is_file()
    assert (dest / "shared/data.csv").is_file()

    # With explicit paths the channel name defaults to the destination dir name.
    frozen = FrozenManifest.load(dest / FROZEN_FILENAME, channel="jan")
    assert frozen.is_frozen("intro")
    assert frozen.skeleton_frozen is True
    assert frozen.channel == "jan"


def test_sync_dry_run_copies_nothing(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    dest = tmp_path / "jan"
    _write_source(source)
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)

    result = runner.invoke(
        release_group,
        [
            "sync",
            "--ledger",
            str(ledger),
            "--source",
            str(source),
            "--dest",
            str(dest),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert not (dest / "Sec/01 Intro.ipynb").exists()


def test_sync_errors_without_manifest(tmp_path):
    runner = CliRunner()
    source = tmp_path / "src"
    source.mkdir()
    ledger = tmp_path / "jan.txt"
    Ledger(["intro"]).save(ledger)

    result = runner.invoke(
        release_group,
        ["sync", "--ledger", str(ledger), "--source", str(source), "--dest", str(tmp_path / "jan")],
    )
    assert result.exit_code != 0
    assert "No provenance manifest" in result.output


SPEC_WITH_CHANNELS = """
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <sections>
    <section>
      <name><de>S</de><en>S</en></name>
      <topics><topic>intro</topic></topics>
    </section>
  </sections>
  <output-targets>
    <output-target name="src">
      <path>output/src</path>
      <kinds><kind>completed</kind></kinds>
    </output-target>
  </output-targets>
  <release-channels source-target="src">
    <channel name="jan" path="solutions/jan" ledger="release/jan.txt"/>
  </release-channels>
</course>
""".strip()


def test_channel_resolves_ledger_source_and_dest_from_spec(tmp_path):
    runner = CliRunner()
    course_root = tmp_path
    # Spec must live in a subdir: resolve_course_paths uses its grandparent.
    specs_dir = course_root / "course-specs"
    specs_dir.mkdir()
    spec_file = specs_dir / "course.xml"
    spec_file.write_text(SPEC_WITH_CHANNELS, encoding="utf-8")

    # Built frozen source for the "src" output target.
    _write_source(course_root / "output" / "src")

    add = runner.invoke(release_group, ["add", str(spec_file), "intro", "--channel", "jan"])
    assert add.exit_code == 0, add.output
    assert Ledger.load(course_root / "release" / "jan.txt").released == ["intro"]

    sync = runner.invoke(release_group, ["sync", str(spec_file), "--channel", "jan"])
    assert sync.exit_code == 0, sync.output

    dest = course_root / "solutions" / "jan"
    assert (dest / "Sec/01 Intro.ipynb").is_file()
    frozen = FrozenManifest.load(dest / FROZEN_FILENAME, channel="jan")
    assert frozen.is_frozen("intro")
    assert frozen.skeleton_frozen is True
