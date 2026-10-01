#!/usr/bin/env bash
# Point the ai-ui formulae at an AI-UI release. The CLI ships inside AI-UI (its
# cli/ folder), so each AI-UI release is also the CLI's release. Before a new
# major, the current formula becomes ai-ui@<old major>, as brew's versioned formulae do.
#   scripts/ai-ui-formula.sh 3.2.1
set -euo pipefail

version="${1:?usage: scripts/ai-ui-formula.sh VERSION (an AI-UI release)}"
major="${version%%.*}"
formula=Formula/ai-ui.rb
url="https://github.com/Sage-is/AI-UI/archive/refs/tags/v${version}.tar.gz"

field() { sed -n "s/^  $2 \"\(.*\)\"$/\1/p" "$1" | head -1; }

echo "  downloading $url"
tarball="$(mktemp)"
trap 'rm -f "$tarball"' EXIT
curl -fsSL "$url" -o "$tarball"
# A release from before the CLI moved has no cli/: pointing the formula at it would break every install.
for path in cli/ai-ui cli/nuke-sage distribution.env; do
  tar -tzf "$tarball" "*/$path" >/dev/null 2>&1 || { echo "AI-UI v$version has no $path: release AI-UI with its cli/ first." >&2; exit 1; }
done
sha="$(shasum -a 256 "$tarball" | cut -d' ' -f1)"

# The version the formula ships today: its `version` line, else its url's tag.
current="$(field "$formula" version)"
[ -n "$current" ] || current="$(basename "$(field "$formula" url)" .tar.gz | sed 's/^v//')"
old_major="${current%%.*}"

if [ "$old_major" != "$major" ] && [ ! -f "Formula/ai-ui@${old_major}.rb" ]; then
  echo "  keeping AI-UI ${old_major}.x as Formula/ai-ui@${old_major}.rb"
  awk -v class="AiUiAT${old_major}" '
    /^class AiUi < Formula/ { sub(/AiUi/, class) }
    /^  depends_on / && !keg_only { print "  keg_only :versioned_formula\n"; keg_only = 1 }
    { print }' "$formula" > "Formula/ai-ui@${old_major}.rb"
fi

for f in "$formula" "Formula/ai-ui@${major}.rb"; do
  [ -f "$f" ] || continue
  # The CLI moved from this tap into AI-UI's cli/ on 2026-09-30. Once a formula
  # installs from cli/, the last three edits find nothing to change.
  sed -i '' \
    -e "s|^  url \".*\"|  url \"$url\"|" \
    -e "s|^  sha256 \".*\"|  sha256 \"$sha\"|" \
    -e '/^  version "/d' -e '/^  revision /d' \
    -e 's|^    libexec.install "ai-ui"$|    libexec.install "cli/ai-ui", "cli/nuke-sage"|' \
    -e '/^    # `ai-ui nuke` runs the nuke-sage beside it, in scripts\/\.$/d' \
    -e '/^    (libexec\/"scripts").install "scripts\/nuke-sage"$/d' \
    "$f"
  echo "  $f -> AI-UI $version"
done
