"""Image registry for detecting filename collisions across course images.

This module provides the ImageRegistry class which collects all images from a course
and detects when two different images share the same relative path from their img/
folder (which would cause collisions in the shared output/img/ folder).

Images in subfolders of img/ (e.g., img/foo/bar.png) are preserved in the same
subfolder structure in the output, so img/foo/bar.png and img/baz/bar.png do not
conflict even though they have the same filename.
"""

import logging
from pathlib import Path

from attrs import Factory, define

logger = logging.getLogger(__name__)


def get_relative_img_path(source_path: Path) -> str:
    """Get the relative path from the img/ folder for an image file.

    For a path like /course/slides/module/topic/img/foo/bar.png, this returns
    "foo/bar.png". For a path like /course/slides/module/topic/img/bar.png,
    this returns "bar.png".

    If no "img" folder is found in the path, returns just the filename.

    Args:
        source_path: Full path to the image file

    Returns:
        The relative path from the img/ folder, using forward slashes
    """
    parts = source_path.parts
    # Find the img folder in the path (searching from the end)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "img":
            # Return all parts after "img" joined with /
            rel_parts = parts[i + 1 :]
            return "/".join(rel_parts)
    # Fallback to just the filename if no img folder found
    return source_path.name


@define
class ImageCollision:
    """Represents a collision between two or more image files with the same relative path.

    Attributes:
        relative_path: The relative path from img/ folder that is duplicated
            (e.g., "foo/bar.png" or just "bar.png")
        paths: List of full paths to the conflicting image files
    """

    relative_path: str
    paths: list[Path]


@define
class ImageRegistry:
    """Registry that collects images and detects relative path collisions.

    The registry tracks all image files by their relative path from the img/ folder.
    For example, img/foo/bar.png is tracked as "foo/bar.png". When two different files
    have the same relative path but different content, a collision is recorded.

    Files with identical content are not considered collisions (they can safely
    be deduplicated).

    Attributes:
        _images: Mapping from relative path (from img/) to source path
        _collisions: List of detected collisions
    """

    _images: dict[str, Path] = Factory(dict)
    _collisions: list[ImageCollision] = Factory(list)

    def register(self, source_path: Path) -> None:
        """Register an image file, detecting collisions with different content.

        Args:
            source_path: Full path to the image file
        """
        rel_path = get_relative_img_path(source_path)

        if rel_path in self._images:
            existing = self._images[rel_path]
            # Same path means same file, not a collision
            if existing == source_path:
                return

            # Check if content differs
            if not self._files_identical(existing, source_path):
                # Check if we already have a collision for this relative path
                for collision in self._collisions:
                    if collision.relative_path == rel_path:
                        # Add to existing collision if not already there
                        if source_path not in collision.paths:
                            collision.paths.append(source_path)
                        return

                # Create new collision
                self._collisions.append(
                    ImageCollision(relative_path=rel_path, paths=[existing, source_path])
                )
                logger.warning(
                    f"Image collision detected: 'img/{rel_path}' exists at "
                    f"{existing} and {source_path} with different content"
                )
            else:
                logger.debug(
                    f"Image 'img/{rel_path}' at {source_path} has identical content "
                    f"to {existing}, not a collision"
                )
        else:
            self._images[rel_path] = source_path
            logger.debug(f"Registered image: img/{rel_path} from {source_path}")

    def _files_identical(self, path1: Path, path2: Path) -> bool:
        """Check if two files have identical content.

        Args:
            path1: First file path
            path2: Second file path

        Returns:
            True if files have identical content, False otherwise
        """
        try:
            return path1.read_bytes() == path2.read_bytes()
        except OSError as e:
            logger.warning(f"Could not compare files {path1} and {path2}: {e}")
            # If we can't read the files, assume they're different to be safe
            return False

    @property
    def collisions(self) -> list[ImageCollision]:
        """Return list of detected collisions."""
        return list(self._collisions)

    @property
    def images(self) -> dict[str, Path]:
        """Return mapping of filename -> source path for all registered images."""
        return dict(self._images)

    def has_collisions(self) -> bool:
        """Check if any collisions have been detected."""
        return len(self._collisions) > 0

    def clear(self) -> None:
        """Clear all registered images and collisions."""
        self._images.clear()
        self._collisions.clear()
