# Claude Code Hooks

This directory contains hooks for Claude Code sessions.

## SessionStart Hook

The `sessionStart` hook runs automatically when a new Claude Code session starts.

### What it does

- **In remote environments** (Claude Code on the web):
  - Automatically installs all dependencies from `requirements.txt`
  - Installs local packages in editable mode:
    - clx-common
    - clx
    - clx-faststream-backend
    - clx-cli
  - Installs service packages in editable mode:
    - services/notebook-processor
    - services/drawio-converter
    - services/plantuml-converter

- **In local environments**:
  - Skips automatic installation
  - Shows manual installation instructions

### Environment Detection

The hook detects a remote environment if any of these conditions are true:
- `CLAUDE_SESSION_ID` environment variable is set
- `USER` environment variable is "claude"
- `USER` environment variable is "root"

### Manual Testing

You can test the hook manually:

```bash
# Test in local mode (skips installation)
./.claude/sessionStart

# Test in remote mode (installs dependencies)
CLAUDE_SESSION_ID=test ./.claude/sessionStart
```

### Important Notes

**Service Packages**: The service packages (notebook-processor, drawio-converter, plantuml-converter) are installed as Python packages for code access and testing, but they will not be functional in remote environments because:
- They require RabbitMQ message broker to operate
- They depend on external tools (Draw.io, PlantUML, Java, etc.) not available in the remote environment
- The notebook-processor requires heavy ML dependencies (PyTorch, CUDA, etc.)

For full service functionality, use Docker Compose locally: `docker-compose up`

### Customization

To customize the hook behavior, edit `.claude/sessionStart`. The hook is a standard bash script.

## References

- [Claude Code Documentation](https://docs.claude.com/en/docs/claude-code)
- [SessionStart Hook Documentation](https://docs.claude.com/en/docs/claude-code/hooks#sessionstart)
