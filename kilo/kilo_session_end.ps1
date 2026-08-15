<#
.SYNOPSIS
    Kilo session end script - writes ModelSession entity via MCP JSON-RPC
.DESCRIPTION
    Reads environment variables, prompts for missing ones, builds ModelSession entity,
    writes via recall_mcp.py MCP server using recall_add tool, checks for bank writes.
#>

param(
    [string]$MemoryDir = "$env:USERPROFILE\.shared_memory",
    [string]$McpScript = "$env:USERPROFILE\recall-mcp\src\recall_mcp.py"
)

# Read environment variables
$sessionId = $env:KILO_SESSION_ID
$topics = $env:KILO_TOPICS
$handoffFrom = $env:KILO_HANDOFF_FROM
$modelId = $env:KILO_MODEL_ID
$provider = $env:KILO_PROVIDER
$workingTree = $env:KILO_WORKING_TREE
$branch = $env:KILO_BRANCH

# Prompt for missing required variables
if (-not $topics) {
    $topics = Read-Host "Enter KILO_TOPICS (comma-separated topics covered this session)"
}
if (-not $handoffFrom) {
    $handoffFrom = Read-Host "Enter KILO_HANDOFF_FROM (context for next session)"
}

# Validate required vars
if (-not $sessionId) {
    Write-Error "KILO_SESSION_ID not set"
    exit 1
}

# Build ModelSession entity
$timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
$entity = @{
    type = "ModelSession"
    name = "session-$sessionId"
    session_id = $sessionId
    topics = $topics -split ',' | ForEach-Object { $_.Trim() }
    handoff_from = $handoffFrom
    model_id = $modelId
    provider = $provider
    working_tree = $workingTree
    branch = $branch
    started_at = $timestamp  # This session's start (approximate)
    ended_at = $timestamp
    cwd = (Get-Location).Path
} | ConvertTo-Json -Depth 5 -Compress

Write-Host "Built ModelSession entity" -ForegroundColor Cyan

# Spawn recall_mcp.py subprocess and send JSON-RPC
$python = "python3"
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) {
    $python = "python"
}

# JSON-RPC requests
$initializeReq = @{
    jsonrpc = "2.0"
    id = 1
    method = "initialize"
    params = @{
        protocolVersion = "2024-11-05"
        capabilities = @{}
        clientInfo = @{
            name = "kilo-session-end"
            version = "1.0.0"
        }
    }
} | ConvertTo-Json -Compress

$toolsCallReq = @{
    jsonrpc = "2.0"
    id = 2
    method = "tools/call"
    params = @{
        name = "recall_add"
        arguments = @{
            question = "ModelSession: $sessionId"
            answer = $entity
            evidence = @("Session ended at $timestamp")
            tags = "kilo session ModelSession $($topics -replace ',', ' ')"
            project = (Split-Path $workingTree -Leaf)
            session_id = $sessionId
            source = "session_end"
            confidence = 1
        }
    }
} | ConvertTo-Json -Compress

# Start MCP subprocess
Write-Host "Starting MCP server..." -ForegroundColor Gray
$process = Start-Process $python -ArgumentList $McpScript -NoNewWindow -PassThru -RedirectStandardInput -RedirectStandardOutput -RedirectStandardError

if (-not $process) {
    Write-Error "Failed to start recall_mcp.py"
    exit 1
}

try {
    # Send initialize request
    $process.StandardInput.WriteLine($initializeReq)
    $process.StandardInput.Flush()
    
    # Read initialize response
    $initResponse = $process.StandardOutput.ReadLine()
    if (-not $initResponse) {
        Write-Error "No initialize response from MCP"
        exit 1
    }
    
    $initResult = $initResponse | ConvertFrom-Json
    if ($initResult.error) {
        Write-Error "Initialize failed: $($initResult.error.message)"
        exit 1
    }
    
    Write-Host "MCP initialized" -ForegroundColor Green
    
    # Send tools/call request
    $process.StandardInput.WriteLine($toolsCallReq)
    $process.StandardInput.Flush()
    
    # Read tools/call response
    $callResponse = $process.StandardOutput.ReadLine()
    if (-not $callResponse) {
        Write-Error "No tools/call response from MCP"
        exit 1
    }
    
    $callResult = $callResponse | ConvertFrom-Json
    if ($callResult.error) {
        Write-Error "Write failed: $($callResult.error.message)"
        exit 1
    }
    
    $resultText = $callResult.result.content[0].text
    Write-Host "Added ModelSession: $resultText" -ForegroundColor Green
    
}
finally {
    # Clean up process
    $process.Kill()
    $process.WaitForExit()
}

# Check for bank writes this session
$flagFile = "$env:TEMP\.kilo_mem_loaded_$PID"
$banksWritten = $false

if (Test-Path $flagFile) {
    $flagTime = (Get-Item $flagFile).LastWriteTime
    $projectFile = Join-Path $MemoryDir "project.json"
    $globalFile = Join-Path $MemoryDir "global.json"
    
    $projectWritten = $false
    $globalWritten = $false
    
    if (Test-Path $projectFile) {
        $projectTime = (Get-Item $projectFile).LastWriteTime
        if ($projectTime -gt $flagTime) {
            $projectWritten = $true
            $banksWritten = $true
        }
    }
    
    if (Test-Path $globalFile) {
        $globalTime = (Get-Item $globalFile).LastWriteTime
        if ($globalTime -gt $flagTime) {
            $globalWritten = $true
            $banksWritten = $true
        }
    }
    
    if ($banksWritten) {
        $banks = @()
        if ($projectWritten) { $banks += "project" }
        if ($globalWritten) { $banks += "global" }
        Write-Host "Bank write detected: $($banks -join ', ')" -ForegroundColor Yellow
    } else {
        Write-Host "No bank writes this session" -ForegroundColor Gray
    }
    
    # Clean up flag file
    Remove-Item $flagFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "No session flag file found (first run?)" -ForegroundColor Gray
}

Write-Host "`n=== Kilo Session End Complete ===" -ForegroundColor Cyan
