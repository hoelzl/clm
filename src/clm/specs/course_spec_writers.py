import csv
from pathlib import Path

from clm.core.course_spec import CourseSpec
from clm.utils.path_utils import (
    base_path_for_csv_file,
)


class CourseSpecCsvWriter:
    @classmethod
    def to_csv(cls, course_spec: CourseSpec, csv_file: Path) -> None:
        with open(csv_file, 'x', encoding='utf-8', newline='') as csvfile:
            spec_writer = csv.writer(csvfile, delimiter=',', quotechar='"')
            spec_writer.writerow(
                (
                    'Base Dir:',
                    course_spec.base_dir.relative_to(
                        base_path_for_csv_file(csv_file)
                    ).as_posix(),
                )
            )
            spec_writer.writerow(
                (
                    'Target Dir:',
                    course_spec.target_dir.relative_to(
                        base_path_for_csv_file(csv_file)
                    ).as_posix(),
                )
            )
            spec_writer.writerow(
                (
                    'Template Dir:',
                    course_spec.template_dir.relative_to(
                        base_path_for_csv_file(csv_file)
                    ).as_posix(),
                )
            )
            spec_writer.writerow(('Language:', course_spec.lang))
            spec_writer.writerow(
                ('Programming Language:', course_spec.prog_lang)
            )
            spec_writer.writerow(())
            # Write only the first three fields of the spec, ignore the dir
            # number.
            spec_writer.writerows(
                spec[:3] for spec in course_spec.document_specs
            )
