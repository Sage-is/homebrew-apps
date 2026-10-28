#!/usr/bin/env bash
# Release one formula in this tap: `make tool_release TOOL=offload VERSION=0.7.0 [APPLY=1]`.
#
# The release tag is the formula's own url with the new version in it: a tool
# kept in this tap is tagged `<tool>-v<VERSION>` here; a formula for another
# repo (work-delegation) waits for that repo to tag its own release.
#
# --dry-run commits, tags and pushes nothing: it checks the tool, prints the
# steps, and pins the formula once its tag exists. --apply runs the steps too,
# then installs and tests the formula from the tap.
set -euo pipefail

# The version a tarball url names: v<X.Y.Z>.tar.gz (brew scans that one) or
# <tool>-v<X.Y.Z>.tar.gz (a tool kept in this tap). Nothing else counts. Prints nothing otherwise.
version_of_url() {  # URL TOOL
  local name
  name=$(basename "$1" .tar.gz)
  name=${name#"$2"-}  # a tool's own tag carries its name
  printf '%s\n' "$name" | sed -n 's/^v\([0-9][0-9.]*\)$/\1/p'
}
# The image a release pins must already be in the registry. trellis-crm pins
# the Trellis image of its own version (the formula's inreplace), so a
# trellis-crm whose image is missing breaks every box that updates: it did on
# 2026-10-28. Prints why and fails; any other tool passes.
image_pushed() {  # TOOL VERSION
  [ "$1" = trellis-crm ] || return 0
  gh api "/orgs/sage-is/packages/container/trellis/versions" --paginate \
    -q '.[].metadata.container.tags[]' 2>/dev/null | grep -qxF "$2" && return 0
  echo "ghcr.io/sage-is/trellis:$2 is not in the registry: ship Trellis $2 first (its make ship pushes the image)" >&2
  return 1
}
[ "${BASH_SOURCE[0]}" = "$0" ] || return 0  # sourced by the tests: the functions are all they need

BRANCH=develop  # the tap's default branch: the one `brew update` reads

usage="usage: tool-release.sh --dry-run|--apply TOOL VERSION"
case "${1:-}" in
  --apply)   apply=1 ;;
  --dry-run) apply=0 ;;
  *)         echo "$usage" >&2; exit 2 ;;
esac
tool=${2:?$usage}
version=${3:?$usage}
formula="Formula/$tool.rb"

die() { echo "$tool: $*" >&2; exit 1; }
step() {  # show a command; with --apply, run it too
  printf '  %s\n' "$(printf '%q ' "$@")"  # quoted, so a copied line runs as shown
  if [ "$apply" = 1 ]; then "$@"; fi
}
field() { sed -n "s/^  $1 \"\(.*\)\"$/\1/p" "$formula" | head -1; }
wait_for_archive() {  # GitHub serves a new tag's archive within seconds
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if curl -fsIL "$url" >/dev/null; then return 0; fi
    sleep 5
  done
  die "$url is still missing after a minute; run this again"
}

[ -f "$formula" ] || die "no $formula"
case "$tool" in ai-ui*) die "ai-ui ships inside AI-UI's releases: run \`make ai_ui_formula\` after one" ;; esac
image_pushed "$tool" "$version" || exit 1

url=$(field url)
pinned_version=$(field version)
[ -n "$pinned_version" ] || pinned_version=$(version_of_url "$url" "$tool")
[ -n "$pinned_version" ] || die "$formula needs a version line, or a v<X.Y.Z> or $tool-v<X.Y.Z> tarball url"
url=${url//$pinned_version/$version}
tag=$(basename "$url" .tar.gz)

if [ -x "./$tool" ]; then  # kept in this tap: tagged here
  remote=origin
  "./$tool" version | grep -qE "(^|[^0-9.])v?${version//./\\.}([^0-9.]|$)" \
    || die "\`./$tool version\` does not say $version; bump the tool first"
else                       # another repo's tool: that repo tags it
  remote=${url%%/archive/*}
fi
brew style "$formula"
if [ "$(git branch --show-current)" != "$BRANCH" ]; then
  [ "$apply" = 0 ] || die "run this on $BRANCH, the branch brew reads"
  echo "$tool: switch to $BRANCH, the branch brew reads, before APPLY=1"
fi

if [ -z "$(git ls-remote --tags "$remote" "refs/tags/$tag")" ]; then
  if [ "$remote" != origin ]; then
    echo "$tool $version: waiting for $remote to tag $tag"
    exit 0
  fi
  echo "$tool $version: tag HEAD and push it"
  # Every file the formula installs rides the tag too: sage-runtime and
  # trellis-crm install the shared lib/.
  installs=$(sed -n 's/.*\.install \(.*\)$/\1/p' "$formula" | grep -o '"[^"]*"' | tr -d '"')
  if [ -n "$(git status --porcelain -- "$tool" "$formula" $installs)" ]; then
    [ "$apply" = 0 ] || die "commit $tool, $formula and the files it installs first: the tag takes HEAD"
    echo "  first: commit $tool, $formula and the files it installs (the tag takes HEAD)"
  fi
  git rev-parse -q --verify "refs/tags/$tag" >/dev/null || step git tag -a "$tag" -m "$tool v$version"
  step git push origin "$BRANCH" "$tag"
  if [ "$apply" = 0 ]; then
    echo "  (dry run: APPLY=1 does this, then pins, installs and tests)"
    exit 0
  fi
  wait_for_archive
fi

sha=$(curl -fsSL "$url" | shasum -a 256 | cut -d' ' -f1)
# Only the formula's own url, version and sha256 (two-space indent): resources keep theirs.
sed -i '' -E \
  -e "s|^  url \".*\"|  url \"$url\"|" \
  -e "s|^  version \".*\"|  version \"$version\"|" \
  -e "s|^  sha256 \".*\"|  sha256 \"$sha\"|" \
  "$formula"
brew style "$formula"

echo "$tool $version: $formula pinned to $tag"
if git diff --quiet -- "$formula"; then
  echo "  already pinned"
else
  step git commit -m "chore($tool): pin v$version sha256" -- "$formula"
  step git push origin "$BRANCH"
fi

echo "$tool $version: install and test from the tap"
step brew update
if brew list --formula "$tool" >/dev/null 2>&1; then
  step brew upgrade "sage-is/apps/$tool"
else
  step brew install "sage-is/apps/$tool"
fi
step brew test "$tool"
step brew audit --strict "sage-is/apps/$tool" || echo "  $tool: the audit found problems (above); the install itself works"
