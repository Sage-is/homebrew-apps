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
case "$tool" in ai-ui*) die "ai-ui releases with \`make release\`, which also moves distribution.env" ;; esac

pinned_version=$(field version)
[ -n "$pinned_version" ] || die "$formula needs a version line"
url=$(field url)
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
  if [ -n "$(git status --porcelain -- "$tool" "$formula")" ]; then
    [ "$apply" = 0 ] || die "commit $tool and $formula first: the tag takes HEAD"
    echo "  first: commit $tool and $formula (the tag takes HEAD)"
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
