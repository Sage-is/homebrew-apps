#!/bin/bash
# formula-helpers.sh — Shell functions for versioned formula management.
# Sourced by Makefile targets: . scripts/formula-helpers.sh

FORMULA="Formula/ai-ui.rb"

# The next CLI release under AI-UI version $1: the version itself until its tag
# exists, then its next formula revision (3.2.0_1, 3.2.0_2, ...).
# Usage: next_release 3.2.0
next_release() {
	local ver="$1" rev=0 tag="v$1"
	while git rev-parse -q --verify "refs/tags/$tag" >/dev/null; do
		rev=$((rev + 1))
		tag="v${ver}_${rev}"
	done
	echo "${tag#v}"
}

# Point the ai-ui formula, its versioned twin and the script at release $1: an
# AI-UI version (3.2.0), or a revision of one (3.2.0_1) that ships a CLI-only fix.
# Homebrew reads a v3.2.0_1 tag as version 0.1, so a revision states its version
# and revision number outright.
# Usage: bump_version 3.2.0 | bump_version 3.2.0_1
bump_version() {
	local VER="$1" BASE="${1%%_*}" REV="" f
	case "$VER" in *_*) REV="${VER##*_}" ;; esac
	for f in "$FORMULA" "Formula/ai-ui@${BASE%%.*}.rb"; do
		[ -f "$f" ] || continue
		sed -i '' -e "s|/archive/refs/tags/v[^\"]*\.tar\.gz|/archive/refs/tags/v${VER}.tar.gz|" \
			-e '/^  version "/d' -e '/^  revision /d' "$f"
		if [ -n "$REV" ]; then
			sed -i '' -e "/^  url \"/a\\
  version \"${BASE}\"
" -e "/^  license \"/a\\
  revision ${REV}
" "$f"
		fi
		echo "  Updated $f"
	done
	sed -i '' "s/^VERSION=\"[^\"]*\"/VERSION=\"${VER}\"/" ai-ui
	# This repo owns CLI_VERSION in distribution.env; publish the change to the siblings.
	sed -i '' "s/^CLI_VERSION=.*/CLI_VERSION=${BASE}/" distribution.env
	make -s distribution_sync
}

# Create a versioned formula (ai-ui@N.rb) from the main formula.
# Usage: create_versioned_formula 2
create_versioned_formula() {
	local MAJOR="$1"
	local VFORMULA="Formula/ai-ui@${MAJOR}.rb"
	if [ -f "$VFORMULA" ]; then
		echo "Versioned formula $VFORMULA already exists — skipping creation."
	else
		echo "Creating versioned formula $VFORMULA..."
		cp "$FORMULA" "$VFORMULA"
		sed -i '' "s/^class AiUi < Formula/class AiUiAT${MAJOR} < Formula/" "$VFORMULA"
		sed -i '' '/^  license/a\
  keg_only :versioned_formula' "$VFORMULA"
		echo "  Created $VFORMULA"
	fi
}
