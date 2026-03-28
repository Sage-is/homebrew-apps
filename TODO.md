# Roadmap

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

## v0.3.0 — Next

### Linux
- [ ] Test on Linux (full CLI exercise)
- [ ] Confirm `xdg-open` fallback works on Linux for `ai-ui open`
- [ ] Confirm `ensure_docker` Linux path (`systemctl start docker`)
