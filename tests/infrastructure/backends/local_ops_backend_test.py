import shutil
from pathlib import Path

from clm.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clm.infrastructure.messaging.base_classes import Payload
from clm.infrastructure.operation import Operation
from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData
from clm.infrastructure.utils.copy_file_data import CopyFileData


# PytestLocalOpsBackend is defined here (copied from conftest.py)
class PytestLocalOpsBackend(LocalOpsBackend):
    async def execute_operation(self, operation: Operation, payload: Payload) -> None:
        pass

    async def wait_for_completion(self, all_submitted=None) -> bool:
        return True


async def test_copy_file(tmp_path):
    infile = tmp_path / "input_file.txt"
    outfile = tmp_path / "output_file.txt"
    infile.write_text("Some Text", encoding="utf-8")
    copy_data = CopyFileData(
        input_path=infile,
        output_path=outfile,
        relative_input_path=infile.relative_to(tmp_path),
    )
    assert infile.exists()
    assert not outfile.exists()

    async with PytestLocalOpsBackend() as unit:
        await unit.copy_file_to_output(copy_data)

    assert infile.exists()
    assert outfile.exists()
    assert outfile.read_text(encoding="utf-8") == "Some Text"


async def test_copy_dir_group(tmp_path):
    input_files = [
        "dir_1/file_1.txt",
        "dir_1/file_2.txt",
        "dir_2/file_3.txt",
        "dir_2/subdir/file_4.txt",
        # ignored files
        "dir_1/build/foo.txt",
        "dir_2/.git/data.bin",
        # CLM's own voiceover scratch must never reach output (issue #431).
        "dir_1/.clm/voiceover-cache/transcripts/abc.json",
    ]
    copy_data, output_dir = build_copy_data(tmp_path, input_files)

    async with PytestLocalOpsBackend() as unit:
        await unit.copy_dir_group_to_output(copy_data)

    assert output_dir.exists()
    assert output_dir.is_dir()
    assert (output_dir / "dir_1/file_1.txt").exists()
    assert (output_dir / "dir_1/file_2.txt").exists()
    assert (output_dir / "dir_2/file_3.txt").exists()
    assert (output_dir / "dir_2/subdir/file_4.txt").exists()
    assert not (output_dir / "dir_1/build/foo.txt").exists()
    assert not (output_dir / "dir_1/.git/data.bin").exists()
    assert not (output_dir / "dir_1/.clm").exists()


async def test_copy_dir_group_skips_unchanged_files(tmp_path, monkeypatch):
    """A rebuild must not rewrite dir-group files whose size and mtime match.

    Rewriting an unchanged vendored tree (e.g. Catch2) on every build is
    needless SSD wear; copy2 preserves the source mtime, so the size+mtime
    quick check holds on every subsequent build.
    """
    input_files = ["dir_1/file_1.txt", "dir_2/subdir/file_2.txt"]
    copy_data, output_dir = build_copy_data(tmp_path, input_files)

    async with PytestLocalOpsBackend() as unit:
        await unit.copy_dir_group_to_output(copy_data)
    assert (output_dir / "dir_1/file_1.txt").exists()

    real_copy2 = shutil.copy2
    copied: list[str] = []

    def counting_copy2(src, dst, *, follow_symlinks=True):
        copied.append(Path(src).name)
        return real_copy2(src, dst, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(shutil, "copy2", counting_copy2)

    # Second run over identical sources: no file is written again.
    async with PytestLocalOpsBackend() as unit:
        await unit.copy_dir_group_to_output(copy_data)
    assert copied == []

    # A modified source is copied again.
    changed = tmp_path / "input/dir_1/file_1.txt"
    changed.write_text("changed content")
    async with PytestLocalOpsBackend() as unit:
        await unit.copy_dir_group_to_output(copy_data)
    assert copied == ["file_1.txt"]
    assert (output_dir / "dir_1/file_1.txt").read_text() == "changed content"


def build_copy_data(tmp_dir, input_files):
    input_dir = tmp_dir / "input"
    input_dir.mkdir(parents=True)
    input_paths: list[Path] = [input_dir / file for file in input_files]
    for file in input_paths:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"File {file.name}")
    input_dirs = tuple(input_dir.glob("*/"))
    relative_paths = tuple(in_dir.relative_to(input_dir) for in_dir in input_dirs)
    output_dir = tmp_dir / "output"
    copy_data = CopyDirGroupData(
        name="name-of-copy",
        source_dirs=input_dirs,
        relative_paths=relative_paths,
        lang="en",
        output_dir=output_dir,
    )
    return copy_data, output_dir
