#!/usr/bin/env bash
# agent_session_start.sh
# Called at agent session start. Loads shared memory banks and prints context.

CWD="${PWD}"
HOME_DIR="$HOME"

MCP_CMD="npx -y @modelcontextprotocol/server-memory"

declare -A BANKS=(
    ["memory_global"]="$HOME_DIR/.shared_memory/global.json"
    ["memory_project"]="$HOME_DIR/CDAC_Projects/IOT_security/.shared_memory/project.json"
)

RELEVANT=("memory_global")
if [[ "$CWD" == *"IOT_security"* ]]; then
    RELEVANT+=("memory_project")
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║          AGENT SHARED MEMORY CONTEXT LOADED          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo "CWD: $CWD"
MODEL_INFO="${AGENT_MODEL_INFO:-$(whoami) agent}"
echo "Model: $MODEL_INFO"
echo ""

for bank in "${RELEVANT[@]}"; do
    FILE="${BANKS[$bank]}"
    if [ ! -s "$FILE" ]; then
        continue
    fi
    echo "--- $bank ---"
    MEMORY_FILE_PATH="$FILE" $MCP_CMD 2>/dev/null << 'MCPEOF' | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    result = d.get('result', {})
    content = result.get('content', [{}])
    text = content[0].get('text', '{}') if content else '{}'
    graph = json.loads(text)
    ents = graph.get('entities', [])
    if not ents:
        print('  (empty)')
    for e in ents[:15]:
        print(f\"  [{e.get('entityType','?')}] {e.get('name','?')}\")
        for o in e.get('observations', [])[:2]:
            print(f\"    {str(o)[:120]}\")
except Exception as ex:
    print(f'  (parse error: {ex})')
" 2>/dev/null
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read_graph","arguments":{}}}
MCPEOF
    echo ""
done

echo "═══════════════════════════════════════════════════════"
echo ""