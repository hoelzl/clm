import contextlib
import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class GitDirMover:
    def __init__(self, directories: Iterable[Path], keep_directory: bool = False):
        self.directories = [Path(d) for d in directories]
        self.temp_dir = None
        self.moved_dirs = []
        self.keep_directory = keep_directory

    def __enter__(self):
        if not self.keep_directory:
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
        if not self.keep_directory:
            failures = []

            # Try to restore all moved directories
            for original_path, temp_path in self.moved_dirs:
                logger.debug(f"Moving directory {str(temp_path)} to {str(original_path)}")
                try:
                    shutil.move(str(temp_path), str(original_path))
                except Exception as e:
                    failures.append((original_path, temp_path, e))
                    logger.error(
                        f"Cannot restore directory: {str(temp_path)} -> "
                        f"{str(original_path)}",
                        exc_info=True
                    )

            # Clean up temp directory
            if self.temp_dir:
                try:
                    shutil.rmtree(self.temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to remove temp directory {self.temp_dir}: {e}")

            # If we failed to restore directories, that's a serious problem
            if failures:
                failed_paths = [str(orig) for orig, _, _ in failures]
                error_msg = (
                    f"Failed to restore {len(failures)} .git director{'y' if len(failures) == 1 else 'ies'}: "
                    f"{', '.join(failed_paths)}"
                )
                logger.error(error_msg)

                # Raise exception to alert user of data loss risk
                raise RuntimeError(
                    f"{error_msg}. Git directories may be left in temporary location."
                )


@contextlib.contextmanager
def git_dir_mover(directories: Iterable[Path], keep_directory: bool = False):
    with GitDirMover(directories, keep_directory) as mover:
        yield mover
