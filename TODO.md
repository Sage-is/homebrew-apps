# Roadmap

## TODO

- [ ] **sage-box 0.1.0 reports success while steps fail** #critical (found on the pilot box, 2026-10-20): `--apply` exited 0 with the registry, Trellis and backup steps all failed
  - [ ] [WE] `apply` exits non-zero and names each failed step; `verify` runs and its result decides the exit code
  - [ ] [WE] Registry: `docker login` over SSH cannot use the keychain (`User interaction is not allowed. (-25308)`), and with no `credsStore` the CLI picks `osxkeychain` again while `auths` is empty. Write the `ghcr.io` auth into `~/.docker/config.json` (mode 600, token on stdin), as done by hand 2026-10-20
  - [ ] [WE] Leftovers: also remove Docker Desktop's `credsStore: osxkeychain` and its plugin hooks (`ai`, `scout`, `debug`, `compose`) from `~/.docker/config.json`, after a dated backup
  - [ ] [WE] Sudo: `sudo -S -v` caches no grant over SSH without a tty, so each later `sudo -n` fails (`sudo: a password is required`). Replaced 2026-10-22 by the sudoers drop-in on the card "Box access standard": no password in automation at all
  - [ ] [WE] Tests for all three, then a 0.1.1 release; [MANUALLY] `brew upgrade sage-box`

