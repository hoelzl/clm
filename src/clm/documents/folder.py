import logging
import shutil
from dataclasses import dataclass

from clm.core.course import Course
from clm.core.document import Document
from clm.core.output_spec import OutputSpec

# FIXME: This should be taken from the course layout.
SKIP_DIRS = [
    "__pycache__",
    ".git",
    ".ipynb_checkpoints",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".vs",
    ".vscode",
    ".idea",
    "build",
    "dist",
    ".cargo",
    ".idea",
    ".vscode",
    "target",
    "out",
]


@dataclass
class Folder(Document):
    def process(self, course, output_spec: OutputSpec):
        pass

    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        return self.source_file.name

    def write_to_target(self, course: "Course", output_spec: OutputSpec):
        target_path = self.get_full_target_path(course=course, output_spec=output_spec)
        logging.info(
            f"Copying folder {self.source_file.as_posix()!r} "
            f"to {target_path.as_posix()!r}."
        )
        if not self.source_file.exists():
            logging.warning(
                f"Trying to copy folder {self.source_file} which does not exist."
            )
        target_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copytree(
            self.source_file,
            target_path,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.egg-info", *SKIP_DIRS),
        )
