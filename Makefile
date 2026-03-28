# =============================================================================
# Sage-is/homebrew-apps — Homebrew Tap
# =============================================================================
# Git-flow release management for the ai-ui formula.
#
#   make help             — list targets
#   make sha256           — compute sha256 for current formula URL
#   make minor_release    — start a minor version bump
#   make release_finish   — finish release, tag, push
# =============================================================================

GIT_TAG := $(shell git tag --sort=-v:refname | sed 's/^v//' | head -n 1)
IMAGE_TAG := $(if $(GIT_TAG),$(GIT_TAG),0.0.0)
GIT_BRANCH := $(shell git rev-parse --abbrev-ref HEAD)

RELEASE_VERSION := $(shell git rev-parse --abbrev-ref HEAD | sed -n 's/^release\///p')
ifeq ($(RELEASE_VERSION),)
	RELEASE_VERSION := $(GIT_TAG)
endif

FORMULA := Formula/ai-ui.rb
FORMULA_URL := $(shell sed -n 's/.*url "\(.*\)"/\1/p' $(FORMULA))

help:
	@echo "====================================="
	@echo "  homebrew-apps (Sage-is tap)"
	@echo "====================================="
	@echo ""
	@echo "  Current version: $(IMAGE_TAG)"
	@echo "  Branch:          $(GIT_BRANCH)"
	@echo ""
	@echo "Targets:"
	@echo "  sha256              Compute sha256 for current formula URL"
	@echo "  show-version        Show current version info"
	@echo "  minor_release       Start minor version bump"
	@echo "  patch_release       Start patch version bump"
	@echo "  major_release       Start major version bump"
	@echo "  hotfix              Start hotfix"
	@echo "  release_finish      Finish release: merge, tag, push"
	@echo "  hotfix_finish       Finish hotfix: merge, tag, push"
	@echo "  bump_formula_url    Update formula URL and script VERSION"
	@echo "  test                Run brew audit and test on formula"
	@echo ""

show-version:
	@echo "Tag:     $(IMAGE_TAG)"
	@echo "Branch:  $(GIT_BRANCH)"
	@echo "Release: $(RELEASE_VERSION)"
	@echo "URL:     $(FORMULA_URL)"

# ---------------------------------------------------------------------------
# SHA256
# ---------------------------------------------------------------------------
# Downloads the archive from the formula URL and prints the sha256.
# Run after tagging and pushing a release.
sha256:
	@if [ -z "$(FORMULA_URL)" ]; then \
		echo "Error: Could not extract URL from $(FORMULA)"; \
		exit 1; \
	fi
	@echo "Downloading: $(FORMULA_URL)"
	$(eval HASH := $(shell curl -fsSL "$(FORMULA_URL)" | shasum -a 256 | awk '{print $$1}'))
	@echo "sha256: $(HASH)"
	@sed -i '' 's/sha256 ".*"/sha256 "$(HASH)"/' $(FORMULA)
	@echo "Updated $(FORMULA)"

# ---------------------------------------------------------------------------
# Formula Management
# ---------------------------------------------------------------------------
# Updates the formula URL tag to match RELEASE_VERSION
bump_formula_url:
	@if [ -z "$(RELEASE_VERSION)" ]; then \
		echo "Error: RELEASE_VERSION not defined. Are you on a release/ branch?"; \
		exit 1; \
	fi
	@echo "Updating formula URL to v$(RELEASE_VERSION)..."
	@sed -i '' 's|/archive/refs/tags/v[^"]*\.tar\.gz|/archive/refs/tags/v'"$(RELEASE_VERSION)"'.tar.gz|' $(FORMULA)
	@echo "Updating ai-ui VERSION to $(RELEASE_VERSION)..."
	@sed -i '' 's/^VERSION="[^"]*"/VERSION="$(RELEASE_VERSION)"/' ai-ui
	@echo "Done. Formula URL and script VERSION updated to v$(RELEASE_VERSION)."
	@echo "Remember to update sha256 after tagging."

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
test:
	brew audit --formula $(FORMULA)
	brew test ai-ui

# ---------------------------------------------------------------------------
# Version Management with Git Flow
# ---------------------------------------------------------------------------
# Requires git-flow-next (Go rewrite). Install: brew install git-flow-next
# All version tags start with 'v' (e.g., v0.1.0) following semantic versioning.

require_gitflow_next:
	@if ! git flow version 2>/dev/null | grep -q 'git-flow-next'; then \
		echo "Error: git-flow-next required (Go rewrite). Install: brew install git-flow-next"; \
		exit 1; \
	fi

