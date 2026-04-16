"""Transport types for JupyterLite site-build jobs.

A JupyterLite job is a site-level bundler that consumes the already-built
``notebook``-format output tree for a ``(target, language, kind)`` tuple
and produces a deployable static site under ``output_dir``. The payload
carries the inputs needed to (a) reconstruct the ``lite-dir/`` the
worker will feed to ``jupyter lite build`` and (b) compute a cache key
so unchanged builds can be skipped.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field

from clm.infrastructure.messaging.base_classes import Payload, Result


class JupyterLitePayload(Payload):
    """Inputs for a single ``jupyter lite build`` invocation.

    The base ``Payload`` fields are repurposed for this site-level job:

    - ``input_file`` — the notebook tree directory (path string). Stored
      as a string because ``Payload`` is JSON-serialized across a queue
      boundary and cannot hold ``Path`` objects portably.
    - ``input_file_name`` — a human-readable label used only in logs
      (``<target>/<language>/<kind>``).
    - ``output_file`` — the path to ``_output/index.html``; existence of
      this file gates the SQLite queue cache hit check.
    - ``data`` — the JSON-serialized manifest (notebook hashes, wheel
      hashes, kernel, ``jupyterlite-core`` version). Used by
      ``content_hash`` to key the cache.
    """

    course_root: str
    notebook_tree: str
    output_dir: str
    target_name: str
    language: str
    kind: str
    kernel: Literal["xeus-python", "pyodide"]
    wheels: list[str] = Field(default_factory=list)
    environment_yml: str = ""
    app_archive: Literal["offline", "cdn"] = "offline"
    emit_launcher: bool = True
    jupyterlite_core_version: str = ""

    def content_hash(self) -> str:
        """Hash the manifest + kernel + jupyterlite-core version.

        ``data`` is already a stable JSON manifest produced by
        ``assemble_lite_dir``-style inputs; we re-hash it here so the
        backend cache key uses the full set of inputs that affect the
        build output.
        """
        blob = json.dumps(
            {
                "data": self.data,
                "kernel": self.kernel,
                "app_archive": self.app_archive,
                "jupyterlite_core": self.jupyterlite_core_version,
                "emit_launcher": self.emit_launcher,
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def output_metadata(self) -> str:
        return f"jupyterlite:{self.target_name}:{self.language}:{self.kind}:{self.kernel}"


class JupyterLiteResult(Result):
    """Completion marker for a JupyterLite build.

    Unlike notebook/plantuml/drawio jobs, the ``output_file`` is only an
    anchor into a full directory tree; the ``result`` field carries a
    compact JSON summary so callers can display build stats without
    walking the site tree.
    """

    result_type: Literal["result"] = "result"
    summary: str  # JSON dict: {"files_count": N, "manifest_path": "..."}

    def result_bytes(self) -> bytes:
        return self.summary.encode("utf-8")

    def output_metadata(self) -> str:
        return "jupyterlite"
