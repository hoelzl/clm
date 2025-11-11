#!/usr/bin/env pwsh

# Push script for CLX service images to Docker Hub (PowerShell version)
# Usage: .\push-services.ps1 <dockerhub-username> [service1] [service2] ...

param(
    [Parameter(Position=0, Mandatory=$true)]
    [string]$Username,

    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Services
)

# Available services
$AvailableServices = @("drawio-converter", "notebook-processor", "plantuml-converter")

# Default version
$Version = "0.3.0"

# Function to write colored output
function Write-ColorOutput {
    param(
        [string]$Message,
        [string]$Color = "White"
    )
    Write-Host $Message -ForegroundColor $Color
}

# Function to push a service
function Push-Service {
    param(
        [string]$ServiceName,
        [string]$Username,
        [string]$Version
    )

    $localImage = "clx-$ServiceName"
    $remoteImage = "$Username/clx-${ServiceName}:$Version"
    $remoteImageLatest = "$Username/clx-${ServiceName}:latest"

    Write-ColorOutput "Pushing $ServiceName..." "Yellow"

    # Check if local image exists
    $imageExists = docker image inspect $localImage 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput "Error: Local image $localImage not found" "Red"
        Write-ColorOutput "Run .\build-services.ps1 $ServiceName first" "Blue"
        return $false
    }

    # Tag with version
    Write-ColorOutput "Tagging as $remoteImage" "Blue"
    docker tag $localImage $remoteImage | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput "✗ Failed to tag $ServiceName" "Red"
        return $false
    }

    # Tag as latest
    Write-ColorOutput "Tagging as $remoteImageLatest" "Blue"
    docker tag $localImage $remoteImageLatest | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput "✗ Failed to tag $ServiceName" "Red"
        return $false
    }

    # Push version tag
    Write-ColorOutput "Pushing $remoteImage" "Blue"
    docker push $remoteImage | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput "✗ Failed to push $ServiceName" "Red"
        return $false
    }

    # Push latest tag
    Write-ColorOutput "Pushing $remoteImageLatest" "Blue"
    docker push $remoteImageLatest | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-ColorOutput "✗ Failed to push $ServiceName" "Red"
        return $false
    }

    Write-ColorOutput "✓ Successfully pushed $ServiceName" "Green"
    return $true
}

# Verify logged in to Docker Hub
$dockerInfo = docker info 2>$null | Select-String "Username:"
$loggedIn = $dockerInfo -match "Username:\s+$Username"

if (-not $loggedIn) {
    Write-ColorOutput "Warning: Not logged in to Docker Hub as $Username" "Yellow"
    Write-ColorOutput "Please login first:" "Blue"
    Write-Host "  docker login"
    Write-Host ""
    $response = Read-Host "Continue anyway? (y/N)"
    if ($response -notmatch "^[Yy]$") {
        exit 1
    }
}

# If no services specified, push all
if ($Services.Count -eq 0) {
    Write-ColorOutput "Pushing all services to Docker Hub as ${Username}/clx-*:${Version}" "Yellow"
    Write-Host ""

    $allSucceeded = $true
    foreach ($service in $AvailableServices) {
        $result = Push-Service -ServiceName $service -Username $Username -Version $Version
        if (-not $result) {
            $allSucceeded = $false
        }
        Write-Host ""
    }

    if ($allSucceeded) {
        Write-ColorOutput "✓ All services pushed successfully" "Green"
        exit 0
    } else {
        Write-ColorOutput "✗ Some services failed to push" "Red"
        exit 1
    }
} else {
    # Push specified services
    $allSucceeded = $true

    foreach ($service in $Services) {
        if ($AvailableServices -contains $service) {
            $result = Push-Service -ServiceName $service -Username $Username -Version $Version
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
