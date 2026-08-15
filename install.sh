#!/usr/bin/env bash
#
# recall — install or update.
#
# Copies the four files into a runtime directory, puts the CLI on PATH, and registers the MCP
# server with whichever supported CLIs are present. Safe to re-run: every config edit is backed
# up first and is idempotent.
#
#   ./install.sh                      # install to ~/.shared_memory (default)
#   RECALL_HOME=~/tools ./install.sh  # somewhere else
#
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/src" && pwd)"
HOME_DIR="${RECALL_HOME:-${HOME}/.shared_memory}"
BIN_DIR="${HOME}/.local/bin"
STAMP="$(date +%Y%m%d_%H%M%S)"

say() { printf '  %s\n' "$*"; }

echo "recall — installing"
say "source : ${SRC}"
say "runtime: ${HOME_DIR}"
echo

# ── 1. files ─────────────────────────────────────────────────────────────────
mkdir -p "$HOME_DIR" "$BIN_DIR"
for f in recall_store.py recall_mcp.py recall_extract.py recall_fmt.py recall_main.py recall; do
    cp "${SRC}/${f}" "${HOME_DIR}/${f}"
done
# Copy kilo/ directory recursively (session scripts & helper)
if [[ -d "${SRC}/../kilo" ]]; then
    cp -r "${SRC}/../kilo" "${HOME_DIR}/"
    chmod +x "${HOME_DIR}/kilo/"*.sh 2>/dev/null || true
    say "installed kilo/ directory"
fi
chmod +x "${HOME_DIR}/recall" "${HOME_DIR}/recall_mcp.py"
ln -sf "${HOME_DIR}/recall" "${BIN_DIR}/recall"
say "installed 6 files, linked ${BIN_DIR}/recall"

# The store itself is never touched by an install: re-running must not lose entries.
if [[ -f "${HOME_DIR}/recall.db" ]]; then
    say "existing store kept: ${HOME_DIR}/recall.db"
fi

# ── 2. MCP registration ──────────────────────────────────────────────────────
MCP_PATH="${HOME_DIR}/recall_mcp.py"

# Claude Code / Gemini — JSON with an mcpServers object.
register_json() {
    local cfg="$1" label="$2"
    [[ -f "$cfg" ]] || { say "${label}: not installed, skipped"; return; }
    cp "$cfg" "${cfg}.bak.${STAMP}"
    python3 - "$cfg" "$MCP_PATH" <<'PY'
import json, sys
cfg, mcp = sys.argv[1], sys.argv[2]
with open(cfg) as fh:
    d = json.load(fh)
d.setdefault("mcpServers", {})["recall"] = {"command": "python3", "args": [mcp]}
with open(cfg, "w") as fh:
    json.dump(d, fh, indent=2)
PY
    if [[ $? -eq 0 ]]; then
        say "${label}: registered (backup ${cfg}.bak.${STAMP})"
    else
        mv "${cfg}.bak.${STAMP}" "$cfg"
        say "${label}: FAILED, config restored"
    fi
}

# Codex — TOML with [mcp_servers.NAME] tables.
register_toml() {
    local cfg="$1" label="$2"
    [[ -f "$cfg" ]] || { say "${label}: not installed, skipped"; return; }
    if grep -q '^\[mcp_servers\.recall\]' "$cfg"; then
        say "${label}: already registered"
        return
    fi
    cp "$cfg" "${cfg}.bak.${STAMP}"
    cat >> "$cfg" <<EOF

# Searchable store of questions already answered, with the evidence behind each answer.
# Shared with the other CLIs - same SQLite file, so an entry written by one is visible to all.
[mcp_servers.recall]
command = "python3"
args = ["${MCP_PATH}"]
EOF
    say "${label}: registered (backup ${cfg}.bak.${STAMP})"
}

register_json "${HOME}/.claude.json"          "Claude Code"
register_json "${HOME}/.gemini/settings.json" "Gemini"
register_toml "${HOME}/.codex/config.toml"    "Codex"

# ── 3. auto-capture hook (Claude Code only) ──────────────────────────────────
HOOK="${HOME}/.shared_memory/claude_session_end_hook.sh"
if [[ -f "$HOOK" ]] && ! grep -q "recall_extract.py" "$HOOK"; then
    say "NOTE: Stop hook exists but does not call recall_extract.py."
    say "      Auto-capture stays off until it does - see docs/SETUP.md."
elif [[ -f "$HOOK" ]]; then
    say "auto-capture: wired into the Stop hook"
fi

# ── 4. verify ────────────────────────────────────────────────────────────────
echo
if "${HOME_DIR}/recall" stats >/dev/null 2>&1; then
    say "CLI works: $("${HOME_DIR}/recall" stats | head -2 | tail -1 | xargs)"
else
    say "WARNING: 'recall stats' failed - check python3 is on PATH"
fi

printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | timeout 10 python3 "$MCP_PATH" 2>/dev/null | grep -q serverInfo \
  && say "MCP server responds" \
  || say "WARNING: MCP server did not respond to initialize"

echo
echo "Done. Restart any running CLI to load the MCP server."
echo "Try:  recall stats   |   recall search \"something\"   |   recall --help"
