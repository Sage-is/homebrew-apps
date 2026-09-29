#!/usr/bin/env bash
# Check that new formula and cask names are free, before they ship.
#
# Fails when official Homebrew already has a formula or cask of that name
# (`brew install NAME` would be ambiguous). Warns when npm has a package of
# that name, or this Mac already runs a command of that name: CapRover's npm
# CLI, for one, installs `caprover-deploy`, which would shadow or be shadowed.
set -euo pipefail

found() { [ "$(curl -s -o /dev/null -w '%{http_code}' "$1")" = 200 ]; }

status=0
for name in "$@"; do
  if found "https://formulae.brew.sh/api/formula/$name.json"; then
    echo "  $name: FAIL: official Homebrew already has a formula named $name"; status=1; continue
  fi
  if found "https://formulae.brew.sh/api/cask/$name.json"; then
    echo "  $name: FAIL: official Homebrew already has a cask named $name"; status=1; continue
  fi
  found "https://registry.npmjs.org/$name" && echo "  $name: warning: npm has a package named $name"
  command -v "$name" >/dev/null && echo "  $name: warning: this Mac already runs $(command -v "$name")"
  echo "  $name: free in Homebrew"
done
exit $status
