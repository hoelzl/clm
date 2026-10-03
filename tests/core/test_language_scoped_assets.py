"""Language-suffixed topic assets (#1034): ``x.de.mp4`` ships to DE only.

An asset whose name carries a ``.de`` / ``.en`` segment right before its final
extension is copied only into that language's outputs; every other asset is
copied into every language as before. The copy operations and the provenance
manifest must agree — the release pipeline copies by manifest, so a manifest
listing ``x.de.mp4`` under EN would ship a path the build never wrote.
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from clm.core.course_files.data_file import DataFile
from clm.core.course_files.duplicated_image_file import DuplicatedImageFile
from clm.core.course_files.shared_image_file import SharedImageFile
from clm.core.operation import Concurrently
from clm.core.utils.path_utils import asset_lang_tag, asset_output_languages

DATA_DIR = Path(__file__).parent.parent / "test-data"
TOPIC_REL = Path("slides/module_000_test_1/topic_100_some_topic_from_test_1")

TAGGED_ASSETS = {
    "img/clip.de.mp4": "de",
    "img/clip.en.mp4": "en",
    "img/chart.de.png": "de",
    "img/chart.en.png": "en",
    "data/table.en.csv": "en",
}
UNTAGGED_ASSETS = [
    "img/clip.mp4",
    "img/model.v2.png",
    "data/data.final.csv",
    "data/speech.wav",
]


class TestAssetLangTag:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("breakout.de.mp4", "de"),
            ("diagram.en.png", "en"),
            ("diagram.de.svg", "de"),
            ("breakout.mp4", None),
            ("model.v2.png", None),
            ("data.final.csv", None),
            ("x.fr.png", None),
            # Only the segment right before the final extension counts.
            ("x.de.tar.gz", None),
            # A bare ``.de.png`` has no stem in front of the tag.
            (".de.png", None),
            ("de.png", None),
            ("noext", None),
        ],
    )
    def test_tag_parsing(self, name, expected):
        assert asset_lang_tag(Path("img") / name) == expected

    def test_output_languages_filter(self):
        assert asset_output_languages(Path("x.de.mp4"), ["de", "en"]) == ["de"]
        assert asset_output_languages(Path("x.en.mp4"), ["de"]) == []
        assert asset_output_languages(Path("x.mp4"), ["de", "en"]) == ["de", "en"]


def _course(tmp_path: Path, course_1_spec, image_mode: str):
    from clm.core.course import Course

    data_dir = tmp_path / "test-data"
    shutil.copytree(DATA_DIR, data_dir)
    topic_dir = data_dir / TOPIC_REL
    for rel in [*TAGGED_ASSETS, *UNTAGGED_ASSETS]:
        path = topic_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode())
    return Course.from_spec(course_1_spec, data_dir, tmp_path / "output", image_mode=image_mode)


def _copy_targets(course) -> set[Path]:
    """Every output path the asset copy operations of *course* would write."""

    async def collect() -> set[Path]:
        targets: set[Path] = set()
        for target in course.output_targets:
            for file in course.files:
                if not isinstance(file, DataFile | DuplicatedImageFile | SharedImageFile):
                    continue
                op = await file.get_processing_operation(target.output_root, target=target)
                if isinstance(op, Concurrently):
                    targets.update(sub.output_file for sub in op.operations)
        return targets

    return asyncio.run(collect())


def _manifest_asset_paths(course) -> set[Path]:
    from clm.core.provenance_manifest import enumerate_expected_outputs

    paths: set[Path] = set()
    for target in course.output_targets:
        for path, record in enumerate_expected_outputs(course, target):
            if record["format"] in ("data", "image"):
                paths.add(path)
    return paths


def _lang_of(course, path: Path) -> str:
    """The output language a copy target belongs to, from its course directory."""
    langs = {lang for lang in ("de", "en") if course.output_dir_name[lang] in path.parts}
    assert len(langs) == 1, path
    return langs.pop()


@pytest.mark.parametrize("image_mode", ["duplicated", "shared"])
class TestPlacement:
    def test_tagged_assets_reach_only_their_language(self, tmp_path, course_1_spec, image_mode):
        course = _course(tmp_path, course_1_spec, image_mode)
        written = _copy_targets(course)
        for rel, lang in TAGGED_ASSETS.items():
            name = Path(rel).name
            copies = [p for p in written if p.name == name]
            assert copies, f"{name} must still be copied"
            assert {_lang_of(course, p) for p in copies} == {lang}, name

    def test_untagged_assets_reach_every_language(self, tmp_path, course_1_spec, image_mode):
        course = _course(tmp_path, course_1_spec, image_mode)
        written = _copy_targets(course)
        for rel in UNTAGGED_ASSETS:
            name = Path(rel).name
            assert {_lang_of(course, p) for p in written if p.name == name} == {"de", "en"}, name

    def test_manifest_matches_the_copies(self, tmp_path, course_1_spec, image_mode):
        """The release pipeline copies by manifest: parity is load-bearing."""
        course = _course(tmp_path, course_1_spec, image_mode)
        assert _manifest_asset_paths(course) == _copy_targets(course)


class TestCodeFormatMedia:
    """Display video/audio (under ``img/``) skip the code format; data media don't.

    The code format (jupytext ``py:light``) cannot show a video; images stay,
    because a ``py:light`` file can be reopened as a notebook and code cells
    read images (``Image.open("img/...")``).
    """

    def _code_dirs(self, course) -> list[Path]:
        from clm.core.utils.path_utils import Format, output_specs

        return [
            spec.output_dir
            for target in course.output_targets
            for spec in output_specs(course, target.output_root, target=target)
            if spec.format == Format.CODE
        ]

    def _in_code_dir(self, path: Path, code_dirs: list[Path]) -> bool:
        return any(path.is_relative_to(d) for d in code_dirs)

    def test_img_video_is_not_copied_into_code_outputs(self, tmp_path, course_1_spec):
        course = _course(tmp_path, course_1_spec, "duplicated")
        code_dirs = self._code_dirs(course)
        assert code_dirs, "course_1 must build the code format for this test to mean anything"
        written = _copy_targets(course)

        for name in ("clip.mp4", "clip.de.mp4"):
            copies = [p for p in written if p.name == name]
            assert copies, name
            assert not any(self._in_code_dir(p, code_dirs) for p in copies), name

    def test_images_and_data_media_still_reach_code_outputs(self, tmp_path, course_1_spec):
        course = _course(tmp_path, course_1_spec, "duplicated")
        code_dirs = self._code_dirs(course)
        written = _copy_targets(course)

        for name in ("model.v2.png", "speech.wav", "data.final.csv"):
            assert any(self._in_code_dir(p, code_dirs) for p in written if p.name == name), name
