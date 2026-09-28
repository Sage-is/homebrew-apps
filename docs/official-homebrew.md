# Getting a tool into official Homebrew

This tap is our own shelf. Official Homebrew is the world's shelf.

Users install from it without trusting us first.

A command-line tool belongs in [homebrew/core](https://github.com/Homebrew/homebrew-core).

A Mac app belongs in [homebrew/cask](https://github.com/Homebrew/homebrew-cask).

We never put a command-line tool in a cask.

The rules below come from the [Package Acceptance Policy](https://docs.brew.sh/Package-Acceptance-Policy), read 2026-09-27.

## What every submission must clear

**Notability.** The repo needs 75 stars, 30 forks, or 30 watchers.

**Notability, owner-submitted.** When the repo owner submits it, the bar rises to 225 stars, 90 forks, or 90 watchers.

**Age.** The repo must be at least 30 days old.

**Licence.** The licence must be DFSG-compatible.

**Build.** The tool must have a stable tagged release built from checksummed source.

**No moving targets.** Nothing may be fetched at install time or run time from a moving target.

**No casks from formulas.** A formula may not depend on a cask, and no step may install one.

**Gatekeeper.** A cask must pass Gatekeeper: an Apple Developer ID signature and notarisation.

**Quarantine.** A cask must not clear the quarantine flag.

## Where each tool stands

| Tool | Shelf (core/cask) | Today (stars, 2026-09-27) | What blocks it |
|------|-------------------|---------------------------|----------------|
| ai-ui | core | 9 | Notability. The CLI offers to install Docker Desktop, a cask, at run time. |
| comicreel | core | 0 (private repo) | The repo is private and the app is a 2.0 alpha. The launcher fetches the app with uv at run time. It needs a formula that builds the packaged app from source. It is not called storyboarder because an official storyboarder cask already exists. |
| todoscope | cask | 2 | Developer ID and notarisation are deferred. Postflight clears quarantine. Notability. |
| downes | cask | 6 | Developer ID and notarisation are deferred. Postflight clears quarantine. Notability. |
| mini | cask | 1 | Developer ID and notarisation are deferred. Postflight clears quarantine. Notability. |
| talking | cask | 0 | Developer ID and notarisation are deferred. Postflight clears quarantine. Notability. |
| offload | core | 0 | Notability, counted on its own repo (it lives in this tap today). Otherwise shaped for core. |
| mdprose | core | 0 | Notability, counted on its own repo (it lives in this tap today). Otherwise shaped for core. |
| git-release | core | 0 | Notability, counted on its own repo (it lives in this tap today). Otherwise shaped for core. |
| work-delegation | core | 0 | Notability. Otherwise shaped for core. |
| cr-deploy | stays in the tap | 0 | It is an internal ops tool. (Formerly captain; renamed because an official cask uses that name and CapRover's own CLI ships a `caprover-deploy` command.) |

## How to submit when a tool qualifies

Move a tool that lives in this tap into its own repository first: notability counts on the upstream repo.

Get `brew audit --new --strict --online` green.

Open a pull request to [Homebrew/homebrew-core](https://github.com/Homebrew/homebrew-core) for a command-line tool, or to [Homebrew/homebrew-cask](https://github.com/Homebrew/homebrew-cask) for a Mac app.

Someone other than us must submit it until our repo reaches the 225-star bar.
