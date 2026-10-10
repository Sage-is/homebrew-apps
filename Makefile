# =============================================================================
# Sage-is/homebrew-apps — Homebrew Tap
# =============================================================================
# The ai-ui CLI ships inside each AI-UI release (its cli/ folder). After an AI-UI
# release, `make ai_ui_formula` points the formula at it. The other tools release
# with `make tool_release` and `make release_tools`.
# =============================================================================

# Pull canonical distribution facts from the shared contract; this repo's copy is published to WEB-AI--Sage-is-AI-UI and WEB-Sage.Education-docs by `make distribution_sync`. If need be, you can use `-include` to keep a clone without it parseable.
-include distribution.env

GIT_BRANCH  := $(shell git rev-parse --abbrev-ref HEAD)
FORMULA     := Formula/ai-ui.rb

help:
	@echo "homebrew-apps — the Sage-is Homebrew tap"
	@echo ""
	@echo "  ai_ui_formula       Point the ai-ui formula at AI-UI $(SERVER_TAG), whose cli/ is the CLI"
	@echo "  tool_release        Release one formula (TOOL=x VERSION=y; dry run unless APPLY=1)"
	@echo "  release_tools       Release every formula that is behind (dry run unless APPLY=1)"
	@echo "  rename_projects     Move renamed projects: repo, remote, folder, memory (APPLY=1)"
	@echo "  check               Before a push: style, tests, formula tarballs, copies, new names"
	@echo "  check_names         Are these names free in Homebrew? NAMES=\"a b\""
	@echo "  install_hooks       Fix formula style on commit; run make check before every push"
	@echo "  test                brew style, audit and test, plus the tools' unit tests"
	@echo "  feature_finish      Finish a feature: merge into develop, push"
	@echo "  distribution_sync   Publish distribution.env to the sibling repos"
	@echo "  distribution_verify Refuse while a copy differs or the pinned image is missing"
	@echo ""

tool_release:  ## Release one formula: TOOL=offload VERSION=0.7.0 (dry run unless APPLY=1)
	@scripts/tool-release.sh $(if $(APPLY),--apply,--dry-run) $(TOOL) $(VERSION)

release_tools:  ## Release every formula that is behind, then retire old ~/bin copies (dry run unless APPLY=1)
	@scripts/release-tools.sh $(if $(APPLY),--apply,--dry-run)

rename_projects:  ## Move renamed projects: GitHub repo, remote, folder, Claude memory (dry run unless APPLY=1)
	@scripts/rename-projects.sh $(if $(APPLY),--apply,--dry-run)

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
test:
	brew style Formula Casks
	brew audit --formula $(FORMULA)
	brew test ai-ui
	cd tests && python3 -m unittest -q

# The ai-ui CLI moved to AI-UI's cli/ on 2026-09-30; no copy of it may ship from this tap again.
CLI_OLD_COPIES := ai-ui scripts/nuke-sage scripts/formula-helpers.sh tests/test_ai_ui_runtime.py

check:  ## Before a push: old CLI copies, style, tests, formula tarballs and pins, ai-ui current, distribution.env copies, new names
	@left="$$(for f in $(CLI_OLD_COPIES); do [ -e "$$f" ] && echo "$$f"; done | tr '\n' ' ')"; \
	[ -z "$$left" ] || { echo "FAIL: the ai-ui CLI lives in AI-UI's cli/ now. Remove the old copies: git rm $$left"; exit 1; }
	@brew style Formula Casks
	@cd tests && python3 -B -m unittest -q
	@python3 -c 'import ast; ast.parse(open("offload").read(), "offload")'
	@scripts/check-formulae.sh
	@scripts/check-ai-ui-current.sh
	@$(MAKE) -s distribution_copies
	@scripts/check-names.sh $$(git diff --name-only --diff-filter=A origin/develop -- Formula Casks | sed 's|.*/||; s|\.rb$$||')

check_names:  ## Are these names free in Homebrew? NAMES="a b" (also warns on npm and this Mac's PATH)
	@scripts/check-names.sh $(NAMES)

install_hooks:  ## Fix formula style on commit, run `make check` before every push (points git at tools/git-hooks)
	@git config core.hooksPath tools/git-hooks
	@echo "hooks on: pre-commit runs brew style --fix on staged formulae; pre-push runs make check"

ai_ui_formula: distribution_verify  ## Point the ai-ui formula at AI-UI $(SERVER_TAG), the release whose cli/ is the CLI
	@scripts/ai-ui-formula.sh $(SERVER_TAG)

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
# Prerequisites
# ---------------------------------------------------------------------------
require_gitflow_next:
	@if ! git flow version 2>/dev/null | grep -q 'git-flow-next'; then \
		echo "Error: git-flow-next required (Go rewrite). Install: brew install git-flow-next"; \
		exit 1; \
	fi

.PHONY: help ai_ui_formula tool_release release_tools rename_projects test check check_names \
	install_hooks feature_finish require_gitflow_next \
	setup setup_siblings distribution_sync distribution_copies distribution_verify check_upstream

