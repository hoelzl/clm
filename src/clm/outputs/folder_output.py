import logging
import shutil
from attr import define
from typing import TYPE_CHECKING

from clm.core.document_paths import full_target_path_for_document
from clm.core.output import Output

if TYPE_CHECKING:
    from clm.documents.folder import Folder

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


@define
class FolderOutput(Output):
    doc: "Folder"

    def write_to_target(self, course, output_spec):
        target_path = full_target_path_for_document(
            self.doc, course=course, output_spec=output_spec
        )
        logging.info(
            f"Copying folder {self.doc.source_file.as_posix()!r} "
            f"to {target_path.as_posix()!r}."
        )
        if not self.doc.source_file.exists():
            logging.warning(
                f"Trying to copy folder {self.doc.source_file} which does not exist."
            )
        target_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copytree(
            self.doc.source_file,
            target_path,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.egg-info", *SKIP_DIRS),
        )
