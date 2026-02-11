# Quick Build Reference

## The Problem You Encountered

When building from a service directory:
```bash
cd services/drawio-converter
docker build -t clm-drawio-converter .
```

This fails because Docker cannot access files outside the build context (like `../../clm-common`).

## The Solution

**Always build from the project root** using one of these methods:

### Method 1: Use the Build Script (Easiest)

**Linux/macOS:**
```bash
# From project root
./build-services.sh drawio-converter
```

**Windows PowerShell:**
```powershell
# From project root
.\build-services.ps1 drawio-converter
```

### Method 2: Direct Docker Build

**Linux/macOS:**
```bash
# From project root
export DOCKER_BUILDKIT=1

docker build \
  -f services/drawio-converter/Dockerfile \
  -t clm-drawio-converter \
  --build-arg SERVICE_PATH=services/drawio-converter \
  --build-arg COMMON_PATH=. \
  .
```

**Windows PowerShell:**
```powershell
# From project root
$env:DOCKER_BUILDKIT = "1"

docker build `
  -f services/drawio-converter/Dockerfile `
  -t clm-drawio-converter `
  --build-arg SERVICE_PATH=services/drawio-converter `
  --build-arg COMMON_PATH=. `
  .
```

Notice the final `.` - this sets the build context to the current directory (project root).

## Build All Services

**Linux/macOS:**
```bash
./build-services.sh
```

**Windows PowerShell:**
```powershell
.\build-services.ps1
```

## Why This Works

- The `.` at the end sets the **build context** to the project root
- This gives Docker access to both `services/` and `clm-common/`
- The `-f` flag specifies which Dockerfile to use
- The build args tell the Dockerfile where to find files relative to the root

See `BUILD.md` for complete documentation including cache management.
