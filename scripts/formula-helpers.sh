#!/bin/bash
# formula-helpers.sh — Shell functions for versioned formula management.
# Sourced by Makefile targets: . scripts/formula-helpers.sh

FORMULA="Formula/ai-ui.rb"

# Bump formula URL and ai-ui VERSION to a given version.
# Also updates versioned formula if one exists for that major version.
# Usage: bump_version 1.0.0
bump_version() {
	local VER="$1"
	sed -i '' "s|/archive/refs/tags/v[^\"]*\.tar\.gz|/archive/refs/tags/v${VER}.tar.gz|" "$FORMULA"
	sed -i '' "s/^VERSION=\"[^\"]*\"/VERSION=\"${VER}\"/" ai-ui

	local MAJOR=$(echo "$VER" | awk -F'.' '{print $1}')
	local VFORMULA="Formula/ai-ui@${MAJOR}.rb"
	if [ -f "$VFORMULA" ]; then
		sed -i '' "s|/archive/refs/tags/v[^\"]*\.tar\.gz|/archive/refs/tags/v${VER}.tar.gz|" "$VFORMULA"
		echo "  Updated versioned formula $VFORMULA"
	fi
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
