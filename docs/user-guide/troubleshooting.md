# Troubleshooting Guide

This guide covers common issues and their solutions when using CLM.

## Installation Issues

### CLM Command Not Found

**Symptoms**:
```bash
$ clm build course.xml
bash: clm: command not found
```

**Solutions**:

1. **Verify installation**:
   ```bash
   pip list | grep clm
   ```

2. **Reinstall CLM**:
   ```bash
   pip install --force-reinstall coding-academy-lecture-manager
   ```

3. **Check PATH**:
   ```bash
   # Find where CLM is installed
   pip show clm

   # Ensure pip's bin directory is in PATH
   echo $PATH  # Linux/macOS
   echo %PATH% # Windows
   ```

4. **Use full path**:
   ```bash
   python -m clm.cli.main build course.xml
   ```

### Import Errors

**Symptoms**:
```python
ImportError: No module named 'clm'
```

**Solutions**:

1. **Check Python environment**:
   ```bash
   # Are you in the correct virtual environment?
   which python
   pip list | grep clm
   ```

2. **Reinstall in correct environment**:
   ```bash
   deactivate  # Exit current venv
   source /path/to/correct/venv/bin/activate
   pip install coding-academy-lecture-manager
   ```

3. **Check for conflicting installations**:
   ```bash
   # Uninstall all versions
   pip uninstall coding-academy-lecture-manager

   # Install clean
   pip install coding-academy-lecture-manager
   ```

## Build Issues

### Course Spec Not Found

**Symptoms**:
```
Error: course.xml not found
```

**Solutions**:

1. **Check current directory**:
   ```bash
   ls course.xml
   ```

2. **Specify full path**:
   ```bash
   clm build /full/path/to/course.xml
   ```

3. **Check filename**:
   - The course specification file must be an XML file (e.g., `course.xml`)

### No Sections Found

**Symptoms**:
```
Warning: No sections found in course
```

**Solutions**:

1. **Check directory structure**:
   ```bash
   tree -L 2
   # Should show section_001/, section_002/, etc.
   ```

2. **Verify naming convention**:
   - Directories must be named `section_NNN` (with zero-padded numbers)
   - Or explicitly define in `course.xml`

3. **Explicit section definition** in `course.xml`:
   ```yaml
   sections:
     - name: "Section 1"
       dir: "my_section_dir"
   ```

### Files Not Processing

**Symptoms**:
```
Course built successfully!
(but no output files generated)
```

**Solutions**:

1. **Check file extensions**:
   - Python notebooks: `.py`
   - PlantUML: `.puml` or `.plantuml`
   - Draw.io: `.drawio`

2. **Check file format**:
   ```python
   # Python files must use cell markers:
   # %% [markdown]
   """
   # Heading
   """

   # %%
   code_here()
   ```

3. **Enable debug logging**:
   ```bash
   clm build course.xml --log-level DEBUG
   ```

4. **Check cache**:
   ```bash
   # Clear cache and rebuild
   rm clm_cache.db clm_jobs.db
   clm build course.xml
   ```

## Notebook Execution Issues

### Kernel Not Found

**Symptoms**:
```
Error: Kernel 'python3' not found
```

**Solutions**:

1. **Install Jupyter**:
   ```bash
   pip install jupyter notebook
   ```

2. **Install kernels**:
   ```bash
   # Python (usually already installed)
   python -m ipykernel install --user

   # C++
   conda install xeus-cling -c conda-forge

   # C#
   dotnet tool install -g Microsoft.dotnet-interactive
   dotnet interactive jupyter install

   # Java
   # Download and install IJava from GitHub

   # TypeScript
   npm install -g tslab
   tslab install
   ```

3. **List available kernels**:
   ```bash
   jupyter kernelspec list
   ```

### Notebook Execution Timeout

**Symptoms**:
```
Error: Cell execution timed out after 600 seconds
```

**Solutions**:

1. **Optimize code**:
   - Reduce computation time
   - Use smaller datasets for examples

2. **Increase timeout** (if appropriate):
   - This is configured per worker
   - For development, consider reducing example complexity

3. **Check for infinite loops**:
   - Review notebook code
   - Test cells individually in Jupyter

### Syntax Errors in Notebooks

**Symptoms**:
```
Error: SyntaxError: invalid syntax (line 42)
```

**Solutions**:

1. **Check cell markers**:
   ```python
   # Correct
   # %% [markdown]
   """Markdown content"""

   # %%
   code()

   # Incorrect (missing space)
   #%% [markdown]  # No space after #
   ```

