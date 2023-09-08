import csv
import logging
from collections import defaultdict
from pathlib import Path, PurePath
from typing import Callable

from clm.core.course_layout import get_course_layout
from clm.core.course_spec import CourseSpec
from clm.core.data_source_spec import DataSourceSpec
from clm.utils.location import Location
from clm.utils.path_utils import PathOrStr, base_path_for_csv_file
import clm.specs.directory_kinds  # type: ignore

HEADER_LENGTH = 6


class CourseSpecCsvReader:
    @classmethod
    def read_csv(
        cls, path: PathOrStr, location_factory: Callable[[PathOrStr, str], Location]
    ) -> CourseSpec:
        path = Path(path).absolute()
        with open(path, "r", encoding="utf-8", newline="") as csv_file:
            return cls.read_csv_from_stream(
                csv_file, base_path_for_csv_file(path), location_factory
            )

    @classmethod
    def read_csv_from_stream(
        cls,
        csv_stream,
        root_dir: PathOrStr,
        location_factory: Callable[[PathOrStr, str], Location],
    ):
        """Read the spec (in CSV format) from a stream.

        Resolve relative paths against `root_dir`."""

        if isinstance(root_dir, str):
            root_dir = Path(root_dir)
        assert root_dir.is_absolute()
        csv_entries = list(csv.reader(csv_stream))
        (
            course_loc,
            target_loc,
            template_loc,
            lang,
            prog_lang,
            course_layout,
        ) = cls.parse_csv_header(csv_entries, root_dir, location_factory)
        file_counters = defaultdict(int)
        data_source_specs = []
        for data in csv_entries[HEADER_LENGTH:]:
            if data:
                if len(data) == 3:
                    source_file, target_dir_fragment, kind = data
                    source_loc = course_loc / source_file
                    if source_file.startswith("#"):
                        continue  # line is temporarily commented out
                    counter_key = (target_dir_fragment, kind)
                    file_num = file_counters[counter_key] + 1
                    file_counters[counter_key] = file_num
                    data_source_specs.append(
                        DataSourceSpec(source_loc, target_dir_fragment, kind, file_num)
                    )
                else:
                    logging.error(f"Skipping bad entry in CSV file: {data}.")
        return CourseSpec(
            base_loc=course_loc,
            target_loc=target_loc,
            template_loc=template_loc,
            lang=lang,
            prog_lang=prog_lang,
            data_source_specs=data_source_specs,
            layout=get_course_layout(course_layout),
        )

    CsvFileHeader = tuple[Location, Location, Location, str, str, str]

    @classmethod
    def parse_csv_header(
        cls,
        csv_entries: list[list[str]],
        root_dir: PurePath,
        location_factory: Callable[[PathOrStr, str], Location],
    ) -> CsvFileHeader:
        cls._assert_header_is_correct(csv_entries)
        base_loc = location_factory(root_dir / csv_entries[0][1].strip(), "")
        output_loc = location_factory(root_dir / csv_entries[1][1].strip(), "")
        template_loc_str = csv_entries[2][1].strip()
        template_loc = (
            location_factory(template_loc_str, "")
            if PurePath(template_loc_str).is_absolute()
            else location_factory(root_dir / template_loc_str, "")
        )
        return (
            base_loc,
            output_loc,
            template_loc,
            csv_entries[3][1].strip(),
            csv_entries[4][1].strip(),
            csv_entries[5][1].strip(),
        )

    @classmethod
    def _assert_header_is_correct(cls, csv_entries: list[list[str]]) -> None:
        try:
            if csv_entries[0][0].strip() != "Base Dir:":
                raise ValueError(
                    "Bad CSV file: Expected base dir entry, got " f"{csv_entries[0]}."
                )
            if csv_entries[1][0].strip() != "Target Dir:":
                raise ValueError(
                    "Bad CSV file: Expected target dir entry, got " f"{csv_entries[1]}."
                )
            if csv_entries[2][0].strip() != "Template Dir:":
                raise ValueError(
                    "Bad CSV file: Expected template dir entry, got "
                    f"{csv_entries[2]}."
                )
            if csv_entries[3][0].strip() != "Language:":
                raise ValueError(
                    "Bad CSV file: Expected language entry, got " f"{csv_entries[3]}."
                )
            # Fix CSV files without Programming Language entry:
            if not csv_entries[4]:
                csv_entries.insert(4, ["Programming Language:", "python"])
            if csv_entries[4][0].strip() != "Programming Language:":
                raise ValueError(
                    "Bad CSV file: Expected programming language entry, got "
                    f"{csv_entries[4]}."
                )
            # Fix CSV files without Course Layout entry:
            if not csv_entries[5]:
                # Use the programming language name as course layout name, except for
                # python courses where we default to the legacy layout
                course_layout = csv_entries[4][1].strip()
                if course_layout == "python":
                    course_layout = "legacy_python"
                csv_entries.insert(5, ["Course Layout:", course_layout])
            if csv_entries[5][0].strip() != "Course Layout:":
                raise ValueError(
                    "Bad CSV file: Expected course layout entry, got "
                    f"{csv_entries[5]}."
                )
            if csv_entries[HEADER_LENGTH] and any(csv_entries[HEADER_LENGTH]):
                raise ValueError(
                    "Bad CSV file: Expected empty line, got "
                    f"{csv_entries[HEADER_LENGTH]}."
                )
        except IndexError:
            raise ValueError(
                f"Bad CSV file: Incomplete header: " f"{csv_entries[:HEADER_LENGTH]}."
            )
