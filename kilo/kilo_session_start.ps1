<# 
.SYNOPSIS
    Kilo session start script - loads shared memory from JSONL files
.DESCRIPTION
    Reads global.json and project.json from $env:USERPROFILE\.shared_memory\,
    parses JSONL entities, and prints formatted session header.
#>

param(
    [string]$MemoryDir = "$env:USERPROFILE\.shared_memory"
)

$ModelInfo = "nemotron-3-ultra-550b-a55b:free (nemotron 3 ultra high)"
$Cwd = Get-Location

Write-Host "`n=== Kilo Session Start ===" -ForegroundColor Cyan
Write-Host "CWD: $Cwd" -ForegroundColor Gray
Write-Host "Model: $ModelInfo" -ForegroundColor Gray
Write-Host "Memory Dir: $MemoryDir" -ForegroundColor Gray
Write-Host ""

function Load-Entities {
    param(
        [string]$FilePath,
        [string]$BankName
    )

    if (-not (Test-Path $FilePath)) {
        Write-Host "  [$BankName] File not found: $FilePath" -ForegroundColor Yellow
        return
    }

    $entities = @()
    $lineNumber = 0

    try {
        Get-Content $FilePath | ForEach-Object {
            $lineNumber++
            $line = $_.Trim()
            if ($line -and $line -ne "") {
                try {
                    $entity = $_ | ConvertFrom-Json -ErrorAction Stop
                    $entities += $entity
                }
                catch {
                    Write-Warning "  [$BankName] Line $lineNumber: Failed to parse JSON - $($_.Exception.Message)"
                }
            }
        }
    }
    catch {
        Write-Host "  [$BankName] Error reading file: $($_.Exception.Message)" -ForegroundColor Red
        return
    }

    if ($entities.Count -eq 0) {
        Write-Host "  [$BankName] No entities found" -ForegroundColor Gray
        return
    }

    Write-Host "  [$BankName] $($entities.Count) entities loaded" -ForegroundColor Green

    foreach ($entity in $entities) {
        $type = if ($entity.type) { $entity.type } else { "unknown" }
        $name = if ($entity.name) { $entity.name } else { "unnamed" }
        
        Write-Host "    [$type] $name" -ForegroundColor White

        if ($entity.observations -and $entity.observations.Count -gt 0) {
            $count = 0
            foreach ($obs in $entity.observations) {
                if ($count -ge 2) { break }
                $truncated = if ($obs.Length -gt 120) { $obs.Substring(0, 120) + "..." } else { $obs }
                Write-Host "      - $truncated" -ForegroundColor Gray
                $count++
            }
            if ($entity.observations.Count -gt 2) {
                Write-Host "      ... and $($entity.observations.Count - 2) more observation(s)" -ForegroundColor DarkGray
            }
        }
    }
}

# Load global memory
$globalFile = Join-Path $MemoryDir "global.json"
Load-Entities -FilePath $globalFile -BankName "GLOBAL"

Write-Host ""

# Load project memory
$projectFile = Join-Path $MemoryDir "project.json"
Load-Entities -FilePath $projectFile -BankName "PROJECT"

Write-Host "`n=== Session Ready ===" -ForegroundColor Cyan
