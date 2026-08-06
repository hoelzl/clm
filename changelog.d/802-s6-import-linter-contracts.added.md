- **Layer contracts enforced (Phase 8 step S6 = A11, refs #802)**:
  `import-linter` now runs in CI's lint job and as a pre-commit hook
  (`uv run lint-imports`), enforcing the four-layer architecture — core below
  infrastructure below workers, and no constrained layer may import the CLI or
  extension packages (config in `pyproject.toml` `[tool.importlinter]`). The
  Phase 7 violation-inventory ratchet reached zero with S5 and is replaced by
  these contracts; the string-import dodge guard and the Backend/payload
  schema pins remain in `tests/test_architecture_contracts.py`.
