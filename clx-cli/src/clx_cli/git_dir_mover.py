import contextlib
import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class GitDirMover:
    def __init__(self, directories: Iterable[Path]):
        self.directories = [Path(d) for d in directories]
        self.temp_dir = None
        self.moved_dirs = []

    def __enter__(self):
        self.temp_dir = tempfile.mkdtemp()
        for directory in self.directories:
            for git_dir in directory.rglob(".git"):
                if git_dir.is_dir():
                    relative_path = git_dir.relative_to(directory)
                    temp_path = Path(self.temp_dir) / str(uuid.uuid4()) / relative_path
                    temp_path.parent.mkdir(parents=True, exist_ok=True)
                    logger.debug(f"Moving directory {str(git_dir)} to {str(temp_path)}")
                    shutil.move(str(git_dir), str(temp_path))
                    self.moved_dirs.append((git_dir, temp_path))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for original_path, temp_path in self.moved_dirs:
            logger.debug(f"Moving directory {str(temp_path)} to {str(original_path)}")
            try:
                shutil.move(str(temp_path), str(original_path))
            except Exception as e:
                logger.error(
                    f"Cannot restore directory: {str(temp_path)} -> "
                    f"{str(original_path)}", exc_info=e
                )
        if self.temp_dir:
            shutil.rmtree(self.temp_dir)


@contextlib.contextmanager
def git_dir_mover(directories: Iterable[Path]):
    with GitDirMover(directories) as mover:
        yield mover
