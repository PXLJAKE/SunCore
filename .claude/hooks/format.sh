#!/usr/bin/env bash
# PostToolUse hook: format the file Claude just edited or wrote.
# Receives the tool call as JSON on stdin; never fails the tool call.
set -uo pipefail

payload="$(cat)"
file="$(printf '%s' "$payload" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))' 2>/dev/null)"
[ -z "$file" ] && exit 0
[ -f "$file" ] || exit 0

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$root" || exit 0

case "$file" in
  *.py)
    if command -v uv >/dev/null 2>&1; then
      uv run --quiet ruff format --quiet "$file" >/dev/null 2>&1 || true
      uv run --quiet ruff check --quiet --fix --exit-zero "$file" >/dev/null 2>&1 || true
    fi
    ;;
  *.ts|*.js|*.svelte|*.css|*.json|*.jsonc|*.yaml|*.yml|*.md)
    # Prettier is only configured once the frontend exists (Phase 1); until
    # then this is a no-op so hooks never block.
    if [ -f "$root/frontend/node_modules/.bin/prettier" ]; then
      "$root/frontend/node_modules/.bin/prettier" --log-level silent --write "$file" >/dev/null 2>&1 || true
    fi
    ;;
esac
exit 0