# ---------------------------------------------------------------------------
# distribution.env: one ordinary copy per sibling repo (Jidoka 自働化 primitive)
# ---------------------------------------------------------------------------
# The canonical distribution facts (image, server tag, volume, install command, CLI version). Each sibling keeps an ordinary copy; they were hard-linked until 2026-08-13, and the file's header says why that ended.
#
# This repo owns CLI_VERSION; AI-UI owns SERVER_TAG.
#
# `distribution_sync` publishes this copy to the siblings. `distribution_verify`
# refuses while a copy differs or the pinned server image is missing on GHCR;
# `ai_ui_formula` depends on it, so the formula never points at a broken release.

SIBLING_HOMEBREW ?= .
SIBLING_AI_UI    ?= ../WEB-AI--Sage-is-AI-UI
SIBLING_DOCS     ?= ../WEB-Sage.Education-docs
DIST_SOURCE      := $(SIBLING_HOMEBREW)/distribution.env
DIST_PEERS       := $(SIBLING_AI_UI)/distribution.env $(SIBLING_DOCS)/distribution.env

## setup_siblings — check the three sibling repos and publish distribution.env to them.
##
## Verifies all three repos are checked out side-by-side. If a sibling is
## missing, prints the exact `git clone` command and exits non-zero. If
## all three are present, calls distribution_sync to publish the copies.
## Idempotent — safe to re-run.
setup_siblings:
	@chmod +x tools/setup_siblings.sh
	@tools/setup_siblings.sh

## setup — fresh-machine bootstrap. Currently equivalent to setup_siblings;
## reserved for additional homebrew-apps setup steps (lint config, etc.).
setup: setup_siblings install_hooks
	@echo ""
	@echo "=== Setup complete ==="

distribution_sync:
	@test -f $(DIST_SOURCE) || { \
		echo "ERROR: $(DIST_SOURCE) not found. This repo holds the canonical file."; \
		exit 1; \
	}
	@for p in $(DIST_PEERS); do d=$$(dirname "$$p"); \
		if [ ! -d "$$d" ]; then echo "ERROR: $$d not found. Run 'make setup_siblings' first."; exit 1; \
		elif cmp -s $(DIST_SOURCE) "$$p"; then echo "  already equal: $$p"; \
		else cp $(DIST_SOURCE) "$$p.new" && mv "$$p.new" "$$p" && echo "  published:     $$p"; fi; \
	done
	@$(MAKE) distribution_verify

distribution_copies:  # the siblings' copies of distribution.env equal this one
	@for p in $(DIST_PEERS); do \
		test -f "$$p" || { echo "FAIL: $$p missing — run 'make setup_siblings'"; exit 1; }; \
		cmp -s $(DIST_SOURCE) "$$p" || { echo "FAIL: $$p differs. Reconcile by hand, then 'make distribution_sync'."; exit 1; }; \
	done
	@echo "OK: distribution.env matches both siblings."

distribution_verify: check_upstream distribution_copies
	@server_tag=$$(grep '^SERVER_TAG=' $(DIST_SOURCE) | cut -d= -f2); \
	image_reg=$$(grep '^IMAGE=' $(DIST_SOURCE) | cut -d= -f2); \
	echo "Checking GHCR: $$image_reg:$$server_tag ..."; \
	if ! docker manifest inspect "$$image_reg:$$server_tag" >/dev/null 2>&1; then \
		echo "FAIL: $$image_reg:$$server_tag not found on GHCR."; \
		echo "  Release AI-UI first: make ship (in WEB-AI--Sage-is-AI-UI)"; \
		exit 1; \
	fi; \
	echo "OK: $$image_reg:$$server_tag verified on GHCR."

## check_upstream — compare local SERVER_TAG against AI-UI's latest GitHub release.
##
## Exits 0 when in sync, 1 when SERVER_TAG is ahead of upstream (would ship a
## CLI pinned to a server that doesn't exist), 2 when SERVER_TAG lags behind.
## WARN_LAG=1 allows the lag case (intentional LTS lane).
##
## No `gh` / `jq` dep — pure curl + sed + sort -V. Rate limit: 60 req/hour
## per IP unauthenticated; fine for manual + release-gate usage.
check_upstream:
	@upstream=$$(curl -fsSL "https://api.github.com/repos/Sage-is/AI-UI/tags?per_page=100" 2>/dev/null \
	             | grep -E '"name"' \
	             | sed -E 's/.*"v?([^"]+)".*/\1/' \
	             | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$$' \
	             | sort -V | tail -1); \
	[ -n "$$upstream" ] || { echo "ERROR: could not fetch AI-UI tags from GitHub"; exit 1; }; \
	current=$$(grep ^SERVER_TAG= $(DIST_SOURCE) | cut -d= -f2); \
	if [ "$$current" = "$$upstream" ]; then \
	  echo "OK: SERVER_TAG=$$current matches AI-UI latest"; \
	elif printf '%s\n%s\n' "$$upstream" "$$current" | sort -V -C; then \
	  echo "ERROR: SERVER_TAG=$$current is ahead of AI-UI latest ($$upstream)"; \
	  echo "       Cannot ship a CLI pinned to a server that doesn't exist yet."; \
	  exit 1; \
	else \
	  echo "WARN: SERVER_TAG=$$current lags AI-UI latest ($$upstream)"; \
	  echo "      Run AI-UI '_pin_server_tag' to bump, or set WARN_LAG=1 to allow lag."; \
	  [ "$$WARN_LAG" = "1" ] || exit 2; \
	fi
