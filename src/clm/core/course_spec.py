"""
Specs are descriptions of objects that can be edited as text.

A `CourseSpec` is a description of a complete course.
"""

from dataclasses import dataclass, field
from operator import attrgetter
from pathlib import Path
from typing import (
    TYPE_CHECKING,
)

from clm.core.document_spec import DocumentSpec
from clm.utils.general import find

if TYPE_CHECKING:
    from clm.core.document import Document


SKIP_SPEC_TARGET_DIR_FRAGMENTS = ["-", "", "$skip"]


@dataclass
class CourseSpec:
    base_dir: Path
    target_dir: Path
    template_dir: Path = None
    lang: str = "en"
    document_specs: list[DocumentSpec] = field(default_factory=list, repr=False)
    prog_lang: str = "python"

    def __post_init__(self):
        if self.template_dir is None:
            self.template_dir = self.base_dir / "templates"

    def __iter__(self):
        return iter(self.document_specs)

    def __len__(self):
        return len(self.document_specs)

    def __getitem__(self, item):
        if isinstance(item, int):
            return self.document_specs[item]
        else:
            return find(self.document_specs, item, key=attrgetter("source_file"))

    def merge(
        self, other: "CourseSpec"
    ) -> tuple[list[DocumentSpec], list[DocumentSpec]]:
        """Merge the document specs of `other` into our document specs.

        Equality is checked according to the source files.

        Returns the new and deleted specs."""

        spec: DocumentSpec
        new_specs, remaining_specs, deleted_specs = self._copy_existing_specs(other)
        new_specs.extend(sorted(remaining_specs, key=attrgetter("source_file")))
        return new_specs, deleted_specs

    def _copy_existing_specs(self, other):
        new_specs = []
        deleted_specs = []
        remaining_specs = set(other.document_specs)
        for existing_spec in self.document_specs:
            # Copy the existing spec if its path was not deleted, i.e., if we
            # find a corresponding spec in the remaining specs.
            spec = find(existing_spec, remaining_specs, key=attrgetter("source_file"))
            if spec is not None:
                new_specs.append(existing_spec)
                remaining_specs.remove(spec)
            else:
                deleted_specs.append(existing_spec)
        return new_specs, remaining_specs, deleted_specs

    @property
    def documents(self) -> list["Document"]:
        from clm.core.document import Document

        return [
            Document.from_spec(self, document_spec)
            for document_spec in self.document_specs
            if document_spec.target_dir_fragment not in SKIP_SPEC_TARGET_DIR_FRAGMENTS
        ]
