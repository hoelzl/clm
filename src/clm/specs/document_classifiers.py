from pathlib import Path

from clm.core.directory_role import GeneralDirectory
from clm.core.document_classifier import (
    DocumentClassifier,
    ExactPathToDirectoryRoleFun,
    SubpathToDirectoryRoleFun,
)
from clm.specs.directory_roles import LegacyExampleDirectory, NotebookDirectory

LEGACY_PYTHON_CLASSIFIER = DocumentClassifier(
    default_role=GeneralDirectory(),
    path_to_dir_role_funs=[
        ExactPathToDirectoryRoleFun(
            LegacyExampleDirectory(), [Path('examples')]
        ),
        SubpathToDirectoryRoleFun(
            NotebookDirectory(), [Path('python_courses/slides')]
        ),
    ],
)
