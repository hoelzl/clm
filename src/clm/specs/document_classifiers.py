from pathlib import Path

from clm.core.directory_role import GeneralDirectory
from clm.core.document_classifier import (
    DocumentClassifier,
    ExactPathToDirectoryRoleFun,
    SubpathToDirectoryRoleFun,
)
from clm.specs.directory_roles import LegacyExampleDirectory, NotebookDirectory


def legacy_python_classifier(base_path: Path) -> DocumentClassifier:
    return DocumentClassifier(
        base_path=base_path,
        default_role=GeneralDirectory(),
        path_to_dir_role_funs=[
            ExactPathToDirectoryRoleFun(
                LegacyExampleDirectory(), [Path('examples')], base_path
            ),
            SubpathToDirectoryRoleFun(
                NotebookDirectory(),
                [
                    Path('python_courses/slides'),
                    Path('python_courses/workshops'),
                ],
                base_path,
            ),
        ],
    )