minor_release: require_gitflow_next
	git flow release start $$(git tag --sort=-v:refname | sed 's/^v//' | head -n 1 | awk -F'.' '{print $$1"."$$2+1".0"}')
	@echo ""
	@echo "=== Release branch created ==="
	@echo "Next steps:"
	@echo "  1. make bump_formula_url         # Update formula URL to new version"
	@echo "  2. Edit formula if needed"
	@echo "  3. git add -A && git commit"
	@echo "  4. make release_finish"

patch_release: require_gitflow_next
	git flow release start $$(git tag --sort=-v:refname | sed 's/^v//' | head -n 1 | awk -F'.' '{print $$1"."$$2"."$$3+1}')
	@echo ""
	@echo "=== Release branch created ==="
	@echo "Next steps:"
	@echo "  1. make bump_formula_url         # Update formula URL to new version"
	@echo "  2. Edit formula if needed"
	@echo "  3. git add -A && git commit"
	@echo "  4. make release_finish"

major_release: require_gitflow_next
	git flow release start $$(git tag --sort=-v:refname | sed 's/^v//' | head -n 1 | awk -F'.' '{print $$1+1".0.0"}')
	@echo ""
	@echo "=== Release branch created ==="
	@echo "Next steps:"
	@echo "  1. make bump_formula_url         # Update formula URL to new version"
	@echo "  2. Edit formula if needed"
	@echo "  3. git add -A && git commit"
	@echo "  4. make release_finish"

hotfix: require_gitflow_next
	git flow hotfix start $$(git tag --sort=-v:refname | sed 's/^v//' | head -n 1 | awk -F'.' '{if (NF < 4) print $$1"."$$2"."$$3".1"; else print $$1"."$$2"."$$3"."$$4+1}')
	@echo ""
	@echo "=== Hotfix branch created ==="
	@echo "Next steps:"
	@echo "  1. Fix the issue"
	@echo "  2. git add -A && git commit"
	@echo "  3. make hotfix_finish"

release_finish: require_gitflow_next
	@echo "=== Finishing release ==="
	git flow release finish && git push origin develop && git push origin master && git push --tags
	@echo ""
	@echo "=== Updating sha256 (waiting for GitHub archive) ==="
	@for i in 1 2 3 4 5; do \
		sleep $$(( i * 3 )); \
		if curl -fsSL -o /dev/null "$$(sed -n 's/.*url "\(.*\)"/\1/p' $(FORMULA))" 2>/dev/null; then \
			make sha256; \
			break; \
		fi; \
		echo "  Attempt $$i/5: archive not ready, retrying..."; \
		if [ $$i -eq 5 ]; then \
			echo "WARNING: Archive not available after 45s. Run 'make sha256' manually."; \
			exit 1; \
		fi; \
	done
	@NEW_TAG=$$(git tag --sort=-v:refname | head -1); \
	git add $(FORMULA) && git commit -m "Update sha256 for $$NEW_TAG" && \
	git push origin master && \
	git checkout develop && git merge master && git push origin develop || \
	(echo "ERROR: Post-sha256 steps failed. Current branch: $$(git branch --show-current)"; \
	 echo "  If on master: git checkout develop && git merge master && git push origin develop"; \
	 exit 1)
	@echo ""
	@echo "=== Release complete ==="

hotfix_finish: require_gitflow_next
	@echo "=== Finishing hotfix ==="
	git flow hotfix finish && git push origin develop && git push origin master && git push --tags
	@echo ""
	@echo "=== Updating sha256 (waiting for GitHub archive) ==="
	@for i in 1 2 3 4 5; do \
		sleep $$(( i * 3 )); \
		if curl -fsSL -o /dev/null "$$(sed -n 's/.*url "\(.*\)"/\1/p' $(FORMULA))" 2>/dev/null; then \
			make sha256; \
			break; \
		fi; \
		echo "  Attempt $$i/5: archive not ready, retrying..."; \
		if [ $$i -eq 5 ]; then \
			echo "WARNING: Archive not available after 45s. Run 'make sha256' manually."; \
			exit 1; \
		fi; \
	done
	@NEW_TAG=$$(git tag --sort=-v:refname | head -1); \
	git add $(FORMULA) && git commit -m "Update sha256 for $$NEW_TAG" && \
	git push origin master && \
	git checkout develop && git merge master && git push origin develop || \
	(echo "ERROR: Post-sha256 steps failed. Current branch: $$(git branch --show-current)"; \
	 echo "  If on master: git checkout develop && git merge master && git push origin develop"; \
	 exit 1)
	@echo ""
	@echo "=== Hotfix complete ==="

.PHONY: help show-version sha256 bump_formula_url test \
	require_gitflow_next minor_release patch_release major_release hotfix \
	release_finish hotfix_finish
