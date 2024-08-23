import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from attr import Factory, frozen

from clx.course_file import CourseFile
from clx.utils.notebook_utils import find_images, find_imports
from clx.utils.path_utils import (
    is_ignored_dir_for_course,
    is_in_dir,
    prog_lang_to_extension,
)

if TYPE_CHECKING:
    from clx.course import Course
    from clx.course_files.notebook_file import NotebookFile
    from clx.section import Section

logger = logging.getLogger(__name__)


@frozen
class Topic(ABC):
    id: str
    section: "Section"
    path: Path
    _file_map: dict[Path, CourseFile] = Factory(dict)

    @staticmethod
    def from_id(id: str, section: "Section", path: Path):  # noqa
        cls: type[Topic] = FileTopic if path.is_file() else DirectoryTopic
        return cls(id=id, section=section, path=path)  # noqa

    @property
    def course(self) -> "Course":
        return self.section.course

    @property
    def files(self) -> list[CourseFile]:
        return list(self._file_map.values())

    @property
    def notebooks(self) -> list["NotebookFile"]:
        from clx.course_files.notebook_file import NotebookFile
        return [file for file in self.files if isinstance(file, NotebookFile)]

    @property
    def prog_lang(self):
        return self.course.spec.prog_lang

    def file_for_path(self, path: Path) -> CourseFile:
        return self._file_map.get(path)

    def add_file(self, path: Path):
        # We can add files that don't exist yet (e.g. generated files), so don't check
        # if the path resolves to a file.
        if not self.matches_path(path, False):
            logger.debug(f"Path not within topic: {path}")
            return
        if self.file_for_path(path):
            logger.debug(f"Duplicate path when adding file: {path}")
            return
        if path.is_dir():
            logger.warning(f"Trying to add a directory to topic {self.id!r}: {path}")
            return
        try:
            self._file_map[path] = CourseFile.from_path(self.course, path, self)
        except Exception as e:
            logger.exception("Error adding file %s: %s", path.name, e)
            # TODO: Maybe reraise the exception instead of failing quietly?
            # Revisit this once the app is more stable to better investigate the
            # effects of this change.
            # raise

    @abstractmethod
    def matches_path(self, path: Path, check_is_file: bool = True) -> bool:
        """Returns True if the path is within the topic directory."""
        ...

    @abstractmethod
    def build_file_map(self): ...

    # Helper method for implementing build_file_map
    def add_files_in_dir(self, dir_path):
        for file in sorted(list(dir_path.iterdir())):
            if file.is_file():
                self.add_file(file)
            elif file.is_dir() and not is_ignored_dir_for_course(file):
                for sub_file in file.glob("**/*"):
                    self.add_file(sub_file)


@frozen
class DirectoryTopic(Topic):
    def matches_path(self, path: Path, check_is_file: bool = True) -> bool:
        return is_in_dir(path, self.path, check_is_file)

    def build_file_map(self):
        logger.debug(f"Building file map for file {self.path}")
        self.add_files_in_dir(self.path)


@frozen
class FileTopic(Topic):
    def matches_path(self, path: Path, check_is_file: bool = True) -> bool:
        return is_in_dir(path, self.path.parent, check_is_file)

    def build_file_map(self):
        logger.debug(f"Building file map for directory {self.path}")
        self.add_file(self.path)
        with self.path.open() as f:
            contents = f.read()
        if contents:
            included_images = find_images(contents)
            included_modules = find_imports(contents)
            ext = prog_lang_to_extension(self.prog_lang)
            included_module_files = {module + ext for module in included_modules}
            logger.debug(
                f"Found images: {included_images} and modules: {included_modules}"
            )
            for file in included_images | included_module_files:
                self.add_file(self.path.parent / file)
