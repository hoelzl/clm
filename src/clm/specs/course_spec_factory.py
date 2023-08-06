from operator import attrgetter
from pathlib import Path

from clm.core.course_spec import CourseSpec, find_potential_course_files
from clm.core.document_spec import DocumentSpec
from clm.specs.course_spec_readers import CourseSpecCsvReader
from clm.specs.document_spec_factory import DocumentSpecFactory
from clm.utils.path_utils import (
    PathOrStr,
)


class CourseSpecFactory:
    @classmethod
    def from_dir(
        cls,
        base_dir: PathOrStr,
        target_dir: PathOrStr,
        template_dir: PathOrStr | None = None,
    ) -> 'CourseSpec':
        base_dir = Path(base_dir)
        target_dir = Path(target_dir)
        if template_dir is not None:
            template_dir = Path(template_dir)
        return CourseSpec(
            base_dir=base_dir,
            target_dir=target_dir,
            template_dir=template_dir,
            document_specs=list(cls._create_document_specs(base_dir)),
        )

    @staticmethod
    def _create_document_specs(base_dir: Path):
        spec_factory = DocumentSpecFactory(base_dir)
        document_specs = (
            spec_factory.create_document_spec(file, file_num)
            # FIXME: use separate counters by file kind, not only by directory.
            for file_num, file in enumerate(
                find_potential_course_files(base_dir), 1
            )
        )
        return sorted(document_specs, key=attrgetter('source_file'))


# %%
def create_course_spec_file(
    spec_file: Path,
    course_dir: Path,
    target_dir: Path,
    lang: str | None = None,
    prog_lang: str | None = None,
    remove_existing=False,
    starting_spec_file: Path | None = None,
):
    if remove_existing:
        spec_file.unlink(missing_ok=True)

    course_spec = CourseSpecFactory.from_dir(course_dir, target_dir)
    if lang:
        course_spec.lang = lang.lower()
    if prog_lang:
        course_spec.prog_lang = prog_lang.lower()
    if starting_spec_file:
        print(f'Replacing document specs with {starting_spec_file}')
        # If we have a starting spec we replace the documents in the spec file.
        starting_spec = CourseSpecCsvReader.read_csv(starting_spec_file)
        course_spec.document_specs = starting_spec.document_specs
    course_spec.to_csv(spec_file)


# %%
def update_course_spec_file(
    spec_file: Path,
) -> tuple[CourseSpec, list[DocumentSpec]]:
    """Update a spec file to reflect changes in its sources."""
    spec = CourseSpecCsvReader.read_csv(spec_file)
    spec_from_dir = CourseSpecFactory.from_dir(
        base_dir=spec.base_dir,
        target_dir=spec.target_dir,
        template_dir=spec.template_dir,
    )
    merged_specs, deleted_specs = spec.merge(spec_from_dir)
    spec.document_specs = merged_specs
    return spec, deleted_specs
