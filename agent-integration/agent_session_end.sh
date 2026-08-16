#!/usr/bin/env bash
# agent_session_end.sh
# Called at agent session end. Writes an AgentSession entity to project.json
# and checks if any bank was written this session.

set -uo pipefail

IOT="${HOME}/CDAC_Projects/IOT_security"
PROJECT_JSON="${IOT}/.shared_memory/project.json"
GLOBAL_JSON="${HOME}/.shared_memory/global.json"

# Read session info from environment or args
SESSION_ID="${AGENT_SESSION_ID:-agent_$(date +%Y%m%d_%H%M%S)}"
MODEL_ID="${AGENT_MODEL_ID:-unknown}"
MODEL_LABEL="${AGENT_MODEL_LABEL:-unknown}"
PROVIDER="${AGENT_PROVIDER:-unknown}"
WORKING_TREE="${AGENT_WORKING_TREE:-$(basename "${PWD}")}"
BRANCH="${AGENT_BRANCH:-$(git -C "${PWD}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')}"
TOPICS="${AGENT_TOPICS:-shared memory integration}"
HANDOFF_FROM="${AGENT_HANDOFF_FROM:-}"

START_TIME="${AGENT_START_TIME:-$(date -Iseconds)}"
END_TIME="$(date -Iseconds)"

# Build the AgentSession entity
AGENT_SESSION=$(cat <<EOF
{"type":"entity","name":"Agent_Session_${SESSION_ID}","entityType":"AgentSession","observations":["Model: ${MODEL_ID} (${MODEL_LABEL})","Provider: ${PROVIDER}","Session start: ${START_TIME}","Session end: ${END_TIME}","Working tree: ${WORKING_TREE} (branch ${BRANCH})","Key topics: ${TOPICS}","Handoff from: ${HANDOFF_FROM}"]}
EOF
)

# Append to project.json
echo "${AGENT_SESSION}" >> "${PROJECT_JSON}"
echo "  Added AgentSession: Agent_Session_${SESSION_ID}" >&2

# Check if any bank was written this session (compare mtime against a flag)
FLAG="/tmp/.agent_mem_loaded_${AGENT_PID:-$$}"
BANKS=("${GLOBAL_JSON}" "${PROJECT_JSON}")

if [[ -f "$FLAG" ]]; then
    written=()
    for bank in "${BANKS[@]}"; do
        [[ -f "$bank" ]] || continue
        if [[ "$bank" -nt "$FLAG" ]]; then
            written+=("$(basename "$bank")")
        fi
    done

    if [[ ${#written[@]} -eq 0 ]]; then
        {
            echo ""
            echo "  ── shared memory: nothing written this session ──"
            echo "  Another CLI (Claude / Codex / Antigravity) picking this repo up will not see what"
            echo "  happened here. If anything durable was decided - a trap, a rejected"
            echo "  approach and why, an interface someone would re-derive - ask Agent to"
            echo "  record it before switching."
            echo "  Skip this when the session changed nothing worth carrying forward."
            echo ""
        } >&2
    else
        printf '  shared memory updated this session: %s\n' "${written[*]}" >&2
    fi
fi

rm -f "$FLAG"
exit 0