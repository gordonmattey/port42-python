#!/bin/bash
# claude_resident.sh — make Claude Code a resident agent in a Port42 channel
#
# Usage:
#   ./claude_resident.sh --invite "<url>" --name "scout"
#
# Listens for @mentions of --name in the channel, pipes each one to `claude`,
# and sends the reply back. Requires: port42, claude (Claude Code CLI)

set -euo pipefail

INVITE=""
NAME="$(basename "$PWD")"
SYSTEM_PROMPT="You are ${NAME}, a resident agent in a Port42 channel. Reply concisely. Do not use markdown unless asked."

while [[ $# -gt 0 ]]; do
    case $1 in
        --invite|-i) INVITE="$2"; shift 2 ;;
        --name|-n)   NAME="$2"; SYSTEM_PROMPT="You are ${2}, a resident agent in a Port42 channel. Reply concisely. Do not use markdown unless asked."; shift 2 ;;
        --system|-s) SYSTEM_PROMPT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$INVITE" ]]; then
    echo "Usage: $0 --invite \"<url>\" [--name \"scout\"] [--system \"prompt\"]"
    exit 1
fi

if ! command -v claude &>/dev/null; then
    echo "Error: claude CLI not found. Install Claude Code: https://claude.ai/code"
    exit 1
fi

if ! command -v port42 &>/dev/null; then
    echo "Error: port42 not found. Install: pip install port42"
    exit 1
fi

echo "[claude_resident] starting as @${NAME}"
echo "[claude_resident] ctrl+c to stop"

port42 send "ready — listening for @${NAME} mentions" \
    --invite "$INVITE" --name "$NAME"

port42 listen \
    --invite "$INVITE" \
    --name "$NAME" \
    --mentions-only | while IFS= read -r line; do

    sender=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sender','?'))" 2>/dev/null || echo "?")
    text=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('text',''))" 2>/dev/null || echo "$line")

    echo "[claude_resident] message from ${sender}: ${text}"

    reply=$(echo "${SYSTEM_PROMPT}

${sender} says: ${text}

Reply in one or two sentences." | claude --print --no-markdown 2>/dev/null)

    if [[ -n "$reply" ]]; then
        port42 send "$reply" --invite "$INVITE" --name "$NAME"
        echo "[claude_resident] sent reply"
    fi
done
