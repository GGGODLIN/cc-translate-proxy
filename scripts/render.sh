#!/usr/bin/env bash
# Render helper: tail the i18n proxy emit file through glow for live markdown view.
# Usage: ./scripts/render.sh <session-id>
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <session-id>"
  echo "Hint: session-id is logged to stderr when proxy receives a request."
  exit 1
fi

SESSION="$1"
PROXY_HOME="${CC_I18N_PROXY_HOME:-$HOME/.cc-i18n-proxy}"
EMIT_DIR="${CC_I18N_PROXY_EMIT_DIR:-$PROXY_HOME/emit}"
FILE="$EMIT_DIR/cc-i18n-${SESSION}.md"

if ! command -v glow >/dev/null 2>&1; then
  echo "Install glow first: brew install glow"
  exit 1
fi

# Create file if not exists so tail -F doesn't error
touch "$FILE"

tail -n +1 -F "$FILE" | glow -s dark -
