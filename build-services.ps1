#!/usr/bin/env pwsh

# Build script for CLX services (PowerShell version for Windows)
# Must be run from the root of the clx project

param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Services
)

# Enable BuildKit
$env:DOCKER_BUILDKIT = "1"

# Available services
$AvailableServices = @("drawio-converter", "notebook-processor", "plantuml-converter")

# Function to write colored output
function Write-ColorOutput {
    param(
        [string]$Message,
        [string]$Color = "White"
    )
    Write-Host $Message -ForegroundColor $Color
}

# Function to get version from pyproject.toml
function Get-Version {
    $pyprojectPath = "clx-common/pyproject.toml"
    if (-not (Test-Path $pyprojectPath)) {
        return "0.2.2"  # fallback version
    }

    $content = Get-Content $pyprojectPath
    $versionLine = $content | Select-String -Pattern '^version = "(.+)"' | Select-Object -First 1
    if ($versionLine) {
        return $versionLine.Matches.Groups[1].Value
    }
    return "0.2.2"  # fallback version
}

# Function to build a service
function Build-Service {
    param(
        [string]$ServiceName
    )

    $servicePath = "services/$ServiceName"
    $version = Get-Version

    # Image names without clx- prefix for pool manager
    $imageName = $ServiceName
    # Also tag with clx- prefix for backward compatibility
    $imageNameClx = "clx-$ServiceName"

    if (-not (Test-Path $servicePath)) {
        Write-ColorOutput "Error: Service directory $servicePath not found" "Red"
        return $false
    }

    if (-not (Test-Path "$servicePath/Dockerfile")) {
        Write-ColorOutput "Error: Dockerfile not found in $servicePath" "Red"
        return $false
    }

    Write-ColorOutput "Building $ServiceName (version $version)..." "Yellow"

    # Redirect docker output to host to prevent it from being captured in return value
    docker build `
        -f "$servicePath/Dockerfile" `
        -t "${imageName}:${version}" `
        -t "${imageName}:latest" `
        -t "${imageNameClx}:${version}" `
        -t "${imageNameClx}:latest" `
        --build-arg SERVICE_PATH=$servicePath `
        --build-arg COMMON_PATH=. `
        . | Out-Host

    if ($LASTEXITCODE -eq 0) {
        Write-ColorOutput "✓ Successfully built ${imageName}:${version}" "Green"
        Write-ColorOutput "  Tagged as: ${imageName}:${version}, ${imageName}:latest" "Green"
        Write-ColorOutput "  Tagged as: ${imageNameClx}:${version}, ${imageNameClx}:latest" "Green"
        return $true
    } else {
        Write-ColorOutput "✗ Failed to build $imageName" "Red"
        return $false
    }
}

# Check if we're in the right directory
if (-not ((Test-Path "services") -and (Test-Path "clx-common"))) {
    Write-ColorOutput "Error: This script must be run from the root of the clx project" "Red"
    Write-ColorOutput "Current directory: $(Get-Location)" "Red"
    Write-ColorOutput "Expected to find: services/ and clx-common/ directories" "Red"
    exit 1
}

# If no arguments, build all services
if ($Services.Count -eq 0) {
    Write-ColorOutput "Building all services..." "Yellow"
    $allSucceeded = $true

    foreach ($service in $AvailableServices) {
        $result = Build-Service -ServiceName $service
        if (-not $result) {
            $allSucceeded = $false
        }
        Write-Host ""
    }

    if ($allSucceeded) {
        Write-ColorOutput "✓ All services built successfully" "Green"
        exit 0
    } else {
        Write-ColorOutput "✗ Some services failed to build" "Red"
        exit 1
    }
} else {
    # Build specified services
    $allSucceeded = $true

    foreach ($service in $Services) {
        if ($AvailableServices -contains $service) {
            $result = Build-Service -ServiceName $service
            if (-not $result) {
                $allSucceeded = $false
            }
            Write-Host ""
        } else {
            Write-ColorOutput "Error: Unknown service '$service'" "Red"
            Write-ColorOutput "Available services: $($AvailableServices -join ', ')" "Yellow"
            exit 1
        }
    }

    if ($allSucceeded) {
        Write-ColorOutput "Done!" "Green"
        exit 0
    } else {
        exit 1
    }
}
