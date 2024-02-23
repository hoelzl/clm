import re
from pathlib import Path

import pytest

from clm.core.course_layout import (
    PathClassifier,
    CourseLayout,
    NOTEBOOK_REGEX,
    NOTEBOOK_SUBDIR_REGEX,
)
from clm.core.course_layout import (
    get_course_layout,
    course_layout_to_dict,
    SKIP_DIRS,
    course_layout_from_dict,
)
from clm.core.directory_kind import (
    IGNORED_LABEL,
    GeneralDirectory,
    PLAIN_FILE_LABEL,
    EXAMPLE_SOLUTION_LABEL,
    EXAMPLE_STARTER_KIT_LABEL,
    NOTEBOOK_LABEL,
    FOLDER_LABEL,
)
from clm.specs.directory_kinds import (
    ExampleDirectory,
    NotebookDirectory,
    LegacyExampleDirectory,
)
from clm.utils.in_memory_filesystem import InMemoryFilesystem
from clm.utils.location import convert_to_in_memory_filesystem, InMemoryLocation


# noinspection PyUnusedLocal
def test_get_course_layout_returns_existing_layout(mock_layout):
    layout = get_course_layout("mock_layout")
    assert isinstance(layout, CourseLayout)
    assert layout.name == "mock_layout"


def test_get_course_layout_raises_error_for_non_existing_layout():
    with pytest.raises(ValueError, match="Unknown course layout: non_existing_layout"):
        get_course_layout("non_existing_layout")


# noinspection PyUnusedLocal
def test_course_layout_to_dict(mock_layout):
    layout = get_course_layout("mock_layout")
    assert course_layout_to_dict(layout) == {
        "name": "mock_layout",
        "directory_patterns": [["data", "GeneralDirectory"]],
        "kept_files": ["__init__.py", "__main__.py"],
        "ignored_files": [".gitignore"],
        "ignored_files_regex": "^[_.](.*)(\\.*)?",
        "ignored_directories": list(SKIP_DIRS),
        "ignored_directories_regex": "(.*\\.egg-info.*|.*cmake-build-.*)",
        "notebook_regex": NOTEBOOK_REGEX.pattern,
        "notebook_subdir_regex": NOTEBOOK_SUBDIR_REGEX.pattern,
        "default_directory_kind": "GeneralDirectory",
    }


# noinspection PyUnusedLocal
def test_course_layout_from_dict(mock_layout):
    layout = get_course_layout("mock_layout")
    layout_dict = course_layout_to_dict(layout)
    assert course_layout_from_dict(layout_dict) == layout


# noinspection PyUnusedLocal
def test_course_layout_from_dict_with_defaults(mock_layout):
    base_dir = Path("/foo/bar")
    layout = get_course_layout("mock_layout")
    layout_dict = {
        "name": "mock_layout",
        "base_path": str(base_dir),
        "directory_patterns": [["data", "GeneralDirectory"]],
    }
    assert course_layout_from_dict(layout_dict) == layout


@pytest.fixture
def course_layout():
    return CourseLayout(
        name="test_layout",
        default_directory_kind=GeneralDirectory(),
        directory_patterns=(
            ("examples", ExampleDirectory),
            ("legacy_examples", LegacyExampleDirectory),
            ("notebooks", NotebookDirectory),
        ),
        notebook_subdir_regex=re.compile(r"^data$"),
    )


