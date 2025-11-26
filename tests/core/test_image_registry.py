"""Tests for ImageRegistry collision detection."""

from pathlib import Path

import pytest

from clx.core.image_registry import ImageCollision, ImageRegistry


class TestImageRegistry:
    """Tests for the ImageRegistry class."""

    def test_register_single_image(self, tmp_path):
        """Test registering a single image file."""
        registry = ImageRegistry()
        img_path = tmp_path / "image.png"
        img_path.write_bytes(b"PNG data")

        registry.register(img_path)

        assert len(registry.images) == 1
        assert "image.png" in registry.images
        assert registry.images["image.png"] == img_path
        assert not registry.has_collisions()

    def test_register_multiple_unique_images(self, tmp_path):
        """Test registering multiple images with unique names."""
        registry = ImageRegistry()
        img1 = tmp_path / "image1.png"
        img2 = tmp_path / "image2.png"
        img1.write_bytes(b"PNG data 1")
        img2.write_bytes(b"PNG data 2")

        registry.register(img1)
        registry.register(img2)

        assert len(registry.images) == 2
        assert not registry.has_collisions()

    def test_no_collision_same_path(self, tmp_path):
        """Test that registering the same file twice is not a collision."""
        registry = ImageRegistry()
        img_path = tmp_path / "image.png"
        img_path.write_bytes(b"PNG data")

        registry.register(img_path)
        registry.register(img_path)

        assert len(registry.images) == 1
        assert not registry.has_collisions()

    def test_no_collision_same_content(self, tmp_path):
        """Test that files with same name but identical content are not collisions."""
        registry = ImageRegistry()
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        img1 = dir1 / "image.png"
        img2 = dir2 / "image.png"
        # Same content
        img1.write_bytes(b"identical content")
        img2.write_bytes(b"identical content")

        registry.register(img1)
        registry.register(img2)

        # First one registered wins, no collision
        assert len(registry.images) == 1
        assert registry.images["image.png"] == img1
        assert not registry.has_collisions()

    def test_collision_detected_different_content(self, tmp_path):
        """Test that files with same name but different content cause collision."""
        registry = ImageRegistry()
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        img1 = dir1 / "image.png"
        img2 = dir2 / "image.png"
        # Different content
        img1.write_bytes(b"content A")
        img2.write_bytes(b"content B")

        registry.register(img1)
        registry.register(img2)

        assert registry.has_collisions()
        assert len(registry.collisions) == 1

        collision = registry.collisions[0]
        assert collision.filename == "image.png"
        assert img1 in collision.paths
        assert img2 in collision.paths

    def test_multiple_collisions_for_same_filename(self, tmp_path):
        """Test that multiple files with same name are all tracked in collision."""
        registry = ImageRegistry()
        dirs = [tmp_path / f"dir{i}" for i in range(3)]
        for d in dirs:
            d.mkdir()

        imgs = []
        for i, d in enumerate(dirs):
            img = d / "image.png"
            img.write_bytes(f"unique content {i}".encode())
            imgs.append(img)

        for img in imgs:
            registry.register(img)

        assert registry.has_collisions()
        assert len(registry.collisions) == 1

        collision = registry.collisions[0]
        assert collision.filename == "image.png"
        # All three paths should be in the collision
        for img in imgs:
            assert img in collision.paths

    def test_collision_includes_paths(self, tmp_path):
        """Test that collision error includes all conflicting file paths."""
        registry = ImageRegistry()
        dir1 = tmp_path / "section1" / "topic1" / "img"
        dir2 = tmp_path / "section2" / "topic2" / "img"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)

        img1 = dir1 / "diagram.png"
        img2 = dir2 / "diagram.png"
        img1.write_bytes(b"diagram version 1")
        img2.write_bytes(b"diagram version 2")

        registry.register(img1)
        registry.register(img2)

        collision = registry.collisions[0]
        assert str(img1) in [str(p) for p in collision.paths]
        assert str(img2) in [str(p) for p in collision.paths]

    def test_clear_resets_registry(self, tmp_path):
        """Test that clear() resets all state."""
        registry = ImageRegistry()
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        img1 = dir1 / "image.png"
        img2 = dir2 / "image.png"
        img1.write_bytes(b"content A")
        img2.write_bytes(b"content B")

        registry.register(img1)
        registry.register(img2)

        assert registry.has_collisions()

        registry.clear()

        assert len(registry.images) == 0
        assert not registry.has_collisions()

    def test_images_property_returns_copy(self, tmp_path):
        """Test that images property returns a copy, not the internal dict."""
        registry = ImageRegistry()
        img_path = tmp_path / "image.png"
        img_path.write_bytes(b"PNG data")

        registry.register(img_path)

        images = registry.images
        images["new_key"] = Path("fake")

        # Original should be unchanged
        assert "new_key" not in registry.images

    def test_collisions_property_returns_copy(self, tmp_path):
        """Test that collisions property returns a copy."""
        registry = ImageRegistry()
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        img1 = dir1 / "image.png"
        img2 = dir2 / "image.png"
        img1.write_bytes(b"content A")
        img2.write_bytes(b"content B")

        registry.register(img1)
        registry.register(img2)

        collisions = registry.collisions
        original_len = len(collisions)
        collisions.append(ImageCollision("fake", []))

        # Original should be unchanged
        assert len(registry.collisions) == original_len


class TestImageCollision:
    """Tests for the ImageCollision dataclass."""

    def test_collision_attributes(self):
        """Test ImageCollision stores filename and paths."""
        paths = [Path("/a/img.png"), Path("/b/img.png")]
        collision = ImageCollision(filename="img.png", paths=paths)

        assert collision.filename == "img.png"
        assert collision.paths == paths
