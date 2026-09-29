#!/usr/bin/env bash
# Check every formula against what it downloads, before a push lets brew see it.
#
# - The sha256 matches the tarball at its url.
# - From 3.x, an ai-ui formula's version equals the SERVER_TAG in that
#   tarball's distribution.env: the version brew shows is the AI-UI it pins.
#
# A formula with a placeholder sha256 is not released yet: listed, not failed.
set -euo pipefail

field() { sed -n "s/^  $2 \"\(.*\)\"$/\1/p" "$1" | head -1; }

status=0
for formula in Formula/*.rb; do
  name=$(basename "$formula" .rb)
  url=$(field "$formula" url)
  sha=$(field "$formula" sha256)
  version=$(field "$formula" version)
  [ -n "$version" ] || version=$(basename "$url" .tar.gz | sed 's/^v//')  # ai-ui: from its v<X.Y.Z> tag

  if [[ $sha =~ ^0{64}$ ]]; then
    printf '  %-16s not released yet\n' "$name"
    continue
  fi

  tarball=$(mktemp)
  curl -fsSL "$url" -o "$tarball"
  actual=$(shasum -a 256 "$tarball" | cut -d' ' -f1)
  problem=""
  if [ "$actual" != "$sha" ]; then
    problem="sha256 is $sha, the tarball's is $actual"
  elif [[ $name == ai-ui* ]] && [ "${version%%.*}" -ge 3 ]; then
    pinned=$(tar -xzOf "$tarball" '*/distribution.env' | sed -n 's/^SERVER_TAG=//p')
    [ "$pinned" = "$version" ] || problem="version $version, but its distribution.env pins AI-UI $pinned"
  fi
  rm -f "$tarball"

  if [ -n "$problem" ]; then
    printf '  %-16s FAIL: %s\n' "$name" "$problem"
    status=1
  else
    printf '  %-16s ok (%s)\n' "$name" "$version"
  fi
done
exit $status
