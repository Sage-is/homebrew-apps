# Roadmap

## TODO

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
- [ ] Test full `make patch_release` → `make release_finish` cycle end-to-end

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
