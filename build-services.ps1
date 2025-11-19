#!/usr/bin/env pwsh

# Build script for CLX services (PowerShell version for Windows)
# Must be run from the root of the clx project

param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Services
)

# Enable BuildKit
$env:DOCKER_BUILDKIT = "1"

# Available services (short names matching docker/ subdirectories)
$AvailableServices = @("plantuml", "drawio", "notebook")

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
    $pyprojectPath = "pyproject.toml"
    if (-not (Test-Path $pyprojectPath)) {
        return "0.4.0"  # fallback version
    }

    $content = Get-Content $pyprojectPath
    $versionLine = $content | Select-String -Pattern '^version = "(.+)"' | Select-Object -First 1
    if ($versionLine) {
        return $versionLine.Matches.Groups[1].Value
    }
    return "0.4.0"  # fallback version
}

# Function to build a service
function Build-Service {
    param(
        [string]$ServiceName
    )

    $dockerPath = "docker/$ServiceName"
    $version = Get-Version

    # Map short names to full service names for image tags
    $fullServiceName = switch ($ServiceName) {
        "plantuml" { "plantuml-converter" }
        "drawio" { "drawio-converter" }
        "notebook" { "notebook-processor" }
        default { $ServiceName }
    }

    # Image names with full service name for backward compatibility
    $imageName = $fullServiceName
    # Also tag with clx- prefix for backward compatibility
    $imageNameClx = "clx-$fullServiceName"
    # Also tag with mhoelzl/ namespace to match docker-compose
    $imageNameHub = "mhoelzl/clx-$fullServiceName"

    if (-not (Test-Path $dockerPath)) {
        Write-ColorOutput "Error: Docker directory $dockerPath not found" "Red"
        return $false
    }

    if (-not (Test-Path "$dockerPath/Dockerfile")) {
        Write-ColorOutput "Error: Dockerfile not found in $dockerPath" "Red"
        return $false
    }

    Write-ColorOutput "Building $ServiceName (version $version)..." "Yellow"

    # Redirect docker output to host to prevent it from being captured in return value
    docker buildx build `
        -f "$dockerPath/Dockerfile" `
        -t "${imageName}:${version}" `
        -t "${imageName}:latest" `
        -t "${imageNameClx}:${version}" `
        -t "${imageNameClx}:latest" `
        -t "${imageNameHub}:${version}" `
        -t "${imageNameHub}:latest" `
        --build-arg DOCKER_PATH=$dockerPath `
        . | Out-Host

    if ($LASTEXITCODE -eq 0) {
        Write-ColorOutput "✓ Successfully built ${imageName}:${version}" "Green"
        Write-ColorOutput "  Tagged as: ${imageName}:${version}, ${imageName}:latest" "Green"
        Write-ColorOutput "  Tagged as: ${imageNameClx}:${version}, ${imageNameClx}:latest" "Green"
        Write-ColorOutput "  Tagged as: ${imageNameHub}:${version}, ${imageNameHub}:latest" "Green"
        return $true
    } else {
        Write-ColorOutput "✗ Failed to build $imageName" "Red"
        return $false
    }
}

# Check if we're in the right directory
if (-not ((Test-Path "docker") -and (Test-Path "pyproject.toml"))) {
    Write-ColorOutput "Error: This script must be run from the root of the clx project" "Red"
    Write-ColorOutput "Current directory: $(Get-Location)" "Red"
    Write-ColorOutput "Expected to find: docker/ directory and pyproject.toml file" "Red"
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
