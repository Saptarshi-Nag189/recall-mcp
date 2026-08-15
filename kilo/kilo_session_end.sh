#!/usr/bin/env bash
# kilo_session_end.sh
# Called at Kilo session end. Writes a ModelSession entity to project.json
# and checks if any bank was written this session.

set -uo pipefail

IOT="${HOME}/CDAC_Projects/IOT_security"
PROJECT_JSON="${IOT}/.shared_memory/project.json"
GLOBAL_JSON="${HOME}/.shared_memory/global.json"

# Read session info from environment or args
SESSION_ID="${KILO_SESSION_ID:-kilo_$(date +%Y%m%d_%H%M%S)}"
MODEL_ID="${KILO_MODEL_ID:-nemotron-3-ultra-550b-a55b:free}"
MODEL_LABEL="${KILO_MODEL_LABEL:-nemotron 3 ultra high}"
PROVIDER="${KILO_PROVIDER:-kilo / nvidia}"
WORKING_TREE="${KILO_WORKING_TREE:-$(basename "${PWD}")}"
BRANCH="${KILO_BRANCH:-$(git -C "${PWD}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')}"
TOPICS="${KILO_TOPICS:-shared memory integration}"
HANDOFF_FROM="${KILO_HANDOFF_FROM:-}"

START_TIME="${KILO_START_TIME:-$(date -Iseconds)}"
END_TIME="$(date -Iseconds)"

# Build the ModelSession entity
MODEL_SESSION=$(cat <<EOF
{"type":"entity","name":"Kilo_Session_${SESSION_ID}","entityType":"ModelSession","observations":["Model: ${MODEL_ID} (${MODEL_LABEL})","Provider: ${PROVIDER}","Session start: ${START_TIME}","Session end: ${END_TIME}","Working tree: ${WORKING_TREE} (branch ${BRANCH})","Key topics: ${TOPICS}","Handoff from: ${HANDOFF_FROM}"]}
EOF
)

# Append to project.json
echo "${MODEL_SESSION}" >> "${PROJECT_JSON}"
echo "  Added ModelSession: Kilo_Session_${SESSION_ID}" >&2

# Check if any bank was written this session (compare mtime against a flag)
FLAG="/tmp/.kilo_mem_loaded_${KILO_PID:-$$}"
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
            echo "  approach and why, an interface someone would re-derive - ask Kilo to"
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