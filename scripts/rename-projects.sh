#!/usr/bin/env bash
# Rename projects whose names changed: `make rename_projects [APPLY=1]`.
#
# For each project it moves the GitHub repository, points the local clone at
# the new address, renames the local folder, and carries the folder's Claude
# Code memory along. Without --apply it only says what it would do. With
# --apply it does each step, saying what it is doing first.
set -euo pipefail

PROJECTS_DIR=$(cd "$(dirname "$0")/../.." && pwd)
CLAUDE_PROJECTS="$HOME/.claude/projects"

# old GitHub repository        new GitHub repository  old folder          new folder
RENAMES=(
  "opencoca/MEDIA-Storyboarder Sage-is/comicreel      MEDIA-Storyboarder  MEDIA-ComicReel"
  "opencoca/talking            Sage-is/talking        local-whisper       APP-Talking"
)

apply=0
case "${1:---dry-run}" in
  --apply)   apply=1 ;;
  --dry-run) ;;
  *)         echo "usage: rename-projects.sh [--dry-run|--apply]" >&2; exit 2 ;;
esac

say() { printf '\n%s\n' "$*"; }
run() {  # show a command; with --apply, run it too
  printf '    $ %s\n' "$(printf '%q ' "$@")"  # quoted, so a copied line runs as shown
  if [ "$apply" = 1 ]; then "$@"; fi
}
full_name() { gh api "repos/$1" --jq .full_name 2>/dev/null || true; }  # follows GitHub's redirects
memory_dir() { echo "$CLAUDE_PROJECTS/$(echo "$PROJECTS_DIR/$1" | sed 's/[^a-zA-Z0-9]/-/g')"; }

move_repo() {
  local old=$1 new=$2
  say "GitHub: move $old to $new. GitHub keeps redirecting the old address for web, git and API calls."
  if [ "$(full_name "$new")" = "$new" ]; then
    echo "    already at $new"
    return
  fi
  if [ "${old%%/*}" = "${new%%/*}" ]; then
    run gh repo rename --repo "$old" "${new##*/}" --yes
  else
    run gh api --method POST "repos/$old/transfer" -f new_owner="${new%%/*}" -f new_name="${new##*/}" --silent
  fi
  [ "$apply" = 1 ] || return 0
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do  # a transfer finishes within seconds, but not instantly
    if [ "$(full_name "$new")" = "$new" ]; then echo "    done: https://github.com/$new"; return; fi
    sleep 5
  done
  echo "    not there yet: check https://github.com/$new, then run this again" >&2
  exit 1
}

point_clone_at() {
  local dir=$1 new=$2 url
  say "Local clone: point its origin remote at $new"
  url=$(git -C "$dir" remote get-url origin)
  case "$url" in
    git@*) url="git@github.com:$new.git" ;;
    *)     url="https://github.com/$new.git" ;;
  esac
  run git -C "$dir" remote set-url origin "$url"
}

rename_folder() {
  local old_dir=$1 new_dir=$2
  say "Folder: rename $old_dir to $new_dir. Close editors and terminals open in it first."
  if [ -d "$PROJECTS_DIR/$new_dir" ]; then
    echo "    already renamed"
  else
    run mv "$PROJECTS_DIR/$old_dir" "$PROJECTS_DIR/$new_dir"
  fi
}

move_memory() {
  local old_mem new_mem
  old_mem=$(memory_dir "$1"); new_mem=$(memory_dir "$2")
  say "Claude Code: carry this folder's memory and sessions over to its new name"
  if [ ! -d "$old_mem" ]; then
    echo "    nothing kept under the old name"
  elif [ -e "$new_mem" ]; then
    echo "    both $old_mem and $new_mem exist; merge them by hand"
  else
    run mv "$old_mem" "$new_mem"
  fi
}

gh auth status >/dev/null 2>&1 || { echo "Log in to GitHub first: gh auth login" >&2; exit 1; }
if [ "$apply" = 1 ]; then
  read -r -p "Type rename to move ${#RENAMES[@]} projects on GitHub and on this Mac: " answer
  if [ "$answer" != rename ]; then echo "Nothing done."; exit 1; fi
else
  echo "Dry run: nothing changes. APPLY=1 does it."
fi

old_paths=()
for row in "${RENAMES[@]}"; do
  read -r old_repo new_repo old_dir new_dir <<<"$row"
  echo; echo "== $old_dir → $new_dir"
  dir="$PROJECTS_DIR/$old_dir"; [ -d "$dir" ] || dir="$PROJECTS_DIR/$new_dir"
  move_repo "$old_repo" "$new_repo"
  point_clone_at "$dir" "$new_repo"
  rename_folder "$old_dir" "$new_dir"
  move_memory "$old_dir" "$new_dir"
  old_paths+=(-e "$PROJECTS_DIR/$old_dir")
done

say "Settings that still name an old folder (review these by hand):"
leftovers=$(grep -l "${old_paths[@]}" "$HOME"/.claude/settings*.json "$PROJECTS_DIR"/*/.claude/settings*.json 2>/dev/null || true)
echo "${leftovers:-none}" | sed 's/^/    /'

cat <<EOF

Next, in this order:
  1. Commit and push the rename in each project (MEDIA-ComicReel, APP-Talking).
  2. Release ComicReel 2.0.0-alpha.3 from MEDIA-ComicReel: the tap's comicreel launcher pins it.
  3. Release the tap: make release_tools APPLY=1
  4. Other Macs: rename the same folders there, or let your sync tool carry the move.
EOF
