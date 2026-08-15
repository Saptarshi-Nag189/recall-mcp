#!/usr/bin/env pwsh
<#PSScriptInfo
.VERSION 1.0
.GUID 1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d
.AUTHOR Kilo
.DESCRIPTION PowerShell wrapper for recall_main.py
#>

param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Args
)

$pythonScript = Join-Path $env:USERPROFILE ".shared_memory\recall_main.py"

if (-not (Test-Path $pythonScript)) {
    Write-Error "recall_main.py not found at: $pythonScript"
    exit 1
}

& python $pythonScript @Args
exit $LASTEXITCODE
