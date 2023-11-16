import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.core.course_spec import CourseSpec


def copy_files_to_jupyterlite_repo(course_spec):
    course_spec.target_loc.mkdir(exist_ok=True, parents=True)
    jl_dir = jupyterlite_dir(course_spec)

    shutil.copytree(
        course_spec.source_loc.absolute() / "metadata/jupyterlite",
        jl_dir,
        dirs_exist_ok=True,
    )
    shutil.copytree(
        course_spec.target_loc.absolute() / "public/Notebooks",
        jl_dir / "content/Notebooks",
        dirs_exist_ok=True,
    )
    if (course_spec.target_loc / "public/examples").exists():
        shutil.copytree(
            course_spec.target_loc.absolute() / "public/examples",
            jl_dir / "content/examples",
            dirs_exist_ok=True,
        )


def jupyterlite_dir(course_spec: "CourseSpec") -> Path:
    return course_spec.target_loc.absolute() / "jupyterlite"


def jupyterlite_git_dir(course_spec: "CourseSpec") -> Path:
    return jupyterlite_dir(course_spec) / ".git"
