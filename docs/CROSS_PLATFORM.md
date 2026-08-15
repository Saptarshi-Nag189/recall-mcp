# Cross-Platform Notes for Recall + Kilo

## Windows

- **PowerShell execution policy**: may need `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` or run with `-ExecutionPolicy Bypass`
- **PATH update** requires shell restart (installer prints reminder)
- **MCP config paths** use `%USERPROFILE%` (works for Claude/Codex on Windows)
- **No auto-capture hook** (Claude Stop hook is bash-only); use manual `recall_extract.py`
- `recall.ps1` is copied (not symlinked) to avoid admin requirement

## WSL2

- Runs Linux `install.sh` inside WSL
- **Separate DB** from native Windows install
- Access Windows files via `/mnt/c/...` if needed

## Linux/macOS

- Standard bash `install.sh`
- `~/.local/bin` must be in PATH (usually default)
- **Auto-capture hook** works for Claude Code

## Kilo Integration (all platforms)

- `kilo_memory_helper.py` is pure Python — works everywhere
- Wrapper function in shell config (`~/.bashrc` / `$PROFILE`)
- `kilo_session_start.ps1` reads JSONL directly (fast)
- `kilo_session_end.ps1` uses MCP subprocess for writes
- `kilo -c` does **NOT** run wrapper; use `kilo` or `kilo run`

## File Paths

- **Linux**: `~/.shared_memory/`
- **Windows**: `%USERPROFILE%\.shared_memory\`
- Both use append-only JSONL banks + SQLite DB

## Troubleshooting

- `"recall not found"` → restart shell, check PATH
- `"MCP server not responding"` → check `python3` on PATH, `recall_mcp.py` exists
- `"Permission denied"` on Windows → check execution policy