2. **Validate Python syntax**:
   ```bash
   python -m py_compile topic_001.py
   ```

3. **Check for mixed indentation**:
   - Use spaces, not tabs
   - Consistent indentation (4 spaces recommended)

## Diagram Conversion Issues

### PlantUML: Java Not Found

**Symptoms**:
```
Error: Java executable not found
```

**Solutions**:

1. **Install Java**:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install default-jre

   # macOS
   brew install openjdk

   # Windows
   # Download from https://www.java.com/
   ```

2. **Verify installation**:
   ```bash
   java -version
   ```

### PlantUML: JAR Not Found

**Symptoms**:
```
Error: PlantUML JAR not found
```

**Solutions**:

1. **Download PlantUML**:
   ```bash
   wget https://github.com/plantuml/plantuml/releases/download/v1.2024.6/plantuml-1.2024.6.jar
   ```

2. **Set environment variable**:
   ```bash
   export PLANTUML_JAR="/path/to/plantuml-1.2024.6.jar"

   # Make permanent
   echo 'export PLANTUML_JAR="/path/to/plantuml.jar"' >> ~/.bashrc
   ```

3. **Verify**:
   ```bash
   echo $PLANTUML_JAR
   java -jar $PLANTUML_JAR -version
   ```

### Draw.io: Executable Not Found

**Symptoms**:
```
Error: DrawIO executable not found
```

**Solutions**:

1. **Install Draw.io**:
   ```bash
   # Ubuntu/Debian
   wget https://github.com/jgraph/drawio-desktop/releases/download/v24.7.5/drawio-amd64-24.7.5.deb
   sudo dpkg -i drawio-amd64-24.7.5.deb

   # macOS
   brew install --cask drawio

   # Windows
   # Download from https://github.com/jgraph/drawio-desktop/releases
   ```

2. **Set environment variable**:
   ```bash
   # Linux
   export DRAWIO_EXECUTABLE="/usr/bin/drawio"

   # macOS
   export DRAWIO_EXECUTABLE="/Applications/draw.io.app/Contents/MacOS/draw.io"

   # Windows
   set DRAWIO_EXECUTABLE="C:\Program Files\draw.io\draw.io.exe"
   ```

3. **Verify**:
   ```bash
   $DRAWIO_EXECUTABLE --version
   ```

### Draw.io: Display Error (Linux)

**Symptoms**:
```
Error: cannot open display
```

**Solutions**:

1. **Install Xvfb**:
   ```bash
   sudo apt-get install xvfb
   ```

2. **Start Xvfb**:
   ```bash
   Xvfb :99 -screen 0 1024x768x24 &
   export DISPLAY=:99
   ```

3. **Verify**:
   ```bash
   echo $DISPLAY
   # Should show :99
   ```

4. **Make permanent** (for servers):
   ```bash
   # Add to startup script
   echo 'Xvfb :99 -screen 0 1024x768x24 &' >> ~/.bashrc
   echo 'export DISPLAY=:99' >> ~/.bashrc
   ```

## Performance Issues

### Slow Builds

**Symptoms**: Building takes a long time, even for unchanged files

**Solutions**:

1. **Check cache**:
   ```bash
   # Verify cache is working
   ls -lh clm_cache.db

   # Should grow over time
   ```

2. **Rebuild cache** (if corrupted):
   ```bash
   rm clm_cache.db clm_jobs.db
   clm build course.xml
   ```

3. **Use watch mode** for incremental builds:
   ```bash
   clm build course.xml --watch
   ```

4. **Optimize content**:
   - Reduce notebook cell execution time
   - Simplify complex PlantUML diagrams
   - Use smaller image sizes in Draw.io

### High Memory Usage

**Symptoms**: System runs out of memory during build

**Solutions**:

1. **Reduce concurrent workers**:
   - Workers process files in parallel
   - For memory-constrained systems, reduce parallelism

2. **Process sections sequentially**:
   - Build one section at a time
   - Restart workers between sections

3. **Optimize notebooks**:
   - Avoid loading large datasets
   - Clear variables after use:
     ```python
     # %%
     import gc
     large_data = None
     gc.collect()
     ```

### Too Many Concurrent Operations (Windows)

**Symptoms**:
```
Assertion failed: Connection reset by peer [10054]
ERROR:asyncio:Error on reading from the event loop self pipe
ConnectionResetError: [WinError 995] The I/O operation has been aborted
```

This occurs on Windows when processing large courses with hundreds of notebooks, causing:
- ZMQ connection failures
- AsyncIO event loop crashes
- Worker process failures

**Solutions**:

1. **Reduce concurrency limit** (recommended):
   ```bash
   # Windows PowerShell
   $env:CLM_MAX_CONCURRENCY=25
   clm build course.xml

   # Windows CMD
   set CLM_MAX_CONCURRENCY=25
   clm build course.xml

   # Linux/macOS
   export CLM_MAX_CONCURRENCY=25
   clm build course.xml
   ```

2. **Recommended limits by system**:
   - **Default**: 50 (good for most systems)
   - **Windows low-spec/VMs**: 25
   - **Windows high-spec**: 50-75
   - **Linux/macOS**: 50-100 (or higher)

3. **Monitor resource usage**:
   - Watch Task Manager / Activity Monitor during build
   - Reduce limit if you see:
     - High memory usage (>90%)
     - Many failed processes
     - System becoming unresponsive

4. **Long-term solution**:
   - The concurrency limit prevents resource exhaustion
   - Default of 50 is conservative for compatibility
   - Can be increased on high-performance systems

## Database Issues

### Database Locked

**Symptoms**:
```
Error: database is locked
```

**Solutions**:

1. **Check for running processes**:
   ```bash
   # Linux/macOS
   lsof clm_jobs.db

   # Kill hung workers if needed
   pkill -f clm
   ```

2. **Remove database**:
   ```bash
   rm clm_jobs.db
   clm build course.xml
   ```

3. **Increase timeout**:
   - SQLite has a 30-second busy timeout
   - Should be sufficient for most cases

### Corrupted Database

**Symptoms**:
```
Error: database disk image is malformed
```

**Solutions**:

1. **Delete and rebuild**:
   ```bash
   rm clm_jobs.db clm_cache.db
   clm build course.xml
   ```

2. **Prevent corruption**:
   - Don't kill processes forcefully (use Ctrl+C)
   - Let workers shut down gracefully
   - Don't put a database on a network share used by more than one machine at
     a time — see below

### Database on a Network Share Is Still in WAL Mode

**Symptoms**:
```
Error: \\server\share\clm_jobs.db is on a network share and is still in WAL
mode. WAL is unsafe over a network filesystem: ...
```

**Cause**: WAL journaling keeps its index in a memory-mapped `-shm` file, which
is not coherent between machines. CLM opens network-hosted databases with
DELETE journaling instead (since 1.22.1), but it cannot switch a database that
another connection currently holds open — and that other connection is usually
the problem itself: a second machine using the same file.

**Solutions**:

1. **Close the other users**: stop every other `clm` process using that
   database — including on other machines — and retry. `clm status` will tell
   you what a given machine thinks is running.

2. **Use a local database**: point `CLM_JOBS_DB_PATH` at local storage. A RAM
   disk on a local drive letter counts as local and keeps the faster WAL mode.

3. **If nothing else holds it**, the database is stale from an earlier crash.
   The jobs database is ephemeral — only needs to survive one `clm` run — so
   deleting it is safe:
   ```bash
   rm \\server\share\clm_jobs.db
   ```

See [Databases on network shares](configuration.md#databases-on-network-shares)
for the full explanation.

## Docker Issues

### Worker containers run as a non-root user

The three worker images run as uid 1000 rather than root, and the notebook
worker gets the course sources mounted **read-only** — it executes
course-authored code and writes only to the output tree. PlantUML and Draw.io
keep the source mount writable, because rendering diagrams into the source
tree is what they do.

**Symptom**: a notebook cell that writes a file next to its slides fails in
Docker mode.

This should not happen: the kernel runs in a writable temporary directory in
Docker mode exactly as it always has in Direct mode, and files a cell writes
there are discarded with it. If you do see a read-only filesystem error from a
cell, the cell is writing to an *absolute* path inside `/source` — make it
relative, or write under the output tree.

**Symptom**: `clm build --workers docker` fails with "Docker mode refuses to
mount a whole volume".

An `<output-target><path>`, or the course data directory, resolves to a drive
or filesystem root (`C:\`, `/`), which would bind-mount that entire disk into
the container. Point it at a directory below the root.

**Running the images by hand on Linux**: a bind mount keeps host ownership, so
a container writing into one must run as you:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/output:/workspace"     docker.io/mhoelzl/clm-notebook-processor:lite
```

