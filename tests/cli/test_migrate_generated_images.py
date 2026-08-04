"""``clm course migrate-generated-images`` — the #664 migration command.

Moves committed DrawIO/PlantUML renders from ``<topic>/img/`` into the
build-owned ``<topic>/img-generated/``. Pinned here: it moves exactly the
generated set (hand-authored files stay), matches ``ImageFile``'s sanitized
naming, is idempotent, drops byte-identical duplicates, refuses diverging
conflicts with exit 1, honors ``--dry-run``, and skips ignored trees.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.course.migrate_generated_images import migrate_generated_images_cmd


@pytest.fixture
def runner():
    return CliRunner()


def _topic(root: Path, name: str = "topic_100_t") -> Path:
    topic = root / "slides" / "module_100_m" / name
    (topic / "pu").mkdir(parents=True)
    (topic / "drawio").mkdir()
    (topic / "img").mkdir()
    (topic / "pu" / "diag.pu").write_text("@startuml\n@enduml\n", encoding="utf-8")
    (topic / "drawio" / "drawing.drawio").write_text("<mxfile/>", encoding="utf-8")
    return topic


class TestMigration:
    def test_moves_generated_renders_and_leaves_hand_authored_files(self, runner, tmp_path):
        topic = _topic(tmp_path)
        (topic / "img" / "diag.png").write_bytes(b"pu render")
        (topic / "img" / "drawing.png").write_bytes(b"drawio render")
        (topic / "img" / "photo.png").write_bytes(b"hand-authored")

        result = runner.invoke(migrate_generated_images_cmd, [str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert (topic / "img-generated" / "diag.png").read_bytes() == b"pu render"
        assert (topic / "img-generated" / "drawing.png").read_bytes() == b"drawio render"
        assert (topic / "img" / "photo.png").exists(), "hand-authored files must stay"
        assert not (topic / "img" / "diag.png").exists()
        assert "2 moved" in result.output

    def test_both_render_formats_move(self, runner, tmp_path):
        """A repo that switched image_format carries stale renders in the
        other format — both are build-owned."""
        topic = _topic(tmp_path)
        (topic / "img" / "diag.png").write_bytes(b"png render")
        (topic / "img" / "diag.svg").write_bytes(b"svg render")

        result = runner.invoke(migrate_generated_images_cmd, [str(tmp_path)])

        assert result.exit_code == 0
        assert (topic / "img-generated" / "diag.png").exists()
        assert (topic / "img-generated" / "diag.svg").exists()

    def test_idempotent_second_run_is_a_no_op(self, runner, tmp_path):
        topic = _topic(tmp_path)
        (topic / "img" / "diag.png").write_bytes(b"render")
        assert runner.invoke(migrate_generated_images_cmd, [str(tmp_path)]).exit_code == 0

        second = runner.invoke(migrate_generated_images_cmd, [str(tmp_path)])

        assert second.exit_code == 0
        assert "0 moved, 0 duplicate(s) dropped, 0 conflict(s)" in second.output
        assert (topic / "img-generated" / "diag.png").read_bytes() == b"render"

    def test_a_byte_identical_duplicate_is_dropped(self, runner, tmp_path):
        topic = _topic(tmp_path)
        (topic / "img" / "diag.png").write_bytes(b"render")
        (topic / "img-generated").mkdir()
        (topic / "img-generated" / "diag.png").write_bytes(b"render")

        result = runner.invoke(migrate_generated_images_cmd, [str(tmp_path)])

        assert result.exit_code == 0
        assert not (topic / "img" / "diag.png").exists()
        assert "1 duplicate(s) dropped" in result.output

    def test_diverging_copies_are_a_conflict_and_exit_1(self, runner, tmp_path):
        topic = _topic(tmp_path)
        (topic / "img" / "diag.png").write_bytes(b"the build rendered this")
        (topic / "img-generated").mkdir()
        (topic / "img-generated" / "diag.png").write_bytes(b"something else entirely")

        result = runner.invoke(migrate_generated_images_cmd, [str(tmp_path)])

        assert result.exit_code == 1
        assert (topic / "img" / "diag.png").exists(), "a conflict must not be touched"
        assert (topic / "img-generated" / "diag.png").read_bytes() == b"something else entirely"
        assert "CONFLICT" in result.output

    def test_dry_run_reports_but_moves_nothing(self, runner, tmp_path):
        topic = _topic(tmp_path)
        (topic / "img" / "diag.png").write_bytes(b"render")

        result = runner.invoke(migrate_generated_images_cmd, [str(tmp_path), "--dry-run"])

        assert result.exit_code == 0
        assert "would move" in result.output
        assert (topic / "img" / "diag.png").exists()
        assert not (topic / "img-generated").exists()

    def test_sanitized_render_names_are_matched(self, runner, tmp_path):
        """The command must find exactly the name the build writes —
        ``ImageFile.img_path`` sanitizes the stem, so the lookup must too."""
        from clm.core.utils.text_utils import sanitize_path

        topic = _topic(tmp_path)
        source = topic / "pu" / "Über Diagramm.pu"
        source.write_text("@startuml\n@enduml\n", encoding="utf-8")
        legacy = sanitize_path((topic / "img" / source.stem).with_suffix(".png"))
        legacy.write_bytes(b"render")

        result = runner.invoke(migrate_generated_images_cmd, [str(tmp_path)])

        assert result.exit_code == 0, result.output
        expected = sanitize_path((topic / "img-generated" / source.stem).with_suffix(".png"))
        assert expected.exists()

    def test_ignored_trees_are_skipped(self, runner, tmp_path):
        """A diagram source inside e.g. ``__pycache__``/``.venv`` must not
        drive a move (mirrors the course scan's ignore rules): a vendored
        package can ship ``.pu`` files, and its neighbors are not ours."""
        ghost_topic = tmp_path / "slides" / "__pycache__" / "topic_999_ghost"
        (ghost_topic / "pu").mkdir(parents=True)
        (ghost_topic / "img").mkdir()
        (ghost_topic / "pu" / "ghost.pu").write_text("@startuml\n@enduml\n", encoding="utf-8")
        (ghost_topic / "img" / "ghost.png").write_bytes(b"not ours to move")

        result = runner.invoke(migrate_generated_images_cmd, [str(tmp_path)])

        assert result.exit_code == 0
        assert (ghost_topic / "img" / "ghost.png").exists()
        assert not (ghost_topic / "img-generated").exists()
