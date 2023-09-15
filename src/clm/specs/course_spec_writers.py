import csv
from pathlib import Path

from clm.core.course_spec import CourseSpec


class CourseSpecCsvWriter:
    @classmethod
    def to_csv(cls, course_spec: CourseSpec, csv_file: Path) -> None:
        with open(csv_file, "x", encoding="utf-8") as csvfile:
            spec_writer = csv.writer(
                csvfile, delimiter=",", quotechar='"', lineterminator="\n"
            )
            root_dir = course_spec.source_loc.parent.absolute()
            base_dir = course_spec.source_loc.absolute().relative_to(root_dir)
            target_dir = course_spec.target_loc.absolute().relative_to(root_dir)
            template_dir = course_spec.template_loc.absolute().relative_to(root_dir)
            spec_writer.writerow(("Base Dir:", base_dir.as_posix()))
            spec_writer.writerow(("Target Dir:", target_dir.as_posix()))
            spec_writer.writerow(("Template Dir:", template_dir.as_posix()))
            spec_writer.writerow(("Language:", course_spec.lang))
            spec_writer.writerow(("Programming Language:", course_spec.prog_lang))
            spec_writer.writerow(("Course Layout:", course_spec.layout.name))
            spec_writer.writerow(())
            # Write only the first three fields of the spec, ignore the dir
            # number.
            spec_writer.writerows(
                spec.get_output_tuple() for spec in course_spec.data_source_specs
            )
