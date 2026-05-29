#!/bin/bash
# Install repo git hooks (run after clone or from project root).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SRC="$ROOT/scripts/prepare-commit-msg"
HOOK_DST="$ROOT/.git/hooks/prepare-commit-msg"
if [ ! -d "$ROOT/.git" ]; then
  echo "Error: not a git repository ($ROOT)" >&2
  exit 1
fi
cp "$HOOK_SRC" "$HOOK_DST"
chmod +x "$HOOK_DST"
echo "Installed prepare-commit-msg hook -> $HOOK_DST"
