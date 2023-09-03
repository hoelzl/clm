from pathlib import Path

from clm.core.course import Course
from clm.core.data_source import DataSource
from clm.core.output_spec import OutputSpec


def full_target_path_for_data_source(
    doc: DataSource, course: "Course", output_spec: OutputSpec
) -> Path:
    target_base_path = course.target_loc
    if not target_base_path.is_absolute():
        raise ValueError(f"Base path {target_base_path} is not absolute.")

    if _is_special_target_dir_fragment(doc.target_dir_fragment):
        return _process_special_target_dir(doc, course, output_spec)
    else:
        return (
            target_base_path
            / output_spec.target_dir_fragment
            / doc.target_dir_fragment
            / doc.get_target_name(course, output_spec)
        )


def _process_special_target_dir(
    doc: DataSource, course: "Course", output_spec: OutputSpec
):
    match doc.target_dir_fragment:
        case "$keep":
            relative_source_path = doc.source_loc.relative_to(course.source_loc)
            result_path = (
                course.target_loc
                / output_spec.target_root_fragment
                / relative_source_path
            )
            return result_path
        case "$parent":
            relative_source_path = doc.source_loc.relative_to(course.source_loc)
            result_path = (
                course.target_loc
                / output_spec.target_root_fragment
                / "/".join(relative_source_path.parts[1:])
            )
            return result_path
        case "$root":
            return (
                course.target_loc
                / output_spec.target_root_fragment
                / doc.get_target_name(course, output_spec)
            )
        case "$target":
            return (
                course.target_loc
                / output_spec.target_root_fragment
                / output_spec.target_subdir_fragment
                / doc.get_target_name(course, output_spec)
            )
    raise ValueError(f"Unknown special target dir: {doc.target_dir_fragment}")


def _is_special_target_dir_fragment(target_dir_fragment: str):
    """Checks whether a target dir fragment needs special processing.
    >>> _is_special_target_dir_fragment("$root")
    True
    >>> _is_special_target_dir_fragment("Base")
    False
    """
    return target_dir_fragment.startswith("$")
