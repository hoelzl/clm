from pathlib import Path
from typing import Sequence

from clm.core.directory_kind import DirectoryKind, GeneralDirectory


class CourseLayout:
    def __init__(
        self,
        base_path: Path,
        directory_patterns: Sequence[tuple[str, type[DirectoryKind]]],
        default_directory_type: type[DirectoryKind] = GeneralDirectory,
    ):
        assert base_path.is_absolute()
        self.base_path = base_path
        self.classifier_patterns = directory_patterns
        self.default_classifier_type = default_directory_type
        self.resolved_classifier_paths: dict[Path, DirectoryKind] = {}

    def classify(self, path: Path) -> str:
        """Classify a file or directory in this course."""
        containing_dir = path.parent
        classifier = self._resolve_classifier(containing_dir)
        return classifier.classify(path)

    def _resolve_classifier(self, containing_dir: Path) -> DirectoryKind:
        classifier = self.resolved_classifier_paths.get(containing_dir)
        if classifier is None:
            classifier = self._find_classifier(containing_dir)
            self.resolved_classifier_paths[containing_dir] = classifier
        return classifier

    def _find_classifier(self, containing_dir: Path) -> DirectoryKind:
        for pattern, classifier in self.classifier_patterns:
            if containing_dir.match(pattern):
                return classifier(self.base_path)
        return self.default_classifier_type(self.base_path)
