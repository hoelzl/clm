from pathlib import Path
from tempfile import TemporaryDirectory

from clx.core.course import Course

# DATA_DIR is defined in tests/conftest.py and available as a fixture
# For direct use, we compute it here
DATA_DIR = Path(__file__).parent.parent / "test-data"


def test_topic_matches_path(topic_1):
    # Existing slides in topic dir match
    assert topic_1.matches_path(topic_1.path / "slides_some_topic_from_test_1.py", False)
    # New slides in topic dir match
    assert topic_1.matches_path(topic_1.path / "slides_new_topic.py", False)
    # Images in the img/ subdirectory match
    assert topic_1.matches_path(topic_1.path / "img/my_image.png", False)
    # PlantUML files in the pu/ subdirectory match
    assert topic_1.matches_path(topic_1.path / "pu/my_diag.pu", False)
    # DrawIO files in the drawio/ subdirectory match
    assert topic_1.matches_path(topic_1.path / "drawio/my_drawing.drawio", False)
    # Deeply nested data files match
    assert topic_1.matches_path(topic_1.path / "test-data/more_data/csv/test.csv", False)

    # Files in other topics do not match
    other_topic = Path(DATA_DIR / "module_010_test_2" / "topic_200_other")
    assert not topic_1.matches_path(other_topic / "slides_a_topic_from_test_2.py", False)

    # Files in the parent module do not match
    assert not topic_1.matches_path(topic_1.path.parent / "slides_in_parent.py", False)


def test_topic_files(course_2_spec):
    with TemporaryDirectory() as out_dir:
        course = Course.from_spec(course_2_spec, DATA_DIR, Path(out_dir))

        unit = course.topics[0]

        assert len(unit.files) == 3
