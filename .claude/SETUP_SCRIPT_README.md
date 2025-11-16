# CLX Test Environment Setup Script

This directory contains `setup-test-env.sh`, an automated script for setting up a complete development and testing environment for the CLX project.

## Purpose

The setup script was created to solve the problem of manual environment setup in Claude Code on the web and other remote/headless environments. Previously, setting up the environment required running many manual steps, which was error-prone and time-consuming.

## What It Does

The script automates:

1. **Package Installation**: Installs CLX with all dependencies (`[all]` extras) including test, TUI, and web dependencies
2. **Service Installation**: Installs all three worker service packages (notebook-processor, plantuml-converter, drawio-converter)
3. **PlantUML Setup**: Downloads PlantUML JAR from GitHub releases and creates wrapper script
4. **DrawIO Setup**: Downloads DrawIO Debian package and extracts the binary
5. **Xvfb Setup**: Starts Xvfb for headless rendering required by DrawIO
6. **Environment Variables**: Sets and persists PLANTUML_JAR, DISPLAY, and DRAWIO_EXECUTABLE
7. **Verification**: Verifies all components are working correctly

## Usage

### Basic Usage

```bash
# From CLX repository root
./.claude/setup-test-env.sh
```

### Options

```bash
# Skip verification at the end (faster, but won't check if everything works)
./.claude/setup-test-env.sh --skip-verify

# Show help
./.claude/setup-test-env.sh --help
```

### Environment Variables

- `CLX_SKIP_DOWNLOADS=1` - Skip downloading PlantUML and DrawIO (useful in restricted networks)

Example:
```bash
export CLX_SKIP_DOWNLOADS=1
./.claude/setup-test-env.sh
```

## Output

The script provides colored output:
- **Blue** - Section headers and info messages
- **Green** - Success messages (✓)
- **Yellow** - Warning messages (⚠)
- **Red** - Error messages (✗)

## Success Criteria

When successful, you should see:

```
===================================
Environment Setup Complete ✓
===================================
All checks passed! Your environment is ready for running CLX tests.

You can now run tests with:
  pytest                    # Unit tests only (fast)
  pytest -m integration     # Integration tests
  pytest -m e2e            # End-to-end tests
  pytest -m ""              # All tests
```

## Troubleshooting

### Downloads Fail

If downloads fail due to network restrictions:
1. Set `export CLX_SKIP_DOWNLOADS=1`
2. Follow manual installation instructions in CLAUDE.md

### Verification Fails

If some checks fail but core packages are installed:
- You can still run unit tests
- Integration/E2E tests may fail if external tools are missing

### Permission Issues

The script requires write access to:
- `/usr/local/bin/` (for tool symlinks)
- `/usr/local/share/` (for PlantUML JAR)
- `/tmp/` (for temporary files)
- `~/.bashrc` (for environment variables)

Run as root or with sudo if needed.

## After Setup

Environment variables are added to `~/.bashrc`. To apply them to your current shell:

```bash
source ~/.bashrc
```

Or simply open a new shell session.

## Integration with Claude Code

This script is designed to be:
1. **Run manually** when you need a test environment
2. **Referenced in CLAUDE.md** for AI assistants to know about it
3. **Independent of sessionStart hook** which only does basic package installation

The sessionStart hook (`.claude/sessionStart`) handles basic package installation during session startup, while this script handles the complete test environment setup including external tools.

## Maintenance

When updating tool versions:
1. Update version numbers in the script (PLANTUML_VERSION, DRAWIO_VERSION)
2. Test the script with new versions
3. Update CLAUDE.md if setup instructions change

## See Also

- `CLAUDE.md` - Full documentation including manual setup instructions
- `.claude/sessionStart` - Session startup hook for basic package installation
- `docs/developer-guide/testing.md` - Testing guide

---

**Last Updated**: 2025-11-16
**Related Issue**: Integration test dependencies setup automation
