"""Side-effect-free locators for the direct-mode diagram binaries.

One resolution, two consumers (issue #747): the converters use these at
worker startup (keeping their own raise-on-missing semantics), and the
host-side cache-key identity (:mod:`clm.infrastructure.workers.image_identity`)
uses them to fingerprint the binary a direct-mode build will execute — so a
PlantUML-JAR or Draw.io upgrade invalidates the diagram caches the same way
a Docker image switch does. Import must stay free of side effects: no
raises, no logging config, no filesystem writes.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

#: Default order: Docker container path → local repo jar → legacy path.
PLANTUML_DEFAULT_JAR_PATHS = [
    "/app/plantuml.jar",  # Docker container path
    str(
        Path(__file__).parents[3] / "docker" / "plantuml" / "plantuml-1.2024.6.jar"
    ),  # Local repo path
    str(Path(__file__).parents[2] / "plantuml-1.2024.6.jar"),  # Legacy path (src/)
]


def locate_plantuml_jar() -> str | None:
    """The JAR path a direct-mode PlantUML worker would use, or ``None``.

    Mirrors the converter's resolution order (``PLANTUML_JAR`` env var,
    then the default paths) without raising: the env value is returned
    even when the file is missing (the converter raises on it; the
    fingerprint side treats a missing file as unlocatable).
    """
    from_env = os.environ.get("PLANTUML_JAR")
    if from_env:
        return from_env
    return next((p for p in PLANTUML_DEFAULT_JAR_PATHS if Path(p).exists()), None)


def locate_drawio_executable() -> str | None:
    """The Draw.io executable a direct-mode worker would use, or ``None``.

    ``DRAWIO_EXECUTABLE`` env var (as-is, may be a bare name), else
    ``drawio`` resolved on PATH.
    """
    from_env = os.environ.get("DRAWIO_EXECUTABLE")
    candidate = from_env or "drawio"
    if os.path.sep in candidate or (os.path.altsep and os.path.altsep in candidate):
        return candidate
    return shutil.which(candidate)
