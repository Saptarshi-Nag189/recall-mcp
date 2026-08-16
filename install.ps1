<# 
.SYNOPSIS
    recall — install or update for Windows PowerShell.
.DESCRIPTION
    Copies runtime files to ~/.shared_memory, puts the CLI on PATH, and registers
    the MCP server with supported CLIs. Safe to re-run: every config edit is backed
    up first and is idempotent.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$srcDir = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) "src"
$homeDir = Join-Path $env:USERPROFILE ".shared_memory"
$binDir  = Join-Path $env:USERPROFILE ".local\bin"
$stamp   = Get-Date -Format "yyyyMMdd_HHmmss"

function Say($msg) { Write-Host "  $msg" }

Write-Host "recall — installing"
Say "source : $srcDir"
Say "runtime: $homeDir"
Write-Host ""

# ── 1. files ────────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
New-Item -ItemType Directory -Force -Path $binDir  | Out-Null

$files = @(
    "recall_store.py",
    "recall_mcp.py",
    "recall_extract.py",
    "recall_fmt.py",
    "recall_main.py",
    "recall.ps1"
)

foreach ($f in $files) {
    Copy-Item (Join-Path $srcDir $f) (Join-Path $homeDir $f) -Force
}
Say "installed $($files.Count) files to $homeDir"

# Copy agent-integration/ directory if it exists
$agentSrc = Join-Path (Split-Path -Parent $srcDir) "agent-integration"
$agentDst = Join-Path $homeDir "agent-integration"
if (Test-Path $agentSrc) {
    if (Test-Path $agentDst) {
        Remove-Item -Recurse -Force $agentDst
    }
    Copy-Item $agentSrc $agentDst -Recurse
    Say "copied agent-integration/ directory"
}

# The store itself is never touched by an install: re-running must not lose entries.
$dbPath = Join-Path $homeDir "recall.db"
if (Test-Path $dbPath) {
    Say "existing store kept: $dbPath"
}

# Also preserve any existing bank files
$banks = Get-ChildItem -Path $homeDir -Filter "*.bank" -ErrorAction SilentlyContinue
if ($banks) {
    Say "existing banks kept: $($banks.Count) file(s)"
}

# ── 2. Add bin dir to user PATH ─────────────────────────────────────────────
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User") ?? ""
$binDirAbs = [System.IO.Path]::GetFullPath($binDir)
if ($currentPath -notlike "*$binDirAbs*") {
    $newPath = if ($currentPath) { "$currentPath;$binDirAbs" } else { $binDirAbs }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Say "added $binDirAbs to user PATH"
} else {
    Say "$binDirAbs already in user PATH"
}

# Copy recall.ps1 to bin dir (not symlink)
Copy-Item (Join-Path $homeDir "recall.ps1") (Join-Path $binDir "recall.ps1") -Force
Say "copied recall.ps1 to $binDir"

# ── 3. MCP registration ─────────────────────────────────────────────────────
$mcpPath = Join-Path $homeDir "recall_mcp.py"

function Register-JsonConfig($cfgPath, $label) {
    if (-not (Test-Path $cfgPath)) {
        Say "$label: not installed, skipped"
        return
    }

    $backup = "$cfgPath.bak.$stamp"
    Copy-Item $cfgPath $backup -Force

    try {
        $json = Get-Content $cfgPath -Raw | ConvertFrom-Json
        if (-not $json.mcpServers) { $json.mcpServers = @{} }
        $json.mcpServers.recall = @{ command = "python"; args = @($mcpPath) }
        $json | ConvertTo-Json -Depth 10 | Set-Content $cfgPath -Encoding UTF8
        Say "$label: registered (backup $backup)"
    }
    catch {
        Move-Item $backup $cfgPath -Force
        Say "$label: FAILED, config restored - $_"
    }
}

function Register-TomlConfig($cfgPath, $label) {
    if (-not (Test-Path $cfgPath)) {
        Say "$label: not installed, skipped"
        return
    }

    $content = Get-Content $cfgPath -Raw
    if ($content -match '^\[mcp_servers\.recall\]') {
        Say "$label: already registered"
        return
    }

    $backup = "$cfgPath.bak.$stamp"
    Copy-Item $cfgPath $backup -Force

    try {
        $tomlAppend = @"
# Searchable store of questions already answered, with the evidence behind each answer.
# Shared with the other CLIs - same SQLite file, so an entry written by one is visible to all.
[mcp_servers.recall]
command = "python"
args = ["$mcpPath"]
"@
        Add-Content $cfgPath $tomlAppend -Encoding UTF8
        Say "$label: registered (backup $backup)"
    }
    catch {
        Move-Item $backup $cfgPath -Force
        Say "$label: FAILED, config restored - $_"
    }
}

Register-JsonConfig (Join-Path $env:USERPROFILE ".claude.json")          "Claude Code"
Register-JsonConfig (Join-Path $env:USERPROFILE ".gemini\settings.json") "Gemini"
Register-TomlConfig (Join-Path $env:USERPROFILE ".codex\config.toml")    "Codex"

# ── 4. verify ───────────────────────────────────────────────────────────────
Write-Host ""
$recallCli = Join-Path $homeDir "recall.ps1"
if (Test-Path $recallCli) {
    try {
        $stats = & $recallCli stats 2>$null
        if ($LASTEXITCODE -eq 0 -and $stats) {
            Say "CLI works: $($stats.Trim())"
        } else {
            Say "WARNING: 'recall stats' failed - check python is on PATH"
        }
    }
    catch {
        Say "WARNING: 'recall stats' failed - check python is on PATH"
    }
}

# Test MCP server initialize
try {
    $init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
    $result = $init | & python $mcpPath 2>$null
    if ($result -match "serverInfo") {
        Say "MCP server responds"
    } else {
        Say "WARNING: MCP server did not respond to initialize"
    }
}
catch {
    Say "WARNING: MCP server did not respond to initialize"
}

Write-Host ""
Write-Host "Done. Restart PowerShell to load the updated PATH."
Write-Host "Try:  recall stats   |   recall search `"something`"   |   recall --help"
