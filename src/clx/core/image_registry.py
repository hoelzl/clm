"""Image registry for detecting filename collisions across course images.

This module provides the ImageRegistry class which collects all images from a course
and detects when two different images share the same filename (which would cause
collisions in the shared output/img/ folder).
"""

import logging
from pathlib import Path

from attrs import Factory, define

logger = logging.getLogger(__name__)


@define
class ImageCollision:
    """Represents a collision between two or more image files with the same filename.

    Attributes:
        filename: The filename that is duplicated
        paths: List of full paths to the conflicting image files
    """

    filename: str
    paths: list[Path]


@define
class ImageRegistry:
    """Registry that collects images and detects filename collisions.

    The registry tracks all image files by their filename. When two different files
    have the same filename but different content, a collision is recorded.

    Files with identical content are not considered collisions (they can safely
    be deduplicated).

    Attributes:
        _images: Mapping from filename to source path
        _collisions: List of detected collisions
    """

    _images: dict[str, Path] = Factory(dict)
    _collisions: list[ImageCollision] = Factory(list)

    def register(self, source_path: Path) -> None:
        """Register an image file, detecting collisions with different content.

        Args:
            source_path: Full path to the image file
        """
        filename = source_path.name

        if filename in self._images:
            existing = self._images[filename]
            # Same path means same file, not a collision
            if existing == source_path:
                return

            # Check if content differs
            if not self._files_identical(existing, source_path):
                # Check if we already have a collision for this filename
                for collision in self._collisions:
                    if collision.filename == filename:
                        # Add to existing collision if not already there
                        if source_path not in collision.paths:
                            collision.paths.append(source_path)
                        return

                # Create new collision
                self._collisions.append(
                    ImageCollision(filename=filename, paths=[existing, source_path])
                )
                logger.warning(
                    f"Image collision detected: '{filename}' exists at "
                    f"{existing} and {source_path} with different content"
                )
            else:
                logger.debug(
                    f"Image '{filename}' at {source_path} has identical content "
                    f"to {existing}, not a collision"
                )
        else:
            self._images[filename] = source_path
            logger.debug(f"Registered image: {filename} from {source_path}")

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
