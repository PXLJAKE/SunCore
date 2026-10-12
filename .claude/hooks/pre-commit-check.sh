#!/usr/bin/env bash
# PreToolUse hook on Bash: when the command is a `git commit`, run the fast
# checks first (lint, types, quick tests). Exit 2 blocks the commit and feeds
# the output back to Claude; anything else lets the command through.
set -uo pipefail

payload="$(cat)"
cmd="$(printf '%s' "$payload" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("command",""))' 2>/dev/null)"
case "$cmd" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$root" || exit 0
[ -x scripts/check.sh ] || exit 0
command -v uv >/dev/null 2>&1 || exit 0

out="$(FAST=1 scripts/check.sh 2>&1)"
status=$?
if [ $status -ne 0 ]; then
  printf 'Fast checks failed, commit blocked:\n%s\n' "$out" >&2
  exit 2
fi
exit 0
