from pathlib import Path

from attrs import frozen


@frozen
class CopyFileData:
    input_path: Path
    relative_input_path: Path
    output_path: Path