`clm` does this for you on native Linux. On Docker Desktop (Windows/macOS) the
mount is virtualized and the image's own user is kept. See `docker/README.md`
for the image-side details.

### Docker Services Not Starting

**Symptoms**:
```
Error: Cannot connect to Docker daemon
```

**Solutions**:

1. **Start Docker**:
   ```bash
   sudo systemctl start docker  # Linux
   # Or start Docker Desktop (macOS/Windows)
   ```

2. **Check permissions**:
   ```bash
   # Add user to docker group
   sudo usermod -aG docker $USER

   # Log out and back in
   ```

3. **Verify Docker**:
   ```bash
   docker ps
   ```

### Build Context Too Large

**Symptoms**:
```
Error: build context is too large
```

**Solutions**:

1. **Use .dockerignore**:
   ```bash
   # Create .dockerignore
   cat > .dockerignore <<EOF
   output/
   *.ipynb
   *.pyc
   __pycache__/
   .git/
   EOF
   ```

2. **Clean up**:
   ```bash
   # Remove generated files
   rm -rf output/
   ```

### Worker API Rejected the Token (401)

**Symptoms**: a Docker-mode build makes no progress, jobs stay `pending`, and
a worker container's log shows:

```
Worker API rejected the token (401) at http://host.docker.internal:8765/api/worker/register
```

