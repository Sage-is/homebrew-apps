# offload

**Your Mac's internal SSD fills up. External drives are cheap. The gap between those two facts is `offload`.**

```bash
brew install sage-is/apps/offload
```

offload moves heavy, low-velocity data — app support folders, developer caches, media libraries, old downloads — off your boot drive onto external storage. It replaces each moved directory with a symlink so every app still finds its files exactly where it expects them.

Dry-run by default. Verify before delete. offload is now a Python 3 script, installed with Homebrew `python@3.13`, with no other dependencies.

## The problem

Apple charges $200 to go from 256 GB to 512 GB at purchase. Aftermarket upgrades don't exist. A 2 TB external SSD costs $80.

The data filling your boot drive isn't stuff that needs fast internal storage. It's:

- **Signal, Steam, Obsidian** — app support folders you never think about
- **Xcode DerivedData** — 15 GB of build artifacts that rebuild themselves
- **~/Downloads** — a graveyard of ISOs and PDFs from last year
- **node_modules** — hundreds of copies, one per project
- **Photos, Music, iMovie** — tens of GBs you access a few times a week

offload knows about all of these. It knows which strategy works for each one. And it refuses to touch the ones that would break.

## Quick start

```bash
# 1. Install
brew install sage-is/apps/offload

# 2. Point it at your drives
mkdir -p ~/.config/offload
cat > ~/.config/offload/offload.conf <<EOF
OFFLOAD_HOT=/Volumes/MyFastSSD
OFFLOAD_COLD=/Volumes/MyArchiveHDD
EOF

# 3. See what you're working with
offload status
offload list
```

Not sure which drives to use? Let offload figure it out:

```bash
offload drives              # classify every mounted volume
offload drives --benchmark  # add read-speed measurements
```

## Every drive

`offload drives` prints a table of every mounted volume with its filesystem, SSD status, free space, backup coverage, ownership, encryption, and HOT/COLD/EXCLUDED role. `--benchmark` adds read-speed measurements. The command is read-only and prints config lines to add; it never writes the config file.

HOT and COLD are picked per move, not once for the whole run. A destination must be mounted and eligible, and must keep `OFFLOAD_HEADROOM` free after the move. Network mounts, ExFAT, and removable sticks never qualify. Private data needs an encrypted volume with ownership turned on. An attached encrypted sparsebundle counts as an encrypted drive, unless its file sits on the boot drive.

## How it works

Eight strategies, matched to each kind of data:

| Strategy | What it does | When to use it |
|----------|-------------|----------------|
| **symlink** | rsync to dest, verify file count + bytes, delete source, create symlink | App data, home dirs, dev tools |
| **archive** | rsync to dest, delete source, recreate empty dir | Downloads (timestamped cold storage) |
| **merge** | rsync `--update` into existing dest, delete source | Shared repos, clones |
| **sweep** | Find and delete rebuildable build artifacts in place | node_modules, .venv, target, dist |
| **purge** | Delete entire dir, recreate empty | Xcode caches, npm cache, Homebrew cache |
| **native-ui** | Print instructions only — never auto-moves | Photos, Music, iMovie, Docker |
| **repos** | Bundle clean, pushed, cold git repos to COLD; copy unsafe ones whole | Old projects under ~/Documents/Projects |
| **cloud** | rclone copy to an approved remote, check, then delete source | Cold archives, only with `--upload` |

Every strategy defaults to **dry-run**. Nothing is touched until you say `--apply`.

```bash
offload move Signal           # see what would happen
offload move Signal --apply   # do it
```

Move an entire category at once:

```bash
offload move app   --apply    # all app-support entries
offload move cache --apply    # purge all rebuild-on-demand caches
offload move sweep --apply    # delete all build cruft
```

## plan: advice only

`offload plan` estimates how much space the boot drive needs, where each target would fit, backup coverage, and priced options such as hardware or cloud for any shortfall. It prints advice only: it moves nothing and never touches the network. `offload prices --update` is the opt-in network update for its dated price table.

## What gets offloaded

Everything offload knows about lives in a single list called `OFFLOAD_TARGETS`. Every subcommand reads from this one list. No duplicated knowledge anywhere in the script.

