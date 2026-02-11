"""Tests for ImageRegistry collision detection."""

from pathlib import Path

import pytest

from clm.core.image_registry import ImageCollision, ImageRegistry, get_relative_img_path


class TestGetRelativeImgPath:
    """Tests for the get_relative_img_path function."""

    def test_simple_img_folder(self, tmp_path):
        """Test image directly in img/ folder."""
        img_path = tmp_path / "topic" / "img" / "image.png"
        assert get_relative_img_path(img_path) == "image.png"

    def test_nested_in_img_folder(self, tmp_path):
        """Test image in subfolder of img/ folder."""
        img_path = tmp_path / "topic" / "img" / "foo" / "bar.png"
        assert get_relative_img_path(img_path) == "foo/bar.png"

    def test_deeply_nested_in_img_folder(self, tmp_path):
        """Test image in deeply nested subfolder of img/ folder."""
        img_path = tmp_path / "topic" / "img" / "a" / "b" / "c" / "image.png"
        assert get_relative_img_path(img_path) == "a/b/c/image.png"

    def test_no_img_folder_returns_filename(self, tmp_path):
        """Test fallback to filename when no img/ folder in path."""
        img_path = tmp_path / "other" / "folder" / "image.png"
        assert get_relative_img_path(img_path) == "image.png"

    def test_multiple_img_folders_uses_last(self, tmp_path):
        """Test that the last img/ folder in path is used."""
        # This edge case: img/something/img/final.png should use "final.png"
        img_path = tmp_path / "img" / "nested" / "img" / "final.png"
        assert get_relative_img_path(img_path) == "final.png"

    def test_realistic_course_path(self, tmp_path):
        """Test with a realistic course structure path."""
        img_path = tmp_path / "slides" / "module_100" / "topic_intro" / "img" / "diagram.png"
        assert get_relative_img_path(img_path) == "diagram.png"

    def test_realistic_course_path_with_subfolder(self, tmp_path):
        """Test with a realistic course structure path with subfolder."""
        img_path = (
            tmp_path / "slides" / "module_100" / "topic_intro" / "img" / "charts" / "diagram.png"
        )
        assert get_relative_img_path(img_path) == "charts/diagram.png"


