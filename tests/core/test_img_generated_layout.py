"""The #664 namespace split: ``img/`` is human-owned, ``img-generated/`` build-owned.

``<topic>/img/`` used to hold both hand-authored assets and the DrawIO/PlantUML
renders, distinguishable only through the course model — the ambiguity behind
the #661 nondeterminism class. Diagrams now render into the build-owned
``img-generated/`` sibling, with one transitional exception: a committed legacy
render keeps its location until ``clm course migrate-generated-images`` moves
it, so unmigrated repos build exactly as before.

Pinned here: the render-target choice, that both directories collapse onto the
SAME output namespace (slide references ``img/...`` never change, so a correct
migration is byte-identical in the output tree), sweep/registry recognition,
and the data-URL inliner's source-tree fallback.
"""

import os
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

DATA_DIR = Path(__file__).parent.parent / "test-data"

from clm.core.course_files.duplicated_image_file import DuplicatedImageFile
from clm.core.course_files.image_file import ImageFile
from clm.core.image_registry import get_relative_img_path
from clm.core.output_write_registry import is_image_path


@pytest.fixture
def mock_course():
    course = MagicMock()
    course.image_format = "png"
    return course


def _diagram_file(course, tmp_path: Path) -> ImageFile:
    source = tmp_path / "topic" / "pu" / "diagram.pu"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("@startuml\n@enduml\n", encoding="utf-8")
    return ImageFile(course=course, path=source, topic=MagicMock())


class TestRenderTarget:
    def test_a_fresh_diagram_renders_into_img_generated(self, mock_course, tmp_path):
        image = _diagram_file(mock_course, tmp_path)
        assert image.img_path == tmp_path / "topic" / "img-generated" / "diagram.png"
        assert image.source_outputs == frozenset({image.img_path})

    def test_a_committed_legacy_render_keeps_its_location(self, mock_course, tmp_path):
        """The transitional rule: an unmigrated repo builds exactly as before.

        Rendering to the new directory while the stale legacy copy still
        shipped as a hand-authored-looking image would put two writers on one
        output path — the #661 conflict class this issue exists to end.
        """
        image = _diagram_file(mock_course, tmp_path)
        legacy = tmp_path / "topic" / "img" / "diagram.png"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"committed render")
        assert image.img_path == legacy

    def test_migrating_the_file_flips_the_target(self, mock_course, tmp_path):
        image = _diagram_file(mock_course, tmp_path)
        legacy = tmp_path / "topic" / "img" / "diagram.png"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"committed render")
        target = tmp_path / "topic" / "img-generated" / "diagram.png"
        target.parent.mkdir(parents=True)
        legacy.rename(target)
        assert image.img_path == target

    def test_image_format_is_respected(self, mock_course, tmp_path):
        mock_course.image_format = "svg"
        image = _diagram_file(mock_course, tmp_path)
        assert image.img_path.name == "diagram.svg"


class TestOneOutputNamespace:
    """Both directories collapse to ``img/`` in the output — the property that
    makes the migration byte-identical and keeps every slide reference valid."""

    def test_shared_mode_relative_path_is_identical_for_both_layouts(self, tmp_path):
        legacy = tmp_path / "topic" / "img" / "diagram.png"
        migrated = tmp_path / "topic" / "img-generated" / "diagram.png"
        assert get_relative_img_path(legacy) == get_relative_img_path(migrated) == "diagram.png"

    def test_subfolders_are_preserved_from_either_root(self, tmp_path):
        assert get_relative_img_path(tmp_path / "img" / "foo" / "bar.png") == "foo/bar.png"
        assert (
            get_relative_img_path(tmp_path / "img-generated" / "foo" / "bar.png") == "foo/bar.png"
        )

    def test_no_image_folder_still_falls_back_to_the_filename(self, tmp_path):
        assert get_relative_img_path(tmp_path / "elsewhere" / "pic.png") == "pic.png"

    def test_duplicated_mode_output_collapses_img_generated(self, tmp_path):
        topic = MagicMock()
        topic.path = tmp_path
        (tmp_path / "img-generated").mkdir()
        (tmp_path / "img-generated" / "diagram.png").write_bytes(b"x")
        image = DuplicatedImageFile(
            course=MagicMock(), path=tmp_path / "img-generated" / "diagram.png", topic=topic
        )
        assert image.output_relative_path == Path("img") / "diagram.png"

    def test_duplicated_mode_output_keeps_plain_img_unchanged(self, tmp_path):
        topic = MagicMock()
        topic.path = tmp_path
        (tmp_path / "img").mkdir()
        (tmp_path / "img" / "photo.png").write_bytes(b"x")
        image = DuplicatedImageFile(
            course=MagicMock(), path=tmp_path / "img" / "photo.png", topic=topic
        )
        assert image.output_relative_path == Path("img") / "photo.png"

    def test_only_the_leading_component_collapses(self, tmp_path):
        """Review L1: the render target is always topic-level. A hand-authored
        file under a DEEPER directory that happens to be named img-generated
        keeps its pre-#664 verbatim copy — collapsing it would silently move
        the output and break references to it."""
        topic = MagicMock()
        topic.path = tmp_path
        nested = tmp_path / "data" / "img-generated"
        nested.mkdir(parents=True)
        (nested / "x.png").write_bytes(b"x")
        image = DuplicatedImageFile(course=MagicMock(), path=nested / "x.png", topic=topic)
        assert image.output_relative_path == Path("data") / "img-generated" / "x.png"


