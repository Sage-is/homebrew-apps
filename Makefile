# =============================================================================
# Sage-is/homebrew-apps — Homebrew Tap
# =============================================================================
# Git-flow release management for the ai-ui formula.
#
# Workflow (two commands):
#   make patch_release      — creates release branch, bumps version, commits
#   make release_finish     — merges, tags, pushes, updates sha256
#
# All release/hotfix targets auto-bump the formula URL + script VERSION
# and auto-update sha256 after tagging. No manual steps needed.
#
# Requires: git-flow-next (Go rewrite). Install: brew install git-flow-next
# =============================================================================

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
GIT_TAG     := $(shell git tag --sort=-v:refname | sed 's/^v//' | head -n 1)
IMAGE_TAG   := $(if $(GIT_TAG),$(GIT_TAG),0.0.0)
GIT_BRANCH  := $(shell git rev-parse --abbrev-ref HEAD)
FORMULA     := Formula/ai-ui.rb
FORMULA_URL := $(shell sed -n 's/.*url "\(.*\)"/\1/p' $(FORMULA))

# On a release/* branch, extract version from branch name; else use latest tag
RELEASE_VERSION := $(shell echo $(GIT_BRANCH) | sed -n 's/^release\///p')
ifeq ($(RELEASE_VERSION),)
  RELEASE_VERSION := $(GIT_TAG)
endif

# Major version number for versioned formula support (ai-ui@1, ai-ui@2, etc.)
MAJOR_VERSION := $(shell echo $(RELEASE_VERSION) | awk -F'.' '{print $$1}')

# Upstream repo URL (extracted from formula homepage)
UPSTREAM_REPO := $(shell sed -n 's/.*homepage "\(.*\)"/\1/p' $(FORMULA))

# ---------------------------------------------------------------------------
# Shared shell snippets (DRY helpers used by multiple targets)
# ---------------------------------------------------------------------------

# Source shell helpers for bump_version / create_versioned_formula.
# Usage in recipes: @. scripts/formula-helpers.sh && bump_version 1.0.0
HELPERS := . scripts/formula-helpers.sh

# Clear stale git-flow-next merge state from a prior interrupted finish.
# git-flow-next leaves .git/gitflow/state/merge.json even after failures.
# If there's no real merge in progress (.git/MERGE_HEAD), it's safe to remove.
define clear_stale_gitflow_state
	if [ -f .git/gitflow/state/merge.json ] && [ ! -f .git/MERGE_HEAD ]; then \
		echo "Clearing stale git-flow merge state from prior run..."; \
		rm -f .git/gitflow/state/merge.json; \
	fi
endef

# Wait for GitHub to make the archive available, then update sha256 in formula.
# Retries up to 5 times with increasing backoff (3s, 6s, 9s, 12s, 15s = 45s max).
define wait_and_update_sha256
	for i in 1 2 3 4 5; do \
		sleep $$(( i * 3 )); \
		if curl -fsSL -o /dev/null "$$(sed -n 's/.*url "\(.*\)"/\1/p' $(FORMULA))" 2>/dev/null; then \
			echo "Downloading: $$(sed -n 's/.*url "\(.*\)"/\1/p' $(FORMULA))"; \
			HASH=$$(curl -fsSL "$$(sed -n 's/.*url "\(.*\)"/\1/p' $(FORMULA))" | shasum -a 256 | awk '{print $$1}'); \
			echo "sha256: $$HASH"; \
			sed -i '' "s/sha256 \".*\"/sha256 \"$$HASH\"/" $(FORMULA); \
			echo "Updated $(FORMULA)"; \
			MAIN_URL=$$(sed -n 's/.*url "\(.*\)"/\1/p' $(FORMULA)); \
			for vf in Formula/ai-ui@*.rb; do \
				if [ -f "$$vf" ]; then \
					VF_URL=$$(sed -n 's/.*url "\(.*\)"/\1/p' "$$vf"); \
					if [ "$$VF_URL" = "$$MAIN_URL" ]; then \
						sed -i '' "s/sha256 \".*\"/sha256 \"$$HASH\"/" "$$vf"; \
						echo "Updated $$vf"; \
					fi; \
				fi; \
			done; \
			break; \
		fi; \
		echo "  Attempt $$i/5: archive not ready, retrying..."; \
		if [ $$i -eq 5 ]; then \
			echo "WARNING: Archive not available after 45s. Run 'make sha256' manually."; \
			exit 1; \
		fi; \
	done
