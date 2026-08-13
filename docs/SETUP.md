# Setup — manual steps and the auto-capture hook

`install.sh` does all of this. This file is for doing it by hand, moving to a new machine, or
understanding what the installer touched.

## What the installer does

1. Copies `recall_store.py`, `recall_mcp.py`, `recall_extract.py` and `recall` into
   `~/.shared_memory/` (override with `RECALL_HOME`).
2. Symlinks `~/.local/bin/recall` → the CLI.
3. Registers the MCP server in each CLI config that exists.
4. Verifies the CLI runs and the MCP server answers `initialize`.

It never touches `recall.db`, so re-running cannot lose entries.

## Manual registration

Two lines, whatever the format:

```
command: python3
args:    ["/home/sapta/.shared_memory/recall_mcp.py"]
```

**Claude Code** — `~/.claude.json`:
```json
{ "mcpServers": {
    "recall": { "command": "python3",
                "args": ["/home/sapta/.shared_memory/recall_mcp.py"] } } }
```

**Gemini** — `~/.gemini/settings.json`: identical shape.

**Codex** — `~/.codex/config.toml`:
```toml
[mcp_servers.recall]
command = "python3"
args = ["/home/sapta/.shared_memory/recall_mcp.py"]
```

Restart the CLI afterwards.

## Antigravity (agy) — two files, both required

Verified working 2026-08-13: asked an unprompted question whose answer existed only in the
store, and agy called `recall_search` on its own.

**1. Server definition** — `~/.gemini/config/mcp_config.json`. The agy binary names this path
itself ("Global Configuration: `~/.gemini/config/mcp_config.json`"):

```json
{ "mcpServers": {
    "recall": { "command": "python3",
                "args": ["/home/sapta/.shared_memory/recall_mcp.py"] } } }
```

**2. Tool permissions** — `~/.gemini/antigravity-cli/settings.json`. Without these agy prompts
for approval on every call:

```json
{ "permissions": { "allow": [
    "mcp(recall/recall_search)", "mcp(recall/recall_add)",
    "mcp(recall/recall_get)",    "mcp(recall/recall_supersede)"
] } }
```

That file has no `mcpServers` block and never defines servers - permissions only.

**Do not use `~/.gemini/settings.json`.** That is the deprecated Gemini CLI's config; agy does
not read it. Registering there does nothing.

Confirmation after restart: a `recall/` directory appears under
`~/.gemini/antigravity-cli/mcp/`, where agy caches each discovered server's tool schemas.

## Auto-capture hook (Claude Code)

Extraction runs from the `Stop` hook, after the session ends, outside the model — no tokens.

`~/.claude/settings.json` must have a Stop hook:

```json
"Stop": [ { "matcher": ".*",
            "hooks": [ { "type": "command",
                         "command": "bash /home/sapta/.shared_memory/claude_session_end_hook.sh" } ] } ]
```

and that script needs this block near the top. It reads the hook payload from stdin, which
carries `transcript_path`:

```bash
HOOK_STDIN="$(timeout 2 cat 2>/dev/null || true)"
RECALL_DIR="${HOME}/.shared_memory"

if [[ -n "$HOOK_STDIN" && -f "${RECALL_DIR}/recall_extract.py" ]]; then
    TRANSCRIPT="$(printf '%s' "$HOOK_STDIN" | python3 -c '
import json, sys
try:    print(json.load(sys.stdin).get("transcript_path", ""))
except Exception: print("")
' 2>/dev/null || true)"

    if [[ -n "$TRANSCRIPT" && -f "$TRANSCRIPT" ]]; then
        PROJECT="$(basename "${PWD}")"
        CAPTURED="$(timeout 60 python3 "${RECALL_DIR}/recall_extract.py" \
                        --project "$PROJECT" "$TRANSCRIPT" 2>/dev/null | tail -1 || true)"
        [[ -n "$CAPTURED" ]] && printf '  recall: %s\n' "$CAPTURED" >&2
    fi
fi
```

Everything is best-effort with a timeout and `|| true`: a broken capture must cost a lookup
later, never break the session's exit path.

**Test it without waiting for a session to end:**

```bash
T=~/.claude/projects/<dir>/<session>.jsonl
echo "{\"transcript_path\":\"$T\"}" | bash ~/.shared_memory/claude_session_end_hook.sh
```

Expect a line like `recall: 1 transcript(s): 4 candidates, 4 stored`. Re-running stores 0 —
duplicates are detected.

## Backfill existing transcripts

```bash
python3 ~/.shared_memory/recall_extract.py --dry-run ~/.claude/projects/   # preview
python3 ~/.shared_memory/recall_extract.py ~/.claude/projects/            # store
```

Read-only on transcripts. 57 files / 147 MB produced 19 entries and took under a minute.

## Moving to another machine

```bash
scp -r ~/recall-mcp user@host:~/          # the project
scp ~/.shared_memory/recall.db user@host:~/.shared_memory/   # the entries, optional
ssh user@host 'cd ~/recall-mcp && ./install.sh'
```

Only `python3` is required — no pip, no venv, no network.

## Using it in another project

Nothing to do. The store is global (`~/.shared_memory/recall.db`) and `recall` is on PATH, so it
works from any directory. Entries carry a `--project` label if you want to record where something
came from; search is across everything by default.

## Uninstall

```bash
rm ~/.local/bin/recall
rm ~/.shared_memory/recall{,_store.py,_mcp.py,_extract.py}
# keep or delete ~/.shared_memory/recall.db — that is your data
```

Then remove the `recall` entry from each CLI config. The `.bak.<timestamp>` files the installer
wrote are the pre-change versions.

## Troubleshooting

**`recall: command not found`** — `~/.local/bin` is not on PATH for that shell. Use the full path
`~/.shared_memory/recall`, or add the directory to PATH.

**A search returns nothing you expected** — multi-word queries need **2 matching terms**. Try
fewer, more distinctive words. `recall stats` confirms the store is not simply empty.

**The MCP tool does not appear in a CLI** — it needs a restart after registration. Check the
server directly:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 ~/.shared_memory/recall_mcp.py
```
A JSON line containing `serverInfo` means the server is fine and the problem is registration.

**Nothing is being auto-captured** — the extractor is deliberately strict: the answer must carry a
measurement, a commit sha or a file:line. A session of discussion without hard numbers yields
nothing, by design. Check with:

```bash
python3 ~/.shared_memory/recall_extract.py --dry-run <transcript.jsonl>
```