class TestProvenanceManifest:
    """Review H1: the manifest must enumerate what the copy WRITES. Using the
    raw relative path silently dropped every migrated render from
    ``.clm-manifest.json`` — and the release pipeline copies by manifest, so
    frozen cohorts would have shipped without a single generated diagram."""

    @pytest.fixture
    def migrated_course(self, course_1_spec, tmp_path):
        import shutil

        from clm.cli.commands.course.migrate_generated_images import (
            migrate_generated_images_cmd,
        )
        from clm.core.course import Course

        data_dir = tmp_path / "test-data"
        shutil.copytree(DATA_DIR, data_dir)
        from click.testing import CliRunner

        result = CliRunner().invoke(migrate_generated_images_cmd, [str(data_dir)])
        assert result.exit_code == 0, result.output
        assert "2 moved" in result.output
        return Course.from_spec(course_1_spec, data_dir, tmp_path / "output")

    def test_migrated_renders_stay_in_the_manifest(self, migrated_course):
        from clm.core.provenance_manifest import enumerate_expected_outputs

        target = migrated_course.output_targets[0]
        expected_paths = [str(p) for p, _r in enumerate_expected_outputs(migrated_course, target)]
        assert not any("img-generated" in p for p in expected_paths), (
            "the manifest must enumerate the COLLAPSED output paths — "
            "the build never writes an img-generated/ directory to output"
        )
        diag_paths = [p for p in expected_paths if p.endswith("my_diag.png")]
        assert diag_paths, "the migrated render must still be enumerated"
        assert all(f"img{os.sep}my_diag.png" in p for p in diag_paths)

    def test_a_migrated_course_loads_without_the_legacy_warning(self, migrated_course):
        assert not [
            w
            for w in migrated_course.loading_warnings
            if w["category"] == "legacy_img_render_target"
        ]

    def test_an_unmigrated_course_warns_once_per_load(self, course_1_spec, tmp_path):
        """Review M3: the transitional rule needs a signal — a resurrected
        legacy render silently flips the target back, and unmigrated repos
        should be nudged toward the migration."""
        from clm.core.course import Course

        course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path / "out")
        warnings = [
            w for w in course.loading_warnings if w["category"] == "legacy_img_render_target"
        ]
        assert len(warnings) == 1
        assert "migrate-generated-images" in warnings[0]["message"]
        assert len(warnings[0]["details"]["sources"]) >= 2  # my_diag.pu + my_drawing.drawio


class TestRegistryRecognition:
    def test_is_image_path_accepts_both_directories(self):
        assert is_image_path(Path("slides/module/topic/img/x.png"))
        assert is_image_path(Path("slides/module/topic/img-generated/x.png"))

    def test_is_image_path_still_rejects_unrelated_paths(self):
        assert not is_image_path(Path("slides/module/topic/data/x.png"))


class TestInlineImageFallback:
    """``_inject_data_urls`` resolves references against the SOURCE tree, so a
    reference saying ``img/x.png`` must find a migrated render in
    ``img-generated/`` — silently keeping the tag would un-inline every
    generated diagram after migration."""

    def _inject(self, source_dir: Path, content: str) -> str:
        from clm.workers.notebook.notebook_processor import NotebookProcessor

        payload = SimpleNamespace(source_topic_dir=str(source_dir), other_files={})
        return NotebookProcessor._inject_data_urls(NotebookProcessor, content, payload)

    def test_a_migrated_render_still_inlines(self, tmp_path):
        (tmp_path / "img-generated").mkdir()
        (tmp_path / "img-generated" / "diagram.png").write_bytes(b"\x89PNG fake")
        result = self._inject(tmp_path, '<img src="img/diagram.png">')
        assert result.startswith('<img src="data:image/png;base64,')

    def test_a_hand_authored_image_inlines_from_img(self, tmp_path):
        (tmp_path / "img").mkdir()
        (tmp_path / "img" / "photo.png").write_bytes(b"\x89PNG fake")
        result = self._inject(tmp_path, '<img src="img/photo.png">')
        assert result.startswith('<img src="data:image/png;base64,')

    def test_img_takes_precedence_over_img_generated(self, tmp_path):
        """The reference names ``img/``; if a file genuinely sits there it wins
        (the transitional legacy layout, where the render target IS img/)."""
        import base64

        (tmp_path / "img").mkdir()
        (tmp_path / "img-generated").mkdir()
        (tmp_path / "img" / "diagram.png").write_bytes(b"legacy")
        (tmp_path / "img-generated" / "diagram.png").write_bytes(b"migrated")
        result = self._inject(tmp_path, '<img src="img/diagram.png">')
        assert base64.b64encode(b"legacy").decode() in result

    def test_a_missing_image_keeps_the_original_tag(self, tmp_path):
        content = '<img src="img/absent.png">'
        assert self._inject(tmp_path, content) == content

    def test_the_fallback_only_rewrites_the_img_root(self, tmp_path):
        """A reference outside ``img/`` must not be probed under img-generated."""
        (tmp_path / "img-generated").mkdir()
        (tmp_path / "img-generated" / "x.png").write_bytes(b"render")
        content = '<img src="data/x.png">'
        assert self._inject(tmp_path, content) == content
        assert PurePosixPath("data/x.png").parts[0] != "img"  # the guard the test rides on
