"""Base class for course files that generate images."""

from pathlib import Path

from attrs import define

from clm.core.course_file import CourseFile


@define
class ImageFile(CourseFile):
    """Base class for files that convert to images (PNG or SVG).

    This class provides common functionality for diagram files that
    generate images, such as PlantUML and Draw.io files. The output
    format is determined by the course's image_format setting.
    """

    @property
    def generated_img_path(self) -> Path:
        """The #664 render target: ``<topic>/img-generated/<stem>.<ext>``.

        The build-owned sibling of the hand-authored ``img/`` — nothing in
        ``img/`` is ever written by the build, everything here only ever is.
        """
        from clm.core.utils.path_utils import GENERATED_IMG_DIR
        from clm.core.utils.text_utils import sanitize_path

        ext = f".{self.course.image_format}"
        unsanitized = (self.path.parents[1] / GENERATED_IMG_DIR / self.path.stem).with_suffix(ext)
        return sanitize_path(unsanitized)

    @property
    def legacy_img_path(self) -> Path:
        """The pre-#664 render target: ``<topic>/img/<stem>.<ext>``.

        Only still rendered to when a committed legacy render sits there
        (see :attr:`img_path`); ``clm course migrate-generated-images``
        moves such files to :attr:`generated_img_path`.
        """
        from clm.core.utils.text_utils import sanitize_path

        ext = f".{self.course.image_format}"
        unsanitized = (self.path.parents[1] / "img" / self.path.stem).with_suffix(ext)
        return sanitize_path(unsanitized)

    @property
    def img_path(self) -> Path:
        """Path to the generated image.

        Since #664 images render into the build-owned ``img-generated/``
        sibling of ``img/`` — with one transitional exception: a repo that
        still keeps a *committed* render at the legacy ``<topic>/img/``
        location keeps rendering there, so an unmigrated checkout builds
        exactly as before (rendering to the new directory while the stale
        legacy copy still ships as a hand-authored-looking image would have
        both copied to the same output path, resurrecting the #661 conflict
        class). ``clm course migrate-generated-images`` moves the files;
        after that — and for any freshly added diagram — the new directory
        is the only target.

        Disk-dependent, deliberately: the choice is stable within a build
        (the legacy file either exists at course load or it does not), and
        the first render into ``img-generated/`` makes it permanent.
        """
        legacy = self.legacy_img_path
        if legacy.exists():
            return legacy
        return self.generated_img_path

    @property
    def source_outputs(self) -> frozenset[Path]:
        """Image files produce a single image output."""
        return frozenset({self.img_path})
