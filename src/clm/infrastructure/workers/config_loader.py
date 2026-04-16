"""Configuration loading utilities for worker management."""

import logging
from typing import Any

from clm.infrastructure.config import WorkersManagementConfig, get_config

logger = logging.getLogger(__name__)


def load_worker_config(cli_overrides: dict[str, Any] | None = None) -> WorkersManagementConfig:
    """Load worker configuration from all sources with CLI overrides.

    Configuration is loaded in priority order (highest to lowest):
    1. CLI options (passed in cli_overrides)
    2. Environment variables
    3. Project config file
    4. User config file
    5. System config file
    6. Defaults

    Args:
        cli_overrides: Dictionary of CLI option overrides. Expected keys:
            CLI-style keys (from CLI):
                - workers: Execution mode ('direct' or 'docker')
                - worker_count: Default worker count
                - notebook_workers: Notebook worker count
                - plantuml_workers: PlantUML worker count
                - drawio_workers: Draw.io worker count
                - max_workers: Hard cap on effective worker count per type
                  (see ``WorkersManagementConfig.max_workers_cap``). Clamps
                  the per-type counts via
                  ``clm.infrastructure.workers.pool_size_cap``.
                - no_auto_start: Disable auto-start
                - no_auto_stop: Disable auto-stop
                - fresh_workers: Don't reuse existing workers
            OR config-style keys (from tests/programmatic use):
                - default_execution_mode: Execution mode
                - default_worker_count: Default worker count
                - notebook_count: Notebook worker count (alias for notebook_workers)
                - plantuml_count: PlantUML worker count (alias for plantuml_workers)
                - drawio_count: Draw.io worker count (alias for drawio_workers)
                - max_workers_cap: Pool size cap (alias for max_workers)
                - auto_start: Enable auto-start
                - auto_stop: Enable auto-stop
                - reuse_workers: Reuse existing workers

    Returns:
        WorkersManagementConfig with CLI overrides applied
    """
    cli_overrides = cli_overrides or {}

    # Load base config from files + env (handled by ClmConfig)
    config = get_config().worker_management

    # Apply CLI overrides to global settings
    # Support both CLI-style ("workers") and config-style ("default_execution_mode")
    if cli_overrides.get("workers"):
        config.default_execution_mode = cli_overrides["workers"]
        logger.info(f"CLI override: default_execution_mode = {config.default_execution_mode}")
    elif cli_overrides.get("default_execution_mode"):
        config.default_execution_mode = cli_overrides["default_execution_mode"]
        logger.info(f"Config override: default_execution_mode = {config.default_execution_mode}")

    if cli_overrides.get("worker_count") is not None:
        config.default_worker_count = cli_overrides["worker_count"]
        logger.info(f"CLI override: default_worker_count = {config.default_worker_count}")
    elif cli_overrides.get("default_worker_count") is not None:
        config.default_worker_count = cli_overrides["default_worker_count"]
        logger.info(f"Config override: default_worker_count = {config.default_worker_count}")

    # Pool size cap: accepts either "max_workers" (CLI) or "max_workers_cap"
    # (config). A value of 0 or negative is treated as "no cap", matching
    # the handling in pool_size_cap._read_env_cap so operators can clear a
    # cap without unsetting an environment variable.
    cap_value = cli_overrides.get("max_workers")
    if cap_value is None:
        cap_value = cli_overrides.get("max_workers_cap")
    if cap_value is not None:
        if cap_value > 0:
            config.max_workers_cap = cap_value
            logger.info(f"CLI override: max_workers_cap = {config.max_workers_cap}")
        else:
            config.max_workers_cap = None
            logger.info("CLI override: max_workers_cap cleared")

    # Auto-start: support both "no_auto_start" (CLI) and "auto_start" (config)
    if cli_overrides.get("no_auto_start"):
        config.auto_start = False
        logger.info("CLI override: auto_start = False")
    elif "auto_start" in cli_overrides:
        config.auto_start = cli_overrides["auto_start"]
        logger.info(f"Config override: auto_start = {config.auto_start}")

    # Auto-stop: support both "no_auto_stop" (CLI) and "auto_stop" (config)
    if cli_overrides.get("no_auto_stop"):
        config.auto_stop = False
        logger.info("CLI override: auto_stop = False")
    elif "auto_stop" in cli_overrides:
        config.auto_stop = cli_overrides["auto_stop"]
        logger.info(f"Config override: auto_stop = {config.auto_stop}")

    # Reuse workers: support both "fresh_workers" (CLI) and "reuse_workers" (config)
    if cli_overrides.get("fresh_workers"):
        config.reuse_workers = False
        logger.info("CLI override: reuse_workers = False")
    elif "reuse_workers" in cli_overrides:
        config.reuse_workers = cli_overrides["reuse_workers"]
        logger.info(f"Config override: reuse_workers = {config.reuse_workers}")

    # Apply per-type overrides
    # Support both {type}_workers and {type}_count suffixes.
    # JupyterLite is included so `--jupyterlite-workers N` / env overrides
    # work the same way as the other worker types; `count=None` leaves it
    # disabled, matching the opt-in contract in ``get_all_worker_configs``.
    for worker_type in ["notebook", "plantuml", "drawio", "jupyterlite"]:
        workers_key = f"{worker_type}_workers"
        count_key = f"{worker_type}_count"

        if cli_overrides.get(workers_key) is not None:
            type_config = getattr(config, worker_type)
            type_config.count = cli_overrides[workers_key]
            logger.info(f"CLI override: {worker_type}.count = {type_config.count}")
        elif cli_overrides.get(count_key) is not None:
            type_config = getattr(config, worker_type)
            type_config.count = cli_overrides[count_key]
            logger.info(f"Config override: {worker_type}.count = {type_config.count}")

    # Handle notebook image override
    # Support both full image name and just the tag suffix
    if cli_overrides.get("notebook_image"):
        image_value = cli_overrides["notebook_image"]
        # If it's just a tag (like "lite" or "full"), construct full image name
        if "/" not in image_value and ":" not in image_value:
            image_value = f"docker.io/mhoelzl/clm-notebook-processor:{image_value}"
        elif ":" not in image_value:
            # Has namespace but no tag, add :latest
            image_value = f"{image_value}:latest"
        config.notebook.image = image_value
        logger.info(f"CLI override: notebook.image = {config.notebook.image}")

    return config
