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

### Customization

To customize the hook behavior, edit `.claude/sessionStart`. The hook is a standard bash script.

## References

- [Claude Code Documentation](https://docs.claude.com/en/docs/claude-code)
- [SessionStart Hook Documentation](https://docs.claude.com/en/docs/claude-code/hooks#sessionstart)
