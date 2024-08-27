from pathlib import Path

from attrs import frozen


@frozen
class CopyDirGroupData:
    name: str
    source_dirs: tuple[Path, ...]
    relative_paths: tuple[Path, ...]
    output_dir: Path
    lang: str