endef

# Commit sha256 on master, then sync to develop.
# Must be called while on master.
define commit_sha256_and_sync
	NEW_TAG=$$(git tag --sort=-v:refname | head -1); \
	git add $(FORMULA) $$(ls Formula/ai-ui@*.rb 2>/dev/null) && \
	git commit -m "Update sha256 for $$NEW_TAG" && \
	git push origin master && \
	git checkout develop && \
	git merge --no-edit master && \
	git push origin develop
endef

# ---------------------------------------------------------------------------
# Info targets
# ---------------------------------------------------------------------------
help:
	@echo "========================================="
	@echo "  homebrew-apps — Sage-is Homebrew Tap"
	@echo "========================================="
	@echo ""
	@echo "  Current version: $(IMAGE_TAG)"
	@echo "  Branch:          $(GIT_BRANCH)"
	@echo ""
	@echo "Quick start:"
	@echo "  make release          → interactive full release flow"
	@echo ""
	@echo "Targets:"
	@echo "  release             Interactive full release flow"
	@echo "  sha256              Compute sha256 for current formula URL"
	@echo "  show-version        Show current version info"
	@echo "  check-upstream      Compare local version against upstream AI-UI repo"
	@echo "  minor_release       Start minor version bump (0.X.0)"
	@echo "  patch_release       Start patch version bump (0.0.X)"
	@echo "  major_release       Start major version bump (X.0.0)"
	@echo "  hotfix              Start hotfix (0.0.0.X)"
	@echo "  custom_release      Start release with explicit version (VER=X.Y.Z)"
	@echo "  feature_finish      Finish feature: merge into develop, push"
	@echo "  release_finish      Finish release: merge, tag, push, sha256"
	@echo "  hotfix_finish       Finish hotfix: merge, tag, push, sha256"
	@echo "  test                Run brew audit and test on formula"
	@echo ""

show-version:
	@echo "Tag:     $(IMAGE_TAG)"
	@echo "Branch:  $(GIT_BRANCH)"
	@echo "Release: $(RELEASE_VERSION)"
	@echo "URL:     $(FORMULA_URL)"

check-upstream:
	@echo "Upstream: $(UPSTREAM_REPO)"
	@echo "Local:    v$(IMAGE_TAG)"
	@echo ""
	@echo "Upstream tags:"
	@git ls-remote --tags $(UPSTREAM_REPO) 2>/dev/null \
		| sed -n 's|.*refs/tags/\(v[0-9][^{}]*\)$$|\1|p' \
		| sort -V \
		| while read tag; do \
			echo "  $$tag"; \
		done
	@echo ""
	@LATEST=$$(git ls-remote --tags $(UPSTREAM_REPO) 2>/dev/null \
		| sed -n 's|.*refs/tags/\(v[0-9][^{}]*\)$$|\1|p' \
		| sort -V | tail -1); \
	if [ "$$LATEST" = "v$(IMAGE_TAG)" ]; then \
		echo "Up to date."; \
	elif [ -n "$$LATEST" ]; then \
		echo "Behind: local v$(IMAGE_TAG) < upstream $$LATEST"; \
	else \
		echo "Could not fetch upstream tags."; \
	fi

# ---------------------------------------------------------------------------
# SHA256 (standalone — used when you need to update manually)
# ---------------------------------------------------------------------------
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
# Testing
# ---------------------------------------------------------------------------
test:
	brew audit --formula $(FORMULA)
	brew test ai-ui

# ---------------------------------------------------------------------------
# Interactive release (full flow)
# ---------------------------------------------------------------------------
release:
	@scripts/release.sh

# ---------------------------------------------------------------------------
# Release start targets
# ---------------------------------------------------------------------------
# Each target: calculates next version → creates branch → bumps version → commits.
# After this, you only need `make release_finish`.

minor_release: require_gitflow_next
	@$(HELPERS) && \
	NEW_VER=$$(git tag --sort=-v:refname | sed 's/^v//' | head -n 1 | awk -F'.' '{print $$1"."$$2+1".0"}') && \
	git flow release start $$NEW_VER && \
	echo "Bumping formula URL and VERSION to v$$NEW_VER..." && \
	bump_version $$NEW_VER && \
	git add -A && \
	git commit -m "Bump version to $$NEW_VER" && \
	echo "" && \
	echo "=== Release $$NEW_VER ready ===" && \
	echo "Next: make release_finish"