| Category | What's in it | Strategy |
|----------|-------------|----------|
| **app** | Signal, Steam, Obsidian, Keybase, VSCode, Cursor, Epic, Minecraft | symlink |
| **home** | Movies, Music, Pictures, Desktop, .local, .bun, .pyenv, .vscode, .cursor | symlink |
| **cold** | Downloads (timestamped archive to COLD drive) | archive |
| **merge** | Projects/Clones | merge |
| **dev** | CoreSimulator | symlink |
| **cache** | Xcode DerivedData, Xcode Archives, iOS DeviceSupport, ~/.npm, ~/.cache, Homebrew cache | purge |
| **sweep** | node_modules, .venv, target, build, dist, \_\_pycache\_\_, .next | sweep |
| **repos** | Cold git repos under ~/Documents/Projects | repos |
| **protected** | Messages, iOS Backups (requires Admin + Full Disk Access) | symlink |
| **vm** | Docker Desktop | native-ui |
| **media-lib** | Photos Library, Music Library, iMovie Library | native-ui |

### Add your own

Extend the list in `~/.config/offload/offload.conf`:

```bash
OFFLOAD_TARGETS+=(
  "Slack|app|<TH>/Library/Application Support/Slack|HOT/MovedAppData/<U>/Slack|symlink|no|Slack"
)
```

Tokens expand at runtime: `<TH>` (or the older `<TARGET_HOME>`) = target home, `<U>` = username, `<DATE>` = YYYY-MM-DD, `HOT/` and `COLD/` prefix with your configured drive paths. `CLOUD/` uses the first remote in `OFFLOAD_APPROVED_REMOTES`.

## Old git projects: repos and reclone

`offload repos` gives every repository under `~/Documents/Projects` one of four verdicts: **keep** for active repos, **synced** for repos in a sync folder, **reclone** for clean, pushed, cold repos, and **move** for cold repos that are unsafe to remove.

A clean, pushed repo gets a verified `git bundle --all` on COLD, plus a manifest entry, before its local copy is removed. Unsafe repos are copied whole to COLD and verified. `--apply` checks the remote again. Repos inside sync folders are refused unless you pass `--synced-ok`.

Use `offload reclone <path>` or `offload reclone all` to restore repos. Reclone and moved-repo records are kept in the manifest on COLD and in `reclone.tsv`.

## Cloud, only when approved

Custom `CLOUD/` rows use the first rclone remote named in `OFFLOAD_APPROVED_REMOTES`. Cloud moves are opt-in and require both `--apply` and `--upload`. offload copies to the approved remote, checks the copy (with `cryptcheck` for a crypt remote), records it, and only then removes the source.

Restore a cloud archive with `offload restore <Label|path> --apply`. `offload plan` does not access the network. The only network calls are `prices --update` and an approved CLOUD move with `--apply --upload`.

## Helpers

offload needs none of these. `offload plan` detects them and prints recipes. Each one makes an unencrypted, cloud-backed drive hold ciphertext only.

- **rclone** (`brew install rclone`). A `crypt` remote over a folder on a big drive encrypts file contents and names. `rclone nfsmount` (rclone 1.65 or later) shows that store, or a B2 or S3 remote, as a folder for browsing and restores. A mount is never a symlink target.
- **git-annex** (`brew install git-annex`). It records which drive or remote holds each file, and enforces `numcopies` before it drops a local copy. Its `directory` and `rsync` special remotes, or B2 and S3 through `git-annex-remote-rclone`, take `encryption=hybrid`. It suits cold archives and a second copy of repo bundles.
- **Encrypted sparsebundle** (built in). `hdiutil create -type SPARSEBUNDLE -fs APFS -encryption AES-256 -size 200g` makes one on a big drive; `hdiutil attach -owners on` mounts it. offload reads its encryption from `hdiutil info`, so it can take private data.

## Guard launcher

When your data lives on an external drive, launching an app while the drive is unplugged is a problem. offload solves this:

```bash
offload launch Signal
```

If the symlink target volume isn't mounted, offload pops a native macOS dialog telling you to plug in the drive. No silent failures, no corrupt state.

## Safety — poka-yoke discipline

