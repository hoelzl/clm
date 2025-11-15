# Troubleshooting Guide

This guide covers common issues and their solutions when using CLX.

## Installation Issues

### CLX Command Not Found

**Symptoms**:
```bash
$ clx build course.yaml
bash: clx: command not found
```

**Solutions**:

1. **Verify installation**:
   ```bash
   pip list | grep clx
   ```

2. **Reinstall CLX**:
   ```bash
   pip install --force-reinstall clx
   ```

3. **Check PATH**:
   ```bash
   # Find where CLX is installed
   pip show clx

   # Ensure pip's bin directory is in PATH
   echo $PATH  # Linux/macOS
   echo %PATH% # Windows
   ```

4. **Use full path**:
   ```bash
   python -m clx.cli.main build course.yaml
   ```

### Import Errors

**Symptoms**:
```python
ImportError: No module named 'clx'
```

**Solutions**:

1. **Check Python environment**:
   ```bash
   # Are you in the correct virtual environment?
   which python
   pip list | grep clx
   ```

2. **Reinstall in correct environment**:
   ```bash
   deactivate  # Exit current venv
   source /path/to/correct/venv/bin/activate
   pip install clx
   ```

3. **Check for conflicting installations**:
   ```bash
   # Uninstall all versions
   pip uninstall clx clx-cli clx-common clx-faststream-backend

   # Install clean
   pip install clx
   ```

## Build Issues

### Course YAML Not Found

**Symptoms**:
```
Error: course.yaml not found
```

**Solutions**:

1. **Check current directory**:
   ```bash
   ls course.yaml
   ```

2. **Specify full path**:
   ```bash
   clx build /full/path/to/course.yaml
   ```

3. **Check filename**:
   - Must be exactly `course.yaml` (not `Course.yaml` or `course.yml`)

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
   - Or explicitly define in `course.yaml`

3. **Explicit section definition** in `course.yaml`:
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
   clx build course.yaml --log-level DEBUG
   ```

4. **Check cache**:
   ```bash
   # Clear cache and rebuild
   rm clx_cache.db clx_jobs.db
   clx build course.yaml
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
   ls -lh clx_cache.db

   # Should grow over time
   ```

2. **Rebuild cache** (if corrupted):
   ```bash
   rm clx_cache.db clx_jobs.db
   clx build course.yaml
   ```

3. **Use watch mode** for incremental builds:
   ```bash
   clx build course.yaml --watch
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
   lsof clx_jobs.db

   # Kill hung workers if needed
   pkill -f clx
   ```

2. **Remove database**:
   ```bash
   rm clx_jobs.db
   clx build course.yaml
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
   rm clx_jobs.db clx_cache.db
   clx build course.yaml
   ```

2. **Prevent corruption**:
   - Don't kill processes forcefully (use Ctrl+C)
   - Let workers shut down gracefully

## Docker Issues

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

## Getting More Help

### Enable Debug Logging

```bash
clx build course.yaml --log-level DEBUG > clx.log 2>&1
```

This creates a detailed log file you can review or share.

### Check Worker Status

```bash
# View running workers (if using Docker)
docker ps

# View worker logs
docker logs clx-notebook-processor
docker logs clx-plantuml-converter
docker logs clx-drawio-converter
```

### Examine Database

```bash
# Install sqlite3 if needed
sudo apt-get install sqlite3

# Inspect jobs
sqlite3 clx_jobs.db "SELECT * FROM jobs WHERE status='failed';"

# Check workers
sqlite3 clx_jobs.db "SELECT * FROM workers;"

# View cache stats
sqlite3 clx_cache.db "SELECT COUNT(*) FROM results_cache;"
```

### Report Issues

If you can't resolve the issue:

1. **Gather information**:
   - CLX version: `pip show clx`
   - Python version: `python --version`
   - Operating system
   - Error messages
   - Debug log (with `--log-level DEBUG`)

2. **Create minimal reproducible example**:
   - Simplest course.yaml that triggers the issue
   - Minimal content files

3. **Report on GitHub**:
   - https://github.com/hoelzl/clx/issues
   - Include gathered information
   - Attach logs and example files

## Common Error Messages

### "No module named 'clx'"
→ See [Import Errors](#import-errors)

### "course.yaml not found"
→ See [Course YAML Not Found](#course-yaml-not-found)

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
- [GitHub Issues](https://github.com/hoelzl/clx/issues) - Report bugs
