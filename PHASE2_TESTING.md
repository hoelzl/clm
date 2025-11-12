# Phase 2 Testing Guide

This guide explains how to test the Phase 2 worker infrastructure.

## Prerequisites

### 1. Install Dependencies

First, ensure all dependencies are installed:

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install CLX packages in editable mode
pip install -e ./clx-common -e ./clx -e ./clx-faststream-backend -e ./clx-cli
```

### 2. Verify Installation

Run the verification script to ensure all dependencies are installed:

```bash
python verify_installation.py
```

You should see:
```
✓ All dependencies are installed correctly!
```

**Important for Windows users:** If you see `ModuleNotFoundError: No module named 'docker'`, you may need to:
1. Activate your virtual environment
2. Reinstall requirements: `pip install -r requirements.txt`
3. Verify: `pip list | grep docker` (or `pip list | findstr docker` on Windows)

### 3. Docker Setup

The worker pool manager requires Docker to be running:

- **Linux/Mac**: Ensure Docker daemon is running
- **Windows**: Ensure Docker Desktop is running

Check Docker is available:
```bash
docker ps
```

## Testing the Worker Infrastructure

### Option 1: Unit Tests (No Docker Required)

Run the comprehensive unit tests for worker infrastructure:

```bash
# Test worker base class (13 tests)
python -m pytest clx-common/tests/workers/test_worker_base.py -v

# Test pool manager (15 tests)
python -m pytest clx-common/tests/workers/test_pool_manager.py -v

# Run all worker tests (28 tests)
python -m pytest clx-common/tests/workers/ -v
```

These tests use mocks and don't require Docker.

### Option 2: Manual Testing with Pool Manager CLI

**Note:** This requires Docker images to be built first (see Building Docker Images section).

Run the worker pool manager:

#### Linux/Mac:

```bash
# Create workspace directory
mkdir -p test-workspace

# Set environment variables
export CLX_DB_PATH=clx_jobs.db
export CLX_WORKSPACE_PATH=$(pwd)/test-workspace

# Run pool manager (requires Docker images to exist)
python -m clx_common.workers.pool_manager
```

#### Windows (PowerShell):

```powershell
# Create workspace directory
New-Item -ItemType Directory -Force -Path test-workspace

# Set environment variables
$env:CLX_DB_PATH = "clx_jobs.db"
$env:CLX_WORKSPACE_PATH = "$(Get-Location)\test-workspace"

# Run pool manager (requires Docker images to exist)
python -m clx_common.workers.pool_manager
```

The pool manager will:
1. Initialize the SQLite database
2. Start worker containers (notebook, drawio, plantuml)
3. Monitor worker health
4. Press Ctrl+C to stop

### Option 3: Integration Testing (Full End-to-End)

**Coming in Phase 2 Step 2.4** - This will test the complete workflow:
1. Build Docker images
2. Start worker pools
3. Submit test jobs to the queue
4. Verify workers process jobs correctly
5. Check results and statistics

## Building Docker Images

Before running the pool manager with real workers, you need to build the Docker images.

**IMPORTANT:** Use the provided build scripts which automatically:
- Extract the version from `clx-common/pyproject.toml`
- Tag images with the correct names expected by the pool manager
- Create both versioned and `:latest` tags

### Linux/Mac:

```bash
# Build all service images
./build-services.sh

# Or build individually
./build-services.sh notebook-processor
./build-services.sh drawio-converter
./build-services.sh plantuml-converter
```

### Windows (PowerShell):

```powershell
# Build all service images
.\build-services.ps1

# Or build individually
.\build-services.ps1 notebook-processor
.\build-services.ps1 drawio-converter
.\build-services.ps1 plantuml-converter
```

The build scripts will tag images as:
- `notebook-processor:0.2.2` and `notebook-processor:latest`
- `drawio-converter:0.2.2` and `drawio-converter:latest`
- `plantuml-converter:0.2.2` and `plantuml-converter:latest`

(Plus `clx-*` prefixed versions for backward compatibility)

## Troubleshooting

### ModuleNotFoundError: No module named 'docker'

**Cause:** The `docker` Python package is not installed in your environment.

**Solution:**
```bash
# Ensure you're in the correct virtual environment
pip install docker>=7.0.0

# Or reinstall all requirements
pip install -r requirements.txt
```

### Docker daemon not running

**Cause:** The pool manager tries to connect to Docker but the daemon isn't running.

**Solution:**
- **Linux**: `sudo systemctl start docker`
- **Mac/Windows**: Start Docker Desktop application

### Permission denied when accessing Docker

**Cause:** User doesn't have permission to access Docker socket.

**Solution (Linux):**
```bash
sudo usermod -aG docker $USER
# Log out and back in for changes to take effect
```

### Container already exists error

**Cause:** Previous worker containers weren't cleaned up.

**Solution:**
```bash
# Remove existing worker containers
docker rm -f $(docker ps -a -q --filter "name=clx-*-worker-*")
```

## Checking Worker Status

While workers are running, you can check their status:

```bash
# View worker containers
docker ps --filter "name=clx-"

# Check SQLite database
sqlite3 clx_jobs.db "SELECT * FROM workers;"

# Check job queue
sqlite3 clx_jobs.db "SELECT COUNT(*), status FROM jobs GROUP BY status;"
```

## Next Steps

After verifying Phase 2 infrastructure works:

1. **Phase 2 Step 2.4**: Complete end-to-end integration testing
2. **Phase 3**: Migrate services to use SQLite exclusively
3. **Phase 4**: Remove RabbitMQ infrastructure

## Getting Help

If you encounter issues not covered here:

1. Check that all dependencies are installed: `python verify_installation.py`
2. Verify Docker is running: `docker ps`
3. Check the logs: `docker logs <container-name>`
4. Review test output: `pytest -v --tb=short`