patch_release: require_gitflow_next
	@$(HELPERS) && \
	NEW_VER=$$(git tag --sort=-v:refname | sed 's/^v//' | head -n 1 | awk -F'.' '{print $$1"."$$2"."$$3+1}') && \
	git flow release start $$NEW_VER && \
	echo "Bumping formula URL and VERSION to v$$NEW_VER..." && \
	bump_version $$NEW_VER && \
	git add -A && \
	git commit -m "Bump version to $$NEW_VER" && \
	echo "" && \
	echo "=== Release $$NEW_VER ready ===" && \
	echo "Next: make release_finish"

major_release: require_gitflow_next
	@$(HELPERS) && \
	NEW_VER=$$(git tag --sort=-v:refname | sed 's/^v//' | head -n 1 | awk -F'.' '{print $$1+1".0.0"}') && \
	MAJOR=$$(echo $$NEW_VER | awk -F'.' '{print $$1}') && \
	git flow release start $$NEW_VER && \
	echo "Bumping formula URL and VERSION to v$$NEW_VER..." && \
	bump_version $$NEW_VER && \
	create_versioned_formula $$MAJOR && \
	git add -A && \
	git commit -m "Bump version to $$NEW_VER (create ai-ui@$$MAJOR)" && \
	echo "" && \
	echo "=== Release $$NEW_VER ready ===" && \
	echo "Next: make release_finish"

hotfix: require_gitflow_next
	@$(HELPERS) && \
	NEW_VER=$$(git tag --sort=-v:refname | sed 's/^v//' | head -n 1 | awk -F'.' '{if (NF < 4) print $$1"."$$2"."$$3".1"; else print $$1"."$$2"."$$3"."$$4+1}') && \
	git flow hotfix start $$NEW_VER && \
	echo "Bumping formula URL and VERSION to v$$NEW_VER..." && \
	bump_version $$NEW_VER && \
	git add -A && \
	git commit -m "Bump version to $$NEW_VER" && \
	echo "" && \
	echo "=== Hotfix $$NEW_VER ready ===" && \
	echo "Next: fix the issue, commit, then make hotfix_finish"

# ---------------------------------------------------------------------------
# Custom release (arbitrary version jump, e.g. make custom_release VER=2.1.0)
# ---------------------------------------------------------------------------
custom_release: require_gitflow_next
	@if [ -z "$(VER)" ]; then echo "Usage: make custom_release VER=2.1.0"; exit 1; fi
	@$(HELPERS) && \
	MAJOR=$$(echo $(VER) | awk -F'.' '{print $$1}') && \
	git flow release start $(VER) && \
	echo "Bumping formula URL and VERSION to v$(VER)..." && \
	bump_version $(VER) && \
	create_versioned_formula $$MAJOR && \
	git add -A && \
	git commit -m "Bump version to $(VER)" && \
	echo "" && \
	echo "=== Release $(VER) ready ===" && \
	echo "Next: make release_finish"

# ---------------------------------------------------------------------------
# Release finish
# ---------------------------------------------------------------------------
# 1. Clears stale git-flow state (from prior interrupted finish)
# 2. Runs git-flow finish with --no-fetch (release branches are local-only)
# 3. Falls back to manual merge/tag if git-flow fails
# 4. Pushes all branches and tags
# 5. Waits for GitHub archive, computes sha256
# 6. Commits sha256 on master, merges to develop

