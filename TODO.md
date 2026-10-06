# Roadmap

## TODO

- [ ] **The ai-ui CLI moves into AI-UI** (2026-09-30): Homebrew counts the formula's download repo, so AI-UI's stars and forks must be the ones counted #launch
  - [x] [WE] AI-UI's `cli/` holds `ai-ui`, `nuke-sage` and 16 tests (gate `cli_tests`); `make ai_ui_formula` points the formula at an AI-UI release and keeps the old major as `ai-ui@N`
  - [x] [WE] Git-flow release machinery for ai-ui removed (Makefile 463 to 190 lines); README, `docs/versioned-formulas.md` and `docs/poka-yoke-lessons.md` follow
  - [x] [WE] Guards: `make check` refuses while old CLI copies remain; `ai_ui_formula` and the formula check refuse a release without `cli/`; a pre-commit hook runs `brew style --fix`; hooks on for this Mac
  - [ ] [MANUALLY] Remove the tap's copies in the same commit: `git rm ai-ui scripts/nuke-sage scripts/formula-helpers.sh tests/test_ai_ui_runtime.py`
  - [x] [WE] AI-UI's `make ship` runs `ai_ui_formula` last; `make check` refuses a push while the formula lags AI-UI's newest release (2026-09-30)
  - [ ] [MANUALLY] After AI-UI's next release: commit and push the tap's formula change, then `brew upgrade ai-ui && brew test ai-ui`
  - [ ] Later: `CLI_VERSION` in `distribution.env` is unread now that the CLI's version is `SERVER_TAG`; drop it from all three copies

