"""Operation that enqueues a JupyterLite site build for one target tuple.

Runs once per ``(target, language, kind)`` after the notebook-format jobs
for that tuple complete. The operation walks the on-disk notebook output
tree to build a content-addressed manifest, packages it into a
``JupyterLitePayload``, and hands it to the backend for dispatch to a
``jupyterlite`` worker.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from attrs import frozen

from clm.core.course_spec import JupyterLiteConfig
from clm.infrastructure.messaging.correlation_ids import (
    new_correlation_id,
    note_correlation_id_dependency,
)
from clm.infrastructure.messaging.jupyterlite_classes import JupyterLitePayload
from clm.infrastructure.operation import Operation
from clm.workers.jupyterlite.lite_dir import collect_notebook_tree, sha256_of_file

logger = logging.getLogger(__name__)


def _get_jupyterlite_core_version() -> str:
    """Return the installed ``jupyterlite-core`` version, or ``""`` if missing.

    Looked up lazily so ``[jupyterlite]`` does not become a hard import-time
    dependency of the coordinator.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("jupyterlite-core")
        except PackageNotFoundError:
            return ""
    except Exception:
        return ""


@frozen
class BuildJupyterLiteSiteOperation(Operation):
    """Build a JupyterLite static site for one ``(target, language, kind)``."""

    course_root: Path
    notebook_tree: Path
    output_dir: Path
    target_name: str
    language: str
    kind: str
    config: JupyterLiteConfig

    async def execute(self, backend, *args, **kwargs) -> Any:
        try:
            logger.info(
                "Building JupyterLite site for target=%s language=%s kind=%s "
                "(kernel=%s, wheels=%d)",
                self.target_name,
                self.language,
                self.kind,
                self.config.kernel,
                len(self.config.wheels),
            )
            payload = await self.payload()
            await backend.execute_operation(self, payload)
        except Exception as e:
            label = f"{self.target_name}/{self.language}/{self.kind}"
            logger.error(f"Error building JupyterLite site for '{label}': {e}")
            logger.debug(f"Error traceback for '{label}'", exc_info=e)
            raise

    def _resolve_wheels(self) -> list[Path]:
        """Resolve wheel paths relative to the course root."""
        resolved: list[Path] = []
        for wheel_str in self.config.wheels:
            wheel_path = Path(wheel_str)
            if not wheel_path.is_absolute():
                wheel_path = self.course_root / wheel_path
            resolved.append(wheel_path)
        return resolved

    def _resolve_environment_yml(self) -> Path | None:
        """Resolve the environment.yml path, if configured."""
        if not self.config.environment:
            return None
        env_path = Path(self.config.environment)
        if not env_path.is_absolute():
            env_path = self.course_root / env_path
        return env_path

    def _build_manifest(self, wheels: list[Path], environment_yml: Path | None) -> dict:
        """Walk the on-disk inputs to assemble a cache-key manifest.

        This is a deterministic summary of everything that could change
        the build output (excluding ``jupyterlite-core`` itself, which is
        mixed in by ``JupyterLitePayload.content_hash``).
        """
        notebooks = collect_notebook_tree(self.notebook_tree)
        wheel_entries: list[tuple[str, str]] = []
        for wheel in wheels:
            if wheel.is_file():
                wheel_entries.append((wheel.name, sha256_of_file(wheel)))
            else:
                wheel_entries.append((wheel.name, ""))
        env_hash = ""
        if environment_yml is not None and environment_yml.is_file():
            env_hash = sha256_of_file(environment_yml)
        return {
            "notebooks": notebooks,
            "wheels": wheel_entries,
            "environment_sha256": env_hash,
        }

    async def payload(self) -> JupyterLitePayload:
        correlation_id = await new_correlation_id()

        wheels = self._resolve_wheels()
        environment_yml = self._resolve_environment_yml()
        manifest = self._build_manifest(wheels, environment_yml)

        site_index = self.output_dir / "_output" / "index.html"

        branding = self.config.branding
        payload = JupyterLitePayload(
            correlation_id=correlation_id,
            input_file=str(self.notebook_tree),
            input_file_name=f"{self.target_name}/{self.language}/{self.kind}",
            output_file=str(site_index),
            data=json.dumps(manifest, sort_keys=True),
            course_root=str(self.course_root),
            notebook_tree=str(self.notebook_tree),
            output_dir=str(self.output_dir),
            target_name=self.target_name,
            language=self.language,
            kind=self.kind,
            kernel=self.config.kernel,  # type: ignore[arg-type]
            wheels=[str(w) for w in wheels],
            environment_yml=str(environment_yml) if environment_yml else "",
            app_archive=self.config.app_archive,  # type: ignore[arg-type]
            launcher=self.config.launcher,
            branding_theme=branding.theme if branding else "",
            branding_logo=branding.logo if branding else "",
            branding_site_name=branding.site_name if branding else "",
            jupyterlite_core_version=_get_jupyterlite_core_version(),
        )
        await note_correlation_id_dependency(correlation_id, payload)
        return payload

    @property
    def service_name(self) -> str:
        return "jupyterlite-builder"