offload is built on [poka-yoke](https://en.wikipedia.org/wiki/Poka-yoke) (ポカヨケ) — the Toyota production principle that bad outcomes should be structurally impossible, not merely unlikely.

| Principle | How offload implements it |
|-----------|-------------------------|
| **Dry-run by default** | Nothing mutates without `--apply`. No destructive default. No `--yes-to-all`. |
| **Verify before delete** | Cross-volume moves rsync to destination, compare file count + total bytes against source, delete source only on exact signature match. |
| **Idempotent** | Re-running on an already-symlinked path skips. Existing destinations are never silently overwritten. |
| **Never sudo from inside** | Permission failures go to `~/offload-admin-todo.txt`. A reviewable wrapper script is emitted at `~/bin/offload-admin-pass`. You read it, then you run it. |
| **Guard launcher** | `offload launch` checks the symlink target before opening the app. Unmounted volume = refusal with a dialog, not a silent crash. |
| **Two-account workflow** | Protected data (Messages, iOS Backups) requires a separate macOS Admin account with Full Disk Access on its Terminal. |

### What offload deliberately won't do

These aren't missing features. They're structural decisions.

- **Relocate $HOME** via `dscl . -change NFSHomeDirectory`. Breaks iCloud Drive, confuses FileVault, and has been broken by multiple macOS updates. We move *contents*, not the pointer.
- **Symlink Apple sandbox containers** (`~/Library/Containers/com.apple.*`). The sandbox kernel resolves real paths and refuses to follow symlinks outside the container.
- **Symlink browser profiles.** Chrome, Firefox, Brave, and Zen do constant SQLite writes. Corruption under symlinks across volumes is well-documented.
- **Auto-move media libraries.** Photos, Music, iMovie, and Final Cut have native "Choose Library" UIs that handle the transition cleanly — index rebuilds, metadata fixups, everything. `offload relocations` prints the steps.

## Commands

| Command | What it does |
|---------|-------------|
| `offload status` | Disk free, target summary, recent log, admin todo |
| `offload list` | Every target with category, strategy, reclaim estimate, and current state |
| `offload list --all-users` | Audit every macOS user account (for Admin use) |
| `offload checklist` | Live progress checklist with exact commands to advance each entry |
| `offload drives` | Classify mounted volumes as HOT / COLD / EXCLUDED |
| `offload drives --benchmark` | Add read-speed measurements (~10 s per drive) |
| `offload plan` | Advice only: boot need, fit, coverage, and priced options |
| `offload prices [--update]` | Show the dated price table; update it only when asked |
| `offload repos [--synced-ok]` | Give git repos a keep, synced, reclone, or move verdict |
| `offload reclone <path>|all [--apply]` | Restore recloned or moved repos |
| `offload restore <Label|path> [--apply]` | Bring a cloud archive back |
| `offload move <Label>` | Dry-run one target |
| `offload move <Label> --apply` | Move it |
| `offload move <category> --apply` | Move every target in a category |
| `offload launch <App>` | Guarded launch — refuses if data drive is unmounted |
| `offload relocations` | Native-UI relocation steps for Docker, Photos, Music, iMovie |
| `offload protected --target-home /Users/X <Label> --apply` | Admin-mode move for protected targets |
| `offload help` | Full embedded reference (the script header) |
| `offload version` | Print version |

## Configuration

```bash
# ~/.config/offload/offload.conf

OFFLOAD_HOT=/Volumes/MyFastSSD
OFFLOAD_COLD=/Volumes/MyArchiveHDD
```

Or export as environment variables — the config file is read, never run. Override the config path with `OFFLOAD_CONF=/path/to/custom.conf`.

Optional settings include `OFFLOAD_DRIVES`, `OFFLOAD_HEADROOM`, `OFFLOAD_BOOT_FREE_PCT`, `OFFLOAD_PRIVATE_LABELS`, `OFFLOAD_REPO_KEEP`, and `OFFLOAD_APPROVED_REMOTES`.

## Admin mode

Some macOS data — Messages, iOS Backups — sits behind permissions your regular account can't access. offload handles this with a two-account workflow:

1. Log into a separate macOS Admin account
2. Grant Full Disk Access to that account's Terminal
3. Run offload with `--target-home` pointing at the regular user's home

```bash
offload protected --target-home /Users/alice Messages --apply
offload --target-home /Users/alice move Signal --apply
```

For a full multi-user audit:

```bash
sudo offload list --all-users
```

This is safe — `list` is read-only. The script itself never invokes `sudo`.

## Install

```bash
brew install sage-is/apps/offload
```

offload is a Python 3 script installed with Homebrew `python@3.13`. It has no other dependencies.

Or run directly from a clone:

```bash
git clone https://github.com/Sage-is/homebrew-apps.git
cd homebrew-apps
./offload help
```

No build step is needed.

## License

[AGPL-3.0-or-later](../LICENSES/AGPL-3.0-or-later.txt)
