#!/usr/bin/env bash
# Refuse while the ai-ui formula ships an older AI-UI than AI-UI's newest release:
# brew users would stay behind and nothing would say so. `make ai_ui_formula`
# (which AI-UI's `make ship` runs) catches the formula up.
set -euo pipefail

field() { sed -n "s/^  $2 \"\(.*\)\"$/\1/p" "$1" | head -1; }

formula=Formula/ai-ui.rb
current="$(field "$formula" version)"
[ -n "$current" ] || current="$(basename "$(field "$formula" url)" .tar.gz | sed 's/^v//')"

# AI-UI's release tags: v3.2.0, and four numbers for its hotfixes (v3.2.0.1).
newest="$(curl -fsSL "https://api.github.com/repos/Sage-is/AI-UI/tags?per_page=100" \
  | sed -n 's/.*"name": *"v\{0,1\}\([0-9][0-9.]*\)".*/\1/p' \
  | grep -E '^[0-9]+(\.[0-9]+){2,3}$' | sort -V | tail -1 || true)"
[ -n "$newest" ] || { echo "FAIL: could not read AI-UI's release tags from GitHub"; exit 1; }

if [ "$current" != "$newest" ] && [ "$(printf '%s\n%s\n' "$current" "$newest" | sort -V | tail -1)" = "$newest" ]; then
  echo "FAIL: the ai-ui formula ships AI-UI $current, but AI-UI released $newest. Run: make ai_ui_formula"
  exit 1
fi
echo "OK: the ai-ui formula ships AI-UI's newest release ($current)."