**Cause**: every Worker API route requires a per-build bearer token, which the
host injects into each container as `CLM_API_TOKEN`. A worker image built
before that requirement sends no token, so the host rejects it. Host and
worker images have to be upgraded together.

**Solutions**:

1. **Rebuild the images**:
   ```bash
   clm docker build
   ```

2. **Confirm the image is current**:
   ```bash
   docker images | grep clm-
   ```
   An image older than your installed `clm` is the usual cause.

3. **If you set `CLM_API_TOKEN` yourself** (coordinator mode), make sure every
   participating machine has the *same* value — a mismatch produces exactly
   this error.

### Worker API Refuses to Start

**Symptoms**:
```
ValueError: Worker API refuses to bind 0.0.0.0 without a configured token.
```

**Cause**: `CLM_WORKER_API_HOST` is set to an address that reaches beyond this
machine. That is coordinator mode, and it requires a shared secret the other
machines can present — a generated per-build token exists only inside one
process.

**Solutions**:

1. **If you did not mean to expose the API**: unset `CLM_WORKER_API_HOST`.
   Normal Docker mode needs no bind configuration at all.
2. **If you did**: set `CLM_API_TOKEN` to a shared secret on the coordinator
   and on every participating machine. See
   [Worker API (Docker mode)](configuration.md#worker-api-docker-mode).

### Worker API Port Is Already in Use

**Symptoms**: either a warning, and the build carries on:
```
Worker API port 8765 is already in use — another CLM build, or something else,
is listening on it. Using 127.0.0.1:53127 for this build instead
```
or, when the port was pinned, the build stops with
`OSError: [WinError 10048]` / `OSError: [Errno 98] Address already in use`.

**Cause**: something else holds the port. The default `8765` is preferred, not
required — each container is told the real port via `CLM_API_URL` — so CLM
moves to a free one and only warns. A port you pinned with
`CLM_WORKER_API_PORT` is treated as a requirement, so a collision is an error
rather than something to route around.

**Solutions**:

1. **If the warning is all you see**: nothing to do. Note the port from the log
   if you want to `curl` the API's `/health`.
2. **If you pinned the port**: find the holder — `netstat -ano | findstr :8765`
   on Windows, `ss -lptn 'sport = :8765'` on Linux — and stop it, or pick
   another port. A leftover `clm` process from an aborted build is a common
   holder; it exits with its build, so a stale one means the build did not shut
   down cleanly.
3. **If several builds share the machine**: set `CLM_WORKER_API_PORT=0` to let
   the OS assign a free port to each one.

## Cassette Issues

### `clm cassette scan` reports findings — do I have to re-record everything?

No. The scan answers *"would the recorder change this file today?"*, not *"does
this file contain a secret"*, so a finding is a byte-hygiene item unless it is
one of the two classes that also affect replay.

```bash
clm cassette scan course.xml          # text report, exit 1 while findings remain
clm cassette scan course.xml --json   # per-file, per-interaction detail
```

Triage by the `location` field:

| Location | Affects replay? | What to do |
|---|---|---|
| `request query`, `request body` | **Yes** — both are in the replay match key, so the recorded entry no longer matches the filtered incoming request | Re-record the deck (`clm build --http-replay refresh`) |
| `response header`, `response body`, `response body (repeated name)` | No — responses are not in the match key | Re-record opportunistically, when the deck is next touched |

Read the recorded value before assuming the worst: a `set-cookie` finding is
very often `__cf_bm` (a Cloudflare bot-management token with a ~30 minute
lifetime) or an analytics cookie, not a credential.

The scan also exits non-zero when a cassette could not be **read**, because an
unreadable file is not evidence of cleanliness. Check the `error` field in
`--json` output to tell that apart from a real finding.

### A replayed cell reads a redacted field and raises

Expected, and the shape is worth knowing: when a secret-named key holds an
object or array, the whole value is replaced by the placeholder **string**, so
`response["secret"]["id"]` raises a `TypeError` rather than returning a redacted
field. Redacting only the strings *inside* would miss a secret stored under a
key that is not on the filter list, so this is deliberate. If a deck genuinely
needs the structure, rename the field to something outside the filter list — see
`clm info migration` for the full list.

## Getting More Help

### Enable Debug Logging

```bash
clm build course.xml --log-level DEBUG > clm.log 2>&1
```

This creates a detailed log file you can review or share.

### Check Worker Status

```bash
# View running workers (if using Docker)
# Run this first to find the actual container names — runtime containers are
# named clm-{worker_type}-worker-{index}, and the indices depend on the
# configured worker count (e.g. clm-notebook-worker-0, clm-notebook-worker-1).
docker ps

# View worker logs (substitute the names shown by `docker ps` above)
docker logs clm-notebook-worker-0
docker logs clm-plantuml-worker-0
docker logs clm-drawio-worker-0
```

### Examine Database

```bash
# Install sqlite3 if needed
sudo apt-get install sqlite3

# Inspect jobs
sqlite3 clm_jobs.db "SELECT * FROM jobs WHERE status='failed';"

# Check workers
sqlite3 clm_jobs.db "SELECT * FROM workers;"

# View cache stats
sqlite3 clm_cache.db "SELECT COUNT(*) FROM results_cache;"
```

### Report Issues

If you can't resolve the issue:

1. **Gather information**:
   - CLM version: `pip show coding-academy-lecture-manager`
   - Python version: `python --version`
   - Operating system
   - Error messages
   - Debug log (with `--log-level DEBUG`)

2. **Create minimal reproducible example**:
   - Simplest course.xml that triggers the issue
   - Minimal content files

3. **Report on GitHub**:
   - https://github.com/hoelzl/clm/issues
   - Include gathered information
   - Attach logs and example files

## Common Error Messages

### "No module named 'clm'"
→ See [Import Errors](#import-errors)

### "course.xml not found"
→ See [Course Spec Not Found](#course-spec-not-found)

### "Kernel 'X' not found"
→ See [Kernel Not Found](#kernel-not-found)

### "PlantUML JAR not found"
→ See [PlantUML: JAR Not Found](#plantuml-jar-not-found)

### "DrawIO executable not found"
→ See [Draw.io: Executable Not Found](#drawio-executable-not-found)

### "database is locked"
→ See [Database Locked](#database-locked)

### "Cell execution timed out"
→ See [Notebook Execution Timeout](#notebook-execution-timeout)

### "Connection reset by peer" / "WinError 995"
→ See [Too Many Concurrent Operations (Windows)](#too-many-concurrent-operations-windows)

### "Worker API rejected the token (401)"
→ See [Worker API Rejected the Token (401)](#worker-api-rejected-the-token-401)

### "Worker API refuses to bind ... without a configured token"
→ See [Worker API Refuses to Start](#worker-api-refuses-to-start)

## Platform-Specific Issues

### Windows

**Path Issues**:
- Use forward slashes in YAML: `output_dir: "./output"`
- Or escape backslashes: `"C:\\Users\\..."`

**PowerShell**:
- Use `$env:VAR = "value"` to set environment variables
- Not `export VAR=value` (that's Unix)

### macOS

**Xcode Command Line Tools**:
```bash
xcode-select --install
```

Required for some Python packages.

### Linux

**System Packages**:
Some features may require system packages:

```bash
sudo apt-get install \
  build-essential \
  python3-dev \
  default-jre \
  xvfb
```

## See Also

- [Installation Guide](installation.md) - Setup instructions
- [Configuration Guide](configuration.md) - Configuration options
- [Quick Start Guide](quick-start.md) - Getting started tutorial
- [GitHub Issues](https://github.com/hoelzl/clm/issues) - Report bugs