class TestImageRegistry:
    """Tests for the ImageRegistry class."""

    def test_register_single_image(self, tmp_path):
        """Test registering a single image file."""
        registry = ImageRegistry()
        img_dir = tmp_path / "img"
        img_dir.mkdir()
        img_path = img_dir / "image.png"
        img_path.write_bytes(b"PNG data")

        registry.register(img_path)

        assert len(registry.images) == 1
        assert "image.png" in registry.images
        assert registry.images["image.png"] == img_path
        assert not registry.has_collisions()

    def test_register_multiple_unique_images(self, tmp_path):
        """Test registering multiple images with unique names."""
        registry = ImageRegistry()
        img_dir = tmp_path / "img"
        img_dir.mkdir()
        img1 = img_dir / "image1.png"
        img2 = img_dir / "image2.png"
        img1.write_bytes(b"PNG data 1")
        img2.write_bytes(b"PNG data 2")

        registry.register(img1)
        registry.register(img2)

        assert len(registry.images) == 2
        assert not registry.has_collisions()

    def test_no_collision_same_path(self, tmp_path):
        """Test that registering the same file twice is not a collision."""
        registry = ImageRegistry()
        img_dir = tmp_path / "img"
        img_dir.mkdir()
        img_path = img_dir / "image.png"
        img_path.write_bytes(b"PNG data")

        registry.register(img_path)
        registry.register(img_path)

        assert len(registry.images) == 1
        assert not registry.has_collisions()

    def test_no_collision_same_content(self, tmp_path):
        """Test that files with same relative path but identical content are not collisions."""
        registry = ImageRegistry()
        dir1 = tmp_path / "topic1" / "img"
        dir2 = tmp_path / "topic2" / "img"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)

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
        """Test that files with same relative path but different content cause collision."""
        registry = ImageRegistry()
        dir1 = tmp_path / "topic1" / "img"
        dir2 = tmp_path / "topic2" / "img"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)

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
        assert collision.relative_path == "image.png"
        assert img1 in collision.paths
        assert img2 in collision.paths

    def test_no_collision_different_subfolders(self, tmp_path):
        """Test that images in different subfolders don't collide even with same filename."""
        registry = ImageRegistry()
        dir1 = tmp_path / "topic1" / "img" / "foo"
        dir2 = tmp_path / "topic2" / "img" / "bar"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)

        # Same filename but different subfolders
        img1 = dir1 / "image.png"
        img2 = dir2 / "image.png"
        img1.write_bytes(b"content A")
        img2.write_bytes(b"content B")

        registry.register(img1)
        registry.register(img2)

        # No collision because relative paths are different: foo/image.png vs bar/image.png
        assert not registry.has_collisions()
        assert len(registry.images) == 2
        assert "foo/image.png" in registry.images
        assert "bar/image.png" in registry.images

    def test_collision_same_subfolder_path(self, tmp_path):
        """Test that images in same relative subfolder path do collide."""
        registry = ImageRegistry()
        dir1 = tmp_path / "topic1" / "img" / "charts"
        dir2 = tmp_path / "topic2" / "img" / "charts"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)

        # Same relative path: charts/diagram.png
        img1 = dir1 / "diagram.png"
        img2 = dir2 / "diagram.png"
        img1.write_bytes(b"version 1")
        img2.write_bytes(b"version 2")

        registry.register(img1)
        registry.register(img2)

        # Should collide because both have relative path "charts/diagram.png"
        assert registry.has_collisions()
        collision = registry.collisions[0]
        assert collision.relative_path == "charts/diagram.png"

    def test_multiple_collisions_for_same_relative_path(self, tmp_path):
        """Test that multiple files with same relative path are all tracked in collision."""
        registry = ImageRegistry()
        topics = [tmp_path / f"topic{i}" / "img" for i in range(3)]
        for t in topics:
            t.mkdir(parents=True)

        imgs = []
        for i, t in enumerate(topics):
            img = t / "image.png"
            img.write_bytes(f"unique content {i}".encode())
            imgs.append(img)

        for img in imgs:
            registry.register(img)

        assert registry.has_collisions()
        assert len(registry.collisions) == 1

        collision = registry.collisions[0]
        assert collision.relative_path == "image.png"
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
        dir1 = tmp_path / "topic1" / "img"
        dir2 = tmp_path / "topic2" / "img"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)

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
        img_dir = tmp_path / "img"
        img_dir.mkdir()
        img_path = img_dir / "image.png"
        img_path.write_bytes(b"PNG data")

        registry.register(img_path)

        images = registry.images
        images["new_key"] = Path("fake")

        # Original should be unchanged
        assert "new_key" not in registry.images

    def test_collisions_property_returns_copy(self, tmp_path):
        """Test that collisions property returns a copy."""
        registry = ImageRegistry()
        dir1 = tmp_path / "topic1" / "img"
        dir2 = tmp_path / "topic2" / "img"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)

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
        """Test ImageCollision stores relative_path and paths."""
        paths = [Path("/a/img/image.png"), Path("/b/img/image.png")]
        collision = ImageCollision(relative_path="image.png", paths=paths)

        assert collision.relative_path == "image.png"
        assert collision.paths == paths

    def test_collision_with_subfolder(self):
        """Test ImageCollision with subfolder in relative path."""
        paths = [Path("/a/img/charts/diagram.png"), Path("/b/img/charts/diagram.png")]
        collision = ImageCollision(relative_path="charts/diagram.png", paths=paths)

        assert collision.relative_path == "charts/diagram.png"
        assert collision.paths == paths
