#!/usr/bin/env bash
# Release every formula in this tap that is behind: `make release_tools [APPLY=1]`.
#
# Behind: the tool says a newer version than its formula pins, or the formula
# still has a placeholder sha256. ai-ui is left out; it releases with
# `make release`, which also moves distribution.env.
#
# --dry-run (the default) prints the plan and each release's steps; it
# commits, tags and pushes nothing. --apply releases them one by one through
# scripts/tool-release.sh (tag, push, pin, install, test), then moves the old
# ~/bin copies of their commands into an archive folder so the brew ones run.
set -euo pipefail

mode=${1:---dry-run}
case "$mode" in --apply|--dry-run) ;; *) echo "usage: release-tools.sh [--dry-run|--apply]" >&2; exit 2 ;; esac
ARCHIVE="$HOME/bin/_archive/replaced-by-brew-$(date +%F)"

field() { sed -n "s/^  $2 \"\(.*\)\"$/\1/p" "$1" | head -1; }
has_placeholder_sha() { grep -qE '^  sha256 "0{64}"' "$1"; }
wanted_version() {  # what a tool kept in this tap says; for another repo's tool, what its formula asks for
  if [ -x "./$1" ]; then "./$1" version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1
  else field "Formula/$1.rb" version; fi
}

behind=()
printf '%-16s %-8s %-8s %s\n' FORMULA WANTED PINNED STATE
for formula in Formula/*.rb; do
  name=$(basename "$formula" .rb)
  case "$name" in ai-ui*) continue ;; esac
  wanted=$(wanted_version "$name")
  pinned=$(field "$formula" version)
  if [ "$wanted" = "$pinned" ] && ! has_placeholder_sha "$formula"; then
    state="up to date"
  else
    state="release"
    behind+=("$name $wanted")
  fi
  printf '%-16s %-8s %-8s %s\n' "$name" "$wanted" "$pinned" "$state"
done

if [ ${#behind[@]} -eq 0 ]; then
  echo "Every formula is released."
  exit 0
fi

if [ "$mode" = --apply ]; then
  if [ -n "$(git status --porcelain)" ]; then
    echo "Commit or stash your changes first: each release tags HEAD." >&2
    exit 1
  fi
  read -r -p "Type release to tag, push, pin, install and test ${#behind[@]} formula(e): " answer
  if [ "$answer" != release ]; then
    echo "Nothing done."
    exit 1
  fi
fi

for item in "${behind[@]}"; do
  echo
  echo "== $item"
  scripts/tool-release.sh "$mode" $item
done

[ "$mode" = --apply ] || exit 0

echo
echo "== Old ~/bin copies of the released commands move to $ARCHIVE"
for item in "${behind[@]}"; do
  name=${item%% *}
  brew list --formula "$name" >/dev/null 2>&1 || continue  # not installed: still waiting for its release
  for installed in "$(brew --prefix "$name")"/bin/*; do
    command=$(basename "$installed")
    old="$HOME/bin/$command"
    if [ -f "$old" ] && [ "$(stat -f %l "$old")" -gt 1 ]; then
      echo "  left $old alone: it is hard-linked with other copies, so moving it would split that set"
    elif [ -f "$old" ] && [ ! -L "$old" ]; then
      mkdir -p "$ARCHIVE"
      mv "$old" "$ARCHIVE/"
      echo "  moved $old aside"
    fi
    echo "  $command now runs $(command -v "$command" || echo "nothing on PATH")"
  done
done