@pytest.fixture
def course_files() -> InMemoryFilesystem:
    return convert_to_in_memory_filesystem(
        {
            "root_file.txt": "Contents of root file",
            "examples": {
                "example_root.txt": "Contents of example_root.txt",
                "my_example": {"example_file.py": "Contents of example_file.py"},
                "my_example_starter_kit": {
                    "starter_kit_file_1.py": "Contents of starter_kit_file_1.py"
                },
                "my_example_sk": {
                    "starter_kit_file_2.py": "Contents of starter_kit_file_2.py"
                },
                "MyExampleStarterKit": {
                    "starter_kit_file_3.py": "Contents of starter_kit_file_3.py"
                },
                "MyExampleSK": {
                    "starter_kit_file_4.py": "Contents of starter_kit_file_4.py"
                },
            },
            "notebooks": {
                "topic_100_python.ipynb": "Contents of topic_100_python.ipynb",
                "topic_110_loops.py": "Contents of topic_110_loops.py",
                "notebook_source.ipynb": "Contents of notebook_source.ipynb",
                "python_file.py": "Contents of python_file.py",
                "img": {"my_notebook_img.png": "Contents of my_notebook_img.png"},
                "data": {"my_notebook_data.csv": "Contents of my_notebook_data.csv"},
            },
            "data": {
                "my_data_file.csv": "Contents of my_data_file",
                "data_subdir": {"my_data_file_2.csv": "Contents of my_data_file_2"},
            },
        }
    )


class TestPathClassifier:
    @pytest.fixture
    def classifier(self, course_layout):
        return PathClassifier(course_layout)

    @pytest.fixture
    def course_dir(self, course_files):
        return InMemoryLocation("/course", "", course_files)

    def test_file_in_root_directory(self, classifier, course_dir):
        assert classifier.classify(course_dir / "root_file.txt") == PLAIN_FILE_LABEL

    def test_folder_in_root_directory(self, classifier, course_dir):
        assert classifier.classify(course_dir / "data") == IGNORED_LABEL

    def test_file_in_general_directory(self, classifier, course_dir):
        assert (
            classifier.classify(course_dir / "data/my_data_file.csv")
            == PLAIN_FILE_LABEL
        )

    def test_folder_in_general_directory(self, classifier, course_dir):
        assert classifier.classify(course_dir / "data/data_subdir") == IGNORED_LABEL

    def test_file_in_examples_directory_root(self, classifier, course_dir):
        assert classifier.classify(course_dir / "example_root.txt") == IGNORED_LABEL

    def test_subdir_in_examples_directory(self, classifier, course_dir):
        assert (
            classifier.classify(course_dir / "examples/my_example")
            == EXAMPLE_SOLUTION_LABEL
        )

    def test_file_in_examples_directory(self, classifier, course_dir):
        assert (
            classifier.classify(course_dir / "my_example/example_file.py")
            == IGNORED_LABEL
        )

    @pytest.mark.parametrize(
        "name",
        [
            "my_example_starter_kit",
            "my_example_sk",
            "MyExampleStarterKit",
            "MyExampleSK",
        ],
    )
    def test_starter_kit_subdir_in_examples_directory(
        self, classifier, course_dir, name
    ):
        assert (
            classifier.classify(course_dir / "examples" / name)
            == EXAMPLE_STARTER_KIT_LABEL
        )

    def test_ipynb_notebook_in_notebook_dir(self, classifier, course_dir):
        result = classifier.classify(course_dir / "notebooks/topic_100_python.ipynb")
        assert result == NOTEBOOK_LABEL

    def test_py_notebook_in_notebook_dir(self, classifier, course_dir):
        result = classifier.classify(course_dir / "notebooks/topic_110_loops.py")
        assert result == NOTEBOOK_LABEL

    def test_py_file_in_notebook_dir(self, classifier, course_dir):
        result = classifier.classify(course_dir / "notebooks/python_file.py")
        assert result == PLAIN_FILE_LABEL

    def test_ipynb_file_in_notebook_dir(self, classifier, course_dir):
        # This tests for a file with the '.ipynb' extension that does not match
        # the notebook regex.
        result = classifier.classify(course_dir / "notebooks/notebook_source.ipynb")
        assert result == NOTEBOOK_LABEL

    def test_subdir_in_notebook_dir(self, classifier, course_dir):
        assert classifier.classify(course_dir / "notebooks/img") == IGNORED_LABEL

    def test_data_subdir_in_notebook_dir(self, classifier, course_dir):
        assert classifier.classify(course_dir / "notebooks/data") == FOLDER_LABEL

    def test_file_in_notebook_subdir(self, classifier, course_dir):
        assert (
            classifier.classify(course_dir / "notebooks/img/my_notebook_img.png")
            == PLAIN_FILE_LABEL
        )