- [ ] **Box access standard: read key, unlock window, FIDO admin** #critical #security (Alexander, 2026-10-22): one standard for every managed box (pilot first, School B's from day one); plan `~/.claude/plans/what-about-when-i-purring-castle.md`; the agreements promise least privilege and logging
  - [x] [WE] Read tier: a `restrict,from=,command=` key per box; root-owned dispatcher `/usr/local/libexec/sage-box/read` (probe, backup, power, containers, version), 64 KiB cap, each call to `logger`; `sage-box <box> read VERB` (2026-10-22)
  - [x] [WE] Unlock window: `unlock [--hours 1-4]` signs a certificate (FIDO CA only, principal `sage-window`, `-O clear`, source-address); `lock`; admin verbs stop when locked; `--as-me` for a person's own key
  - [x] [WE] Steps `access` and `keys`: dispatcher, root helper (fixed verbs), sudoers drop-in per verb (`visudo -cf`), tagged key lines; root installs only copies matching digests on its own command line, `/usr/bin/sudo` by path
  - [x] [WE] `load_box` resolves only what a command needs; `-F none` and `IdentitiesOnly=yes`; ControlMaster per door; `sudo_remote` gone with the `sudo -n` bug; 87 tests, shellcheck, bash 3.2
  - [x] [WE] `sage-box.1`, `docs/boxes/access.md`, `example.env`; `make box_read` in Trellis (feature branch) and AI-UI: `$(value)` and env only, `$` refused; injection tried, nothing ran
  - [ ] [MANUALLY] Committed `56f9412`, README `d10298c`, bump `e8cb01d` (2026-10-25); next `make tool_release TOOL=sage-box VERSION=0.1.1 APPLY=1`; then `make box_up BOX=pilot APPLY=1` installs the doors
  - [x] [WE] The root helper's Homebrew install needs Apple's notarization and Homebrew's signing team `927JGANW46` (read from the real package 2026-10-22); any other signer is refused
  - [x] [WE] `status` says whether launchd holds the backup job and how old the newest backup is, WARNING past 26 h; `--apply` reloads a job that was never loaded (the pilot's, 2026-10-22: 0.1.0's `sudo -n` bootstrap failed)
  - [x] [WE] Fixed: `load_box` cleared the tool's own `BOX_BACKUPS` constant (every `BOX_` name goes), which broke the backup pull; now `TRELLIS_BACKUPS`, and a test refuses any `BOX_` constant; 90 tests
  - [x] [WE] `trellis-crm manage` (allowlisted manage.py), `sage-box <box> manage`, Trellis `make box_manage` (`40fb450`, Trellis `3f39d1e`, 2026-10-26)
  - [x] [WE] Root verb `tunnel-daemon` and `sage-box <box> tunnel install --env-file` (checked env file on stdin); `cloudflared` via the tools step when `BOX_TUNNEL` is set
  - [x] [WE] Box backups never recopy: `trellis-crm backup --dir` staging, rsync `--link-dest` pull into an encrypted sparse bundle, own read-only rrsync door key; `age`/`scp` gone; 118 + 117 tests
  - [ ] [MANUALLY] Release sage-box 0.1.2 and trellis-crm 0.1.3 after the push packet (run sheet 3b)
  - [ ] Upstream: homebrew-core's rsync installs an `rrsync` that runs `/usr/bin/rsync` (openrsync on macOS), so `-ro` confinement fails; our door swaps in brew rsync until fixed
  - [ ] Open: `rotate-key read`, `unlock --all`; `trellis-crm restore` from a snapshot folder (it takes only `.tar.gz` today)
  - [ ] [MANUALLY] FIDO: an `ed25519-sk` CA and admin key on each device (`ecdsa-sk` on U2F-only), a spare of each; `brew install openssh` here if Apple's `ssh-keygen -t ed25519-sk` refuses
  - [ ] Vault: `<box>-read-key` per box (pilot's stored 2026-10-25 as `bw:pilot-read-key` in Agents-edit); after the window ships, retire `<box>-ssh-key` and move `<box>-user` to a humans-only collection
  - [x] [WE] Tailnet policy drafted (2026-10-22): OPS-Sage `deploy/tailscale/policy.hujson`; `tag:box` reachable from `autogroup:admin` on port 22 only, no box-initiated traffic, Tailscale SSH off
  - [ ] [MANUALLY] Tailnet: tag both boxes `tag:box`, rename them `box-pilot` and `box-school-b` (both names carry real names today), then paste the policy
  - [ ] [MANUALLY] Allow rule `Bash(make box_read BOX=* WHAT=*)` in the Trellis and AI-UI `.claude/settings.json`

- [ ] **The ai-ui CLI moves into AI-UI** (2026-09-30): Homebrew counts the formula's download repo, so AI-UI's stars and forks must be the ones counted #launch
  - [x] [WE] AI-UI's `cli/` holds `ai-ui`, `nuke-sage` and 16 tests (gate `cli_tests`); `make ai_ui_formula` points the formula at an AI-UI release and keeps the old major as `ai-ui@N`
  - [x] [WE] Git-flow release machinery for ai-ui removed (Makefile 463 to 190 lines); README, `docs/versioned-formulas.md` and `docs/poka-yoke-lessons.md` follow
  - [x] [WE] Guards: `make check` refuses while old CLI copies remain; `ai_ui_formula` and the formula check refuse a release without `cli/`; a pre-commit hook runs `brew style --fix`; hooks on for this Mac
  - [ ] [MANUALLY] Remove the tap's copies in the same commit: `git rm ai-ui scripts/nuke-sage scripts/formula-helpers.sh tests/test_ai_ui_runtime.py`
  - [x] [WE] AI-UI's `make ship` runs `ai_ui_formula` last; `make check` refuses a push while the formula lags AI-UI's newest release (2026-09-30)
  - [ ] [MANUALLY] After AI-UI's next release: commit and push the tap's formula change, then `brew upgrade ai-ui && brew test ai-ui`
  - [ ] Later: `CLI_VERSION` in `distribution.env` is unread now that the CLI's version is `SERVER_TAG`; drop it from all three copies

- [ ] **sage-secret: the session key and bootstrap credentials sit within an agent's reach** #security (2026-10-24; found by the guide-and-guard review)
  - [ ] [WE] `bw()` passes `--session KEY` on the command line, where `ps` can see it while `bw` runs: pass it as `BW_SESSION` in the child's environment instead
  - [ ] [MANUALLY] Decide where the agent account's API key and password live: today the login Keychain (service `sage-secret`), readable by any process that can run `security` as the user
  - [ ] [WE] Level-2 proxy: scope each secret by host, method and path, and log each injected request

- [ ] **sage-tunnel and sage-secret: tunnels by tool, secrets by reference** (Alexander, 2026-10-06) #security
  - [x] [WE] `sage-tunnel` (list, create, route, unroute, delete): routes live in Cloudflare, proxied CNAMEs only, connector token written to the env file at 600 and never printed; 11 tests, 3 mutations caught
  - [x] [WE] `sage-secret` (setup, check, run): `bw:ITEM[/FIELD]` references resolved per run from an agent account on Vaultwarden, vault locked after; bootstrap credentials in the Keychain; 11 tests
  - [ ] [MANUALLY] `brew install bitwarden-cli`; the Vaultwarden agent account and its "Agents" collection; a Cloudflare token (Tunnel: Edit, DNS: Edit on startr.cloud) stored there as `cloudflare-tunnel-startr`
  - [ ] [MANUALLY] `sage-secret setup --server <vaultwarden url>` (security prompts for the agent's API key and password)
  - [ ] [WE] `sage-tunnel create yt-transcribe yt.startr.cloud` for the transcription service, then recreate it and check the fixed URL
  - [ ] Level 2, a credential-injecting proxy: research brief pending (NVIDIA and Meta work, Secretless Broker, placeholder-token designs)

  - [x] [WE] First real run 2026-10-07 (agent Lamarr Relay 42 on warden.startr.cloud, yt.startr.cloud created): `check` now syncs first and lists organizations and collections; `sage-tunnel` finds the account through the token's zone when a zone-scoped token can list no accounts; hints name the command as it was run; 12 + 12 tests
- [ ] **sage-runtime: Docker Desktop optional for every project** (Alexander, 2026-10-06) #ux
  - [x] [WE] `sage-runtime` (status, use, copy-volume, copy-image), man page, formula depending on colima, docker, buildx, compose and the keychain helper; 20 tests, mutation-checked
  - [ ] [MANUALLY] `brew install colima docker docker-buildx docker-compose docker-credential-helper` on this Mac, then [WE] prove `use`, both copies and the switch back against real runtimes
  - [x] [WE] 2026-10-09 on krunkit: `--add-host=host.docker.internal:host-gateway` resolves 192.168.5.2 and reaches a port on the Mac; amd64 on the build VM 5.4 s vs 35.4 s under QEMU on krunkit (native arm64 5.1 s); the Makefiles' buildx route built and ran a linux/amd64 image
  - [ ] [WE] File events for hot reload on krunkit (`make reload_gate` sets `WATCHFILES_FORCE_POLLING=true`; run it once on Colima)
  - [x] [WE] 2026-10-08: a new dev VM is `krunkit` on Apple Silicon when krunkit is installed (memory goes back to macOS; GPU through Vulkan, proven: "Virtio-GPU Venus (Apple M1 Max)" with a patched-Mesa image; footprint 1.9 GB after 4 and 3 GiB loads vs 6.8 GB for an idle vz VM), half the Mac's memory up to 12 GiB; `build-vm` runs a vz + Rosetta profile `build` for amd64; `migrate` moves every named volume (`--images`, `--dry-run`) and switches; 35 tests, mutation-checked
  - [x] [WE] `make migrate_to_colima` (DRY=1, IMAGES=1) in Trellis and AI-UI, a thin call to `sage-runtime migrate`
  - [ ] [WE] Drop the Lima `override.yaml` workarounds as Colima releases fix them: the virtiofs line for abiosoft/colima#1607 (0.10.3 hands krunkit 9p), the mount-by-label boot step for abiosoft/colima#1614 (`data_disk_step` in `lib/sage-runtime.sh`)
  - [ ] [WE] `trellis-crm` exports `DOCKER_CONTEXT`, so its engine start reads that, not docker's saved context; harmless while it only starts the runtime in use. If it ever starts a second runtime (as `ai-ui migrate` does), start it with `DOCKER_CONTEXT=''`
  - [x] [WE] This Mac (2026-10-08, Alexander's permission): `default` rebuilt as krunkit on its kept data disk, every volume and image identical; found and fixed abiosoft/colima#1614 (Docker silently on the root disk) with a mount-by-label boot step; `build-vm` proven with an amd64 build
  - [x] [WE] 2026-10-09: `convert` (vz to krunkit, refuses unless Docker's data is on the data disk, prints the way back, resumes from `~/.sage-is/convert`); the runtime code in one shared `lib/sage-runtime.sh` that `sage-runtime` and `trellis-crm` source and AI-UI vendors (`make runtime_sync`, drift test); Docker Desktop step-away (login helper to the Keychain, dangling plugin links, Login Item note, `brew --prefix`, `$SAGE_DATA` guard on `build-vm`); 154 tests
  - [x] [WE] 2026-10-09: `make convert_to_krunkit` in Trellis and AI-UI; one install line everywhere (`brew tap sage-is/apps && brew tap libkrun/krun && brew trust --tap sage-is/apps libkrun/krun && brew install …`); `tool-release.sh` and `check-formulae.sh` check every file a formula installs (`lib/`)
  - [x] Released: sage-runtime 0.1.0 (2026-10-11), trellis-crm 0.1.0 and 0.1.1 (2026-10-14); peers install without `--HEAD`
  - [ ] [MANUALLY] Peer installs: the install line above, then `sage-runtime migrate` (from Docker Desktop) or `sage-runtime convert --yes` (from Colima vz)
  - [ ] [MANUALLY] After 2026-10-16: uninstall Docker Desktop on this Mac and delete `/Volumes/Somma 01 Dock Drive/Docker/DockerDesktop/Docker.raw` (49 GB)
    - [ ] [WE] Inventory first (2026-10-21 plan): `sage-runtime migrate --from docker-desktop --images --dry-run` and the anonymous volumes, gone over together
    - [ ] [WE] Copy what is kept; [MANUALLY] an encrypted DMG of `Docker.raw` on Somma 02, its password in the vault; then Docker's own uninstaller

- [ ] **Map Apple's container (macOS 26) onto our stack** (Alexander, 2026-10-21: Colima for now) #ux
  - [ ] [WE] First, a session where we go over together what Apple's containers are: how they run, and what they can and cannot do next to Colima
  - [ ] [WE] What breaks without a Docker socket: compose, buildx, our Makefiles' `docker` calls
  - [ ] [WE] Whether a shim or a separate runtime option fits, and how `~/.sage-is/runtime` records it

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
  - [x] [WE] 2026-10-16: `tool_release`, `release_tools` and `rename_projects` act only at `APPLY=1`; `APPLY=0` and `APPLY=no` are dry runs
  - [x] [WE] 2026-10-16: `sage-box update` stops at the first failing step; `sage-ai` rejoins `sage-network` after each `ai-ui start`, and `verify` checks it
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
  - [ ] [MANUALLY] After `brew install git-release`: `rm ~/bin/git-release` (re-linked to the tap file 2026-10-21)
  - [x] [WE] 2026-10-21: startr.sh keeps no copy; its Makefile offers the brew install through `startr.sh/scripts/tools.sh`
  - [x] [WE] 2026-10-21: `has_target` reads make's database, never `make -n`, which ran recipe lines that call `$(MAKE)`; proven by startr.sh `tests/run.sh`
  - [ ] [MANUALLY] Tag `git-release-v1.0.0` and push, then [WE] pin its sha256: the formula's sha is all zeros and the tag is missing, so `brew install git-release` fails today

- [ ] **Renames and branding, decided 2026-09-28** #brand: plain tool names, no prefix
  - [x] [WE] `captain` → `cr-deploy` (an official cask owns `captain`; CapRover's npm CLI ships `caprover-*` commands, including `caprover-deploy`); `captain` alias until cr-deploy 0.2.0
  - [x] [WE] storyboarder → comicreel: app shown as "Sage.is ComicReel", package `comicreel`, `COMICREEL_*` settings; `storyboarder` command and `STORYBOARDER_*` read for one release; model cache moves on first launch
  - [x] [WE] local-whisper → Talking: links, notes and the app's Settings link point at `Sage-is/talking`; cask url and homepage too
  - [ ] [MANUALLY] `make rename_projects APPLY=1`: repos to `Sage-is/comicreel` and `Sage-is/talking`, folders to `MEDIA-ComicReel` and `APP-Talking`, Claude memory carried along
  - [ ] Next release after these: drop the `captain`, `storyboarder`, `STORYBOARDER_*` and `STORYBOARDER_REF` aliases
  - [x] `SAGE.IS mini` keeps its casing

- [ ] **cr-deploy: domains behind Cloudflare's proxy, and no env values on screen** (2026-10-06) #security
  - [x] [WE] `ensure` keeps force-SSL on when the default subdomain already has a certificate (a live password manager served plain http without it); test `ForceSsl`
  - [x] [WE] `DomainChecks`: a 1107 on connect or certificate turns the record DNS-only once, retries the captain's own check every 30 s for up to 10 min, then restores the proxy; the old wait on this Mac's resolver timed out behind Tailscale DNS; test `DomainThroughTheProxy`; proven on warden.startr.cloud (81 s)
  - [ ] Mask every env value in the app summary, not only PASSWORD/SECRET/KEY/TOKEN names, and add ADMIN to the hidden names: `STALWART_RECOVERY_ADMIN` leaked twice into a session transcript (moved from the Trellis board's Stalwart card, 2026-10-06)
  - [ ] [MANUALLY] Commit, then `make tool_release` for cr-deploy; `~/bin/cr-deploy` is a stale second copy without either fix: delete it

- [ ] **trellis-crm joins the tap** (Alexander, 2026-10-07): the Trellis source stays private; the CLI, its tests and its formula live here
  - [x] [WE] Moved from Trellis `cli/` with its 31 tests; `trellis-crm dev` added, beside the real instance on `127.0.0.1:8031` (5 tests); `Formula/trellis-crm.rb`, not yet released
  - [x] [WE] 2026-10-15: `trellis-crm dev --env-file FILE` (mode 600; keys and demo switches only, mail settings refused) and `--reset` (the dev volume only); 4 tests, 78 pass
  - [x] [WE] 2026-10-15: `trellis-crm dev --link-ai-ui` (opt-in): the dev install joins `sage-network` and `sage-ai` is connected; the tap sets `AI_UI_URL`, and `--env-file` must carry `AI_UI_API_KEY` and `NOTIFY_SPACE_ID`; mail never
  - [x] [WE] 2026-10-16 poka-yoke: `start` refuses `LOAD_FIXTURES` and `DEMO_RESET*` in the real settings file and pins `LOAD_FIXTURES=0`; it never prints the first admin password
  - [x] [WE] 2026-10-16 poka-yoke: dev env-file lines must be NAME=value, refused by line number and never echoed; `--link-ai-ui` refuses the real install's key or Space; dev passes `CRM_PUBLIC_URL`; `--reset` checks the volume is gone; a warning when dev and the real install share a checkout
  - [ ] [MANUALLY] Tag `ghcr.io/sage-is/trellis:0.1.2` from `0.1.1` (the formula pins the image to its version), release trellis-crm 0.1.2 (`make tool_release TOOL=trellis-crm VERSION=0.1.2 APPLY=1`), then `make box_update BOX=pilot` in Trellis
  - [x] 2026-10-14: Trellis `v0.1.0` tagged and `ghcr.io/sage-is/trellis:0.1.0` pushed as a private package (both arches, from the build VM); trellis-crm 0.1.0 and 0.1.1 released; `0.1.1` is the same image retagged, since the formula pins the image to the CLI's version
  - [ ] Poka-yoke: `tool-release.sh` checks, for trellis-crm, that `ghcr.io/sage-is/trellis:<version>` exists before it tags, or retags the previous image (`docker buildx imagetools create`); today the pin can name an image nobody pushed
  - [ ] One copy of the shared shell: `trellis-crm` carries ai-ui's runtime block and path helpers; one file in the tap, sourced by both

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
- [ ] Verify `ensure_docker` auto-start on a clean macOS install (no Colima VM yet)

## Backlog — Unscheduled

### macOS Swift Launcher

- [ ] Build a minimal macOS status-bar app (NSStatusItem) wrapping `ai-ui start/stop/open` — one-click experience for non-terminal users; ship as a `.app` via a separate Homebrew cask

## v0.3.0 — Next

### Linux

- [ ] Test on Linux (full CLI exercise)
- [ ] Confirm `xdg-open` fallback works on Linux for `ai-ui open`
- [ ] Confirm `ensure_docker` Linux path (`systemctl start docker`)