release_finish: require_gitflow_next
	@echo "=== Finishing release $(RELEASE_VERSION) ==="
	@# Step 1: Clear stale git-flow state if no real merge is in progress
	@$(clear_stale_gitflow_state)
	@# Step 2: Try git-flow finish (--no-fetch: release branches are never pushed)
	@# Step 3: If git-flow fails, do the merge/tag/cleanup manually
	@git flow release finish --no-fetch || ( \
		echo ""; \
		echo "git-flow finish failed — completing release/$(RELEASE_VERSION) manually..."; \
		rm -f .git/gitflow/state/merge.json; \
		git checkout master && \
		git merge --no-ff --no-edit release/$(RELEASE_VERSION) && \
		(git tag -a "v$(RELEASE_VERSION)" -m "Release v$(RELEASE_VERSION)" 2>/dev/null || echo "  Tag v$(RELEASE_VERSION) already exists") && \
		git checkout develop && \
		git merge --no-ff --no-edit master && \
		git branch -d release/$(RELEASE_VERSION) \
	)
	@# Step 4: Push everything
	@git push origin develop && git push origin master && git push --tags
	@echo ""
	@echo "=== Updating sha256 (waiting for GitHub archive) ==="
	@# Step 5: Must be on master for the sha256 commit
	@git checkout master
	@$(wait_and_update_sha256)
	@# Step 6: Commit sha256 on master, sync to develop
	@$(commit_sha256_and_sync)
	@echo ""
	@echo "=== Release $(RELEASE_VERSION) complete ==="

# ---------------------------------------------------------------------------
# Feature finish
# ---------------------------------------------------------------------------
# Detects current feature/ branch and merges it into develop.

feature_finish: require_gitflow_next
	@FEATURE=$$(echo $(GIT_BRANCH) | sed -n 's/^feature\///p'); \
	if [ -z "$$FEATURE" ]; then \
		echo "Error: not on a feature/ branch (current: $(GIT_BRANCH))"; \
		exit 1; \
	fi; \
	echo "=== Finishing feature $$FEATURE ===" && \
	FETCH_FLAG=""; \
	if ! git ls-remote --exit-code --heads origin feature/$$FEATURE >/dev/null 2>&1; then \
		echo "No remote branch found — skipping fetch"; \
		FETCH_FLAG="--no-fetch"; \
	fi; \
	git flow feature finish $$FETCH_FLAG $$FEATURE && \
	git push origin develop && \
	echo "" && \
	echo "=== Feature $$FEATURE merged into develop ==="

# ---------------------------------------------------------------------------
# Hotfix finish
# ---------------------------------------------------------------------------
# Same flow as release_finish but for hotfix branches.

hotfix_finish: require_gitflow_next
	@echo "=== Finishing hotfix ==="
	@$(clear_stale_gitflow_state)
	@git flow hotfix finish --no-fetch || ( \
		echo ""; \
		echo "git-flow hotfix finish failed — completing manually..."; \
		rm -f .git/gitflow/state/merge.json; \
		HOTFIX_VER=$$(git rev-parse --abbrev-ref HEAD | sed 's/^hotfix\///'); \
		git checkout master && \
		git merge --no-ff --no-edit hotfix/$$HOTFIX_VER && \
		(git tag -a "v$$HOTFIX_VER" -m "Hotfix v$$HOTFIX_VER" 2>/dev/null || echo "  Tag v$$HOTFIX_VER already exists") && \
		git checkout develop && \
		git merge --no-ff --no-edit master && \
		git branch -d hotfix/$$HOTFIX_VER \
	)
	@git push origin develop && git push origin master && git push --tags
	@echo ""
	@echo "=== Updating sha256 (waiting for GitHub archive) ==="
	@git checkout master
	@$(wait_and_update_sha256)
	@$(commit_sha256_and_sync)
	@echo ""
	@echo "=== Hotfix complete ==="

# ---------------------------------------------------------------------------
# Formula URL bump (standalone — normally called automatically by start targets)
# ---------------------------------------------------------------------------
bump_formula_url:
	@if [ -z "$(RELEASE_VERSION)" ]; then \
		echo "Error: RELEASE_VERSION not set. Are you on a release/ branch?"; \
		exit 1; \
	fi
	@$(HELPERS) && \
	echo "Updating formula URL and VERSION to v$(RELEASE_VERSION)..." && \
	bump_version $(RELEASE_VERSION) && \
	echo "Done. sha256 will be updated automatically by release_finish / hotfix_finish."

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
require_gitflow_next:
	@if ! git flow version 2>/dev/null | grep -q 'git-flow-next'; then \
		echo "Error: git-flow-next required (Go rewrite). Install: brew install git-flow-next"; \
		exit 1; \
	fi

.PHONY: help show-version check-upstream sha256 test release \
	minor_release patch_release major_release hotfix custom_release \
	feature_finish release_finish hotfix_finish \
	bump_formula_url require_gitflow_next