- [ ] **sage-tunnel and sage-secret: tunnels by tool, secrets by reference** (Alexander, 2026-10-06) #security
  - [x] [WE] `sage-tunnel` (list, create, route, unroute, delete): routes live in Cloudflare, proxied CNAMEs only, connector token written to the env file at 600 and never printed; 11 tests, 3 mutations caught
  - [x] [WE] `sage-secret` (setup, check, run): `bw:ITEM[/FIELD]` references resolved per run from an agent account on Vaultwarden, vault locked after; bootstrap credentials in the Keychain; 11 tests
  - [ ] [MANUALLY] `brew install bitwarden-cli`; the Vaultwarden agent account and its "Agents" collection; a Cloudflare token (Tunnel: Edit, DNS: Edit on startr.cloud) stored there as `cloudflare-tunnel-startr`
  - [ ] [MANUALLY] `sage-secret setup --server <vaultwarden url>` (security prompts for the agent's API key and password)
  - [ ] [WE] `sage-tunnel create yt-transcribe yt.startr.cloud` for the transcription service, then recreate it and check the fixed URL
  - [ ] Level 2, a credential-injecting proxy: research brief pending (NVIDIA and Meta work, Secretless Broker, placeholder-token designs)

- [ ] **sage-runtime: Docker Desktop optional for every project** (Alexander, 2026-10-06) #ux
  - [x] [WE] `sage-runtime` (status, use, copy-volume, copy-image), man page, formula depending on colima, docker, buildx, compose and the keychain helper; 20 tests, mutation-checked
  - [ ] [MANUALLY] `brew install colima docker docker-buildx docker-compose docker-credential-helper` on this Mac, then [WE] prove `use`, both copies and the switch back against real runtimes
  - [ ] [WE] Verify on Colima: `host.docker.internal` with `host-gateway` (to the Mac and to a published port), amd64 via Rosetta, file events for hot reload
  - [ ] [MANUALLY] Commit, then `make tool_release TOOL=sage-runtime VERSION=0.1.0 APPLY=1`
  - [ ] [WE] ai-ui and trellis-crm source sage-runtime's runtime block instead of their own copies (`depends_on "sage-runtime"`)

- [ ] **Colima by default: no Docker Desktop windows on first launch** (ai-ui 3.2.0_1, 2026-09-29) #ux
  - [x] [WE] `ai-ui` runs Sage in Colima, Docker Desktop or OrbStack; the first start asks when a Mac has more than one; `--runtime NAME` picks directly, remembered in `~/.sage-is/runtime`
  - [x] [WE] Colima is a formula dependency on macOS, so nothing installs at run time; the post-install message lists the `--runtime` one-liners; 11 runtime tests
  - [x] [WE] `make release_ai_ui` cuts the next revision (`3.2.0_1`) once `v3.2.0` exists; the formula states `version` and `revision`
  - [x] [WE] `ai-ui nuke` works from a brew install: the formula ships `nuke-sage`, its flags pass through, and `--genesis` removes every Docker provider
  - [x] [MANUALLY] Commit, then `make release_ai_ui` to release 3.2.0_1 (2026-09-29)
  - [x] [WE] 3.2.0_1 on a school Mac: Colima's VM up in 32 s, no window; the pull then failed on Docker Desktop's leftover `credsStore`
  - [x] [WE] `ai-ui` drops a `credsStore` whose helper is missing, with a dated backup; 2 tests
  - [x] [MANUALLY] 3.2.0_2 released and working on the school Mac (2026-09-30), the last CLI built from this tap
  - [ ] [MANUALLY] Verify on a Docker Desktop Mac: the first start asks which runtime to use
  - [ ] Docker Desktop teams: an admin install with `install --accept-license --user=<name>` skips its terms and password screens

- [ ] **Release the tap's tools** #critical: one command per step, dry run unless `APPLY=1`
  - [x] [WE] `make release_tools` / `tool_release` (tag, push, pin sha256, install, test, retire old `~/bin` copies; hard-linked copies left alone); sandbox-proven 2026-09-28
  - [x] [MANUALLY] Stray `release/1.0.5` branch deleted; the formula guard is on `develop` (6035334)
  - [x] [MANUALLY] `make release_ai_ui`: CLI 3.2.0 = AI-UI 3.2.0, `ai-ui@3` created (2026-09-29)
  - [ ] [MANUALLY] On other Macs: `brew update && brew upgrade ai-ui && ai-ui update`
  - [x] [WE] `make check` before a push: style, unit tests, formula tarballs and pins, `distribution.env` copies, new names (2026-09-29)
  - [ ] [MANUALLY] Commit the `make check` work; `make install_hooks` once per clone so `git push` runs it
  - [x] [WE] Tap gates follow the copy model: `distribution_verify` compares content (was 3 hard links, broken since 2026-08-13); `check_upstream` ignores non-version tags like `pre-reword-diagnostics`
  - [ ] [MANUALLY] Commit this tap, then `make rename_projects APPLY=1` (see the renames card)
  - [ ] [MANUALLY] Release ComicReel 2.0.0-alpha.3 from `MEDIA-ComicReel`: the `comicreel` launcher pins it
  - [ ] [MANUALLY] `make release_tools APPLY=1`: comicreel, cr-deploy, git-release, mdprose, offload
  - [ ] [MANUALLY] First `v0.1.0` of Sage-is/work-delegation, then `make release_tools APPLY=1` again to pin it

- [ ] **One git-release, from the tap** (2026-09-29): repos run it from PATH and keep no copy
  - [x] [WE] The 16 repos' `make release` runs `git-release`, with the brew install line when it is missing
  - [ ] [MANUALLY] Delete each repo's `scripts/release.sh` (a hard link; the tap keeps the file), then commit the Makefile and the deletion after `git-release-v1.0.0` ships
  - [ ] [MANUALLY] After `brew install git-release`: `rm ~/bin/git-release`; startr.sh keeps `src/scripts/release.sh`, its published copy

- [ ] **Renames and branding, decided 2026-09-28** #brand: plain tool names, no prefix
  - [x] [WE] `captain` → `cr-deploy` (an official cask owns `captain`; CapRover's npm CLI ships `caprover-*` commands, including `caprover-deploy`); `captain` alias until cr-deploy 0.2.0
  - [x] [WE] storyboarder → comicreel: app shown as "Sage.is ComicReel", package `comicreel`, `COMICREEL_*` settings; `storyboarder` command and `STORYBOARDER_*` read for one release; model cache moves on first launch
  - [x] [WE] local-whisper → Talking: links, notes and the app's Settings link point at `Sage-is/talking`; cask url and homepage too
  - [ ] [MANUALLY] `make rename_projects APPLY=1`: repos to `Sage-is/comicreel` and `Sage-is/talking`, folders to `MEDIA-ComicReel` and `APP-Talking`, Claude memory carried along
  - [ ] Next release after these: drop the `captain`, `storyboarder`, `STORYBOARDER_*` and `STORYBOARDER_REF` aliases
  - [x] `SAGE.IS mini` keeps its casing

## v0.2.0 — Current

### Makefile

- [x] Fix `release_finish` — sha256 retry loop for GitHub archive lag
- [x] Fix stale `IMAGE_TAG` in sha256 commit message (shell expansion instead of Make variable)
- [x] Auto-bump `ai-ui` script VERSION in `bump_formula_url`
- [x] Add `require_gitflow_next` guard for git-flow-next compatibility
- [ ] Clean stale `release/0` git flow config entry
- [x] **OBSOLETE (2026-09-30)**: test the `patch_release` → `release_finish` cycle; the ai-ui CLI moved into AI-UI and those targets are gone

### Script (`ai-ui`)

- [x] Add `version` / `--version` / `-v` command
- [x] Expose `--dir` flag in usage text
- [x] Add `xdg-open` fallback for Linux in `cmd_open`

### Dev mode (`ai-ui dev`)

- [ ] Test `ai-ui dev` end-to-end — clone, mount source, DEV_MODE=true, hot reload
- [ ] Verify dev-mission nag flow: DeveloperStep signup → DevMissionReminderModal → `ai-ui dev` → celebration
- [ ] Test `ai-ui dev --dir /custom/path` with existing clone
- [ ] Verify Vite HMR port 5173 is accessible from host

### Formula

- [ ] Re-test `brew tap sage-is/apps && brew install ai-ui` after AI-UI Docker image slimming (currently ~9.7GB, targeting ~3.5-4GB)
- [ ] Verify `ensure_docker` auto-start on clean macOS install (Docker Desktop not yet installed)

## Backlog — Unscheduled

### macOS Swift Launcher

- [ ] Build a minimal macOS status-bar app (NSStatusItem) wrapping `ai-ui start/stop/open` — one-click experience for non-terminal users; ship as a `.app` via a separate Homebrew cask

## v0.3.0 — Next

### Linux

- [ ] Test on Linux (full CLI exercise)
- [ ] Confirm `xdg-open` fallback works on Linux for `ai-ui open`
- [ ] Confirm `ensure_docker` Linux path (`systemctl start docker`)
