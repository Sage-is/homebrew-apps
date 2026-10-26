# Box access

A box is a Mac that runs Sage.is apps for a team. We reach it over SSH on the tailnet. This page is the standard for every box: which key does what, how a person opens a short window, and what the box itself enforces. `sage-box.1` has the commands.

## The doors

| door | who | key | what it can do |
|---|---|---|---|
| read | Agents, any time | `<box>-read-key` in the vault | Five read verbs, and the box holds it to them: `probe`, `backup`, `power`, `containers`, `version`. |
| backup | The nightly pull on an operator Mac | `<box>-backup-key` in the vault | rsync, read-only, in the box's staging folder alone. |
| window | Agents, after a person unlocks | A certificate from a person's FIDO CA | Admin work for 1 to 4 hours. |
| me | People | Their own FIDO key, never in the vault | Admin work as the person. |
| legacy | Agents and people, until windows work on the box | `BOX_SSH_KEY` and `BOX_SUDO` from the vault | Admin work, with a warning on every run. A new box never gets it. |

Admin verbs (`--apply`, `update`, `mailbox`, `manage`, `tunnel install`) take the first open door: a valid window, then `--as-me`, then legacy. With none, the run stops before it asks the vault for anything: `locked: run sage-box <box> unlock`.

## The read key

The read key's line in the box user's `~/.ssh/authorized_keys`:

```text
restrict,from="<operators>",command="/usr/local/libexec/sage-box/read" ssh-ed25519 AAAA… sage-box:read:<box>
```

| verb | what it prints |
|---|---|
| `probe` | What the dry run reads: tool versions, VM, power settings, whether each app answers, FileVault on or off, and digests of what step access installed. |
| `backup` | The last 50 lines of the backup log, the backup folder's dates, sizes and names, the staging folder's database date and size and its artifact count, and the LaunchDaemon's state. |
| `power` | `pmset -g`, `pmset -g ps`, `uptime`, and last boot. |
| `containers` | Container names and status only. |
| `version` | The dispatcher's version and macOS's. |

sshd sends every login with that key to the read dispatcher. `restrict` turns off forwarding and terminals, and `from=` holds the key to the operators' addresses. The dispatcher belongs to root, so the box user cannot change it. It matches the request whole against the five verbs. It takes no arguments and never evaluates the text; anything else gets `refused` and exit 2.

It never prints env files, databases, backup contents, mail or app logs. The probe reads four named settings from trellis-crm's settings file (source, profile, review time, shift end), and only whether a registry login or a deploy key exists. Output stops at 64 KiB. The box logs every call, refused ones too, with the caller's address: `log show --predicate 'process == "logger"' --last 1d`.

`sage-box <box> read VERB` and `sage-box <box> status` resolve the read key alone from the vault. ssh runs with `-F none` and `IdentitiesOnly=yes`, and its one `IdentityFile` is the read key's public half; your `~/.ssh/config` would offer `id_rsa` beside it otherwise. A box that refuses the key gets a message saying how to install it. Nothing falls back to the admin key.

## The backup key

The backup key's line in the box user's `~/.ssh/authorized_keys`:

```text
restrict,from="<operators>",command="/Users/<user>/.sage-is/bin/sage-box-rrsync -ro /Users/<user>/.sage-is/backups/trellis-crm/current" ssh-ed25519 AAAA… sage-box:backup:<box>
```

- At 02:00 the box refreshes that folder: `trellis-crm backup --dir`. The database is a checked copy from SQLite's backup API, kept as it was when nothing changed. `artifacts/` gains only the artifacts it lacks; they are content-addressed, so a name never changes its bytes.
- `sage-box <box> backup` pulls the folder with brew's rsync into a dated snapshot. Only what changed crosses the network. Every unchanged file is a hard link to the snapshot before (`--link-dest`), so it takes no space twice.
- rrsync holds the key to reading that one folder: no writes, no deletes, no `..`, no other first word than `rsync`. `restrict` and `from=` work as on the read key.
- The line runs `sage-box-rrsync`, a short script step keys writes beside the line. Brew's rrsync starts `/usr/bin/rsync`, which on macOS is openrsync, and openrsync refuses the `--confine-root` that rrsync adds for a folder. The script runs brew's rrsync with brew's rsync in its place, and reads brew's rrsync at each pull, so an upgrade reaches it. The box user owns both, as it owns every line in the file.
- The pull runs with `-F none`, `IdentitiesOnly=yes` and the key's public half as its one `IdentityFile`, like the read key. It passes no `--rsync-path`: rrsync refuses one, and the script already picks brew's rsync.
- This key reads every record the box keeps. That is its job. Keep it in `Agents` like the read key, and never reuse it as another door's key; sage-box refuses the read key in its place.
- Snapshots live in an encrypted APFS sparse bundle on the operator's Mac (`BOX_BACKUP_DIR`). Each pull attaches it with `hdiutil attach -stdinpass` at `~/.sage-is/box-backups/<box>` and detaches it at the end. Its passphrase is `<box>-backup-sparse` in the vault, and `sage-box <box> backup --init` makes both.
- Thinning: every snapshot of the last 2 days, then the newest of each day to 30 days, then the newest of each week to a year. A pull that breaks off leaves `<time>.partial`, which the next pull finishes; `latest` moves only after a pull ends well.
- `rsync_tmbackup.sh`, which the Mac's own snapshots use, is not used here. With a remote source it runs nothing over ssh but rsync, so rrsync would take it. But its ssh is fixed, with `StrictHostKeyChecking=no` and `UserKnownHostsFile=/dev/null`, so it checks no host key and cannot hold the pull to one key. Its `--strategy` sets a variable nothing reads, and it moves `latest` after a failed run.

## The window

- `sage-box <box> unlock` opens a window for 2 hours. `--hours N` takes 1 to 4; anything else is refused, never trimmed.
- It makes a fresh key in `~/.sage-is/unlock/<box>/` (folder 700, key 600), and your FIDO CA (`SAGE_BOX_CA`) signs it: one touch.
- The certificate names the principal `sage-window`, the key ID `<you>-<box>-<UTC time>` and the `BOX_OPERATORS` addresses. It allows no forwarding and no terminal.
- Each box has this CA line for every trusted CA:

```text
restrict,cert-authority,principals="sage-window",from="<operators>" sk-ssh-ed25519@openssh.com AAAA… sage-box:ca:<name>
```

- The box checks the principal, the addresses and the expiry, not this Mac. sshd logs the key ID on every login: that is the audit trail.
- Admin verbs use the window while it is valid. `status` shows `window open until 14:32` or `locked`.
- Run `sage-box <box> lock` to delete the window key and close its shared connection.
- Each admin door shares one connection per run (`ControlMaster`), with its own socket in `~/.sage-is/ssh/`, so a run costs one login and one touch. A window's connection lives no longer than the window.

A window grants what the box user can do, data included. That is why windows are short, named and logged.

## What the box holds

`sage-box <box> --apply` keeps these in step. Each step runs only when the box differs.

- **Step access, root's files.** The read dispatcher `/usr/local/libexec/sage-box/read` and the root helper `/usr/local/libexec/sage-box/root`, both root:wheel 755, and the sudoers drop-in `/etc/sudoers.d/sage-box`, root:wheel 440.
- The drop-in lets the box user run the helper as root without a password, one verb per entry: `homebrew`, `power`, `boot-daemon`, `backup-daemon`, `tunnel-daemon`, `docker-sock`, `reboot`. Nothing else. The helper takes no other input and writes every file it installs from its own text.
- `tunnel-daemon` installs `/Library/LaunchDaemons/is.sage.tunnel.plist` for the tunnel `BOX_TUNNEL` named when the helper went in. launchd runs it as the box user at power-on and keeps it alive. bash sources `~/.sage-is/tunnels/<name>.env` and starts `cloudflared tunnel --no-autoupdate run` with `TUNNEL_TOKEN` from `CLOUDFLARED_TUNNEL_TOKEN`: the token never sits in the plist or on a command line. `sage-box <box> tunnel install --env-file PATH` sends the file on stdin, mode 600, then runs the verb. A box without a tunnel name gets no plist, and the verb refuses.
- The drop-in goes in only after `visudo -cf` passes it, and stays only while `visudo -c` passes. The step refuses if a folder above the helper is a link or writable by anyone but root.
- Step access takes the login password once: from the vault on the legacy door, typed at a terminal on the others. An agent in a window cannot change root's files.
- **Step keys, the box user's lines.** The read key's line, the backup key's line with its `sage-box-rrsync` script when the box file names a backup key, and one line per CA in `~/.sage-is/ca/` on this Mac. Every line tagged `sage-box:` is replaced; every other line stays, in its order. A copy of one of these keys without its tag goes too, since sshd takes the first line that matches. A dated copy of the file comes first, and a second run changes nothing. No password: an agent in a window can add a CA or a new read key.

## Setting it up

1. **[MANUALLY]** Check FIDO support: `ssh-keygen -t ed25519-sk -f /tmp/sk-test`. If Apple's ssh refuses the key type, `brew install openssh` on each admin Mac. Delete the test key.
2. **[MANUALLY]** Make a CA on each device: `ssh-keygen -t ed25519-sk -O resident -O verify-required -O application=ssh:sage-box-ca -f ~/.ssh/sage-box-ca`. On a U2F-only device use `-t ecdsa-sk` without `-O verify-required`. Make a second CA on the spare device.
3. **[MANUALLY]** Copy each `.pub` to `~/.sage-is/ca/<name>.pub` (for example `alex.pub`, `alex-spare.pub`) on every operator Mac. Keep the same set everywhere: a run from a Mac that lacks a CA removes it from the box.
4. **[MANUALLY]** `export SAGE_BOX_CA=~/.ssh/sage-box-ca` in your shell profile.
5. **[MANUALLY]** Your own admin key, one per device: `ssh-keygen -t ed25519-sk -O resident -O verify-required`. Add its public half to the box user's `authorized_keys` with `from="100.64.0.0/10",verify-required` in front. Tailscale SSH stays off on boxes: it skips `authorized_keys`.
6. **[MANUALLY]** Make an ed25519 key with no passphrase for the box's read key. Store its private half in `Agents` as `<box>-read-key`, the way `<box>-ssh-key` is stored. Delete the local copy.
7. **[WE]** Add `BOX_READ_KEY=bw:<box>-read-key` and `BOX_OPERATORS=100.64.0.0/10` (or the operators' own addresses) to the box file. `docs/boxes/example.env` shows both.
8. **[MANUALLY]** At a terminal: `sage-box <box> --as-me --apply`. It asks for the box's login password once. On a box that still has the legacy key, plain `--apply` takes `BOX_SUDO` from the vault.
9. **[WE]** `sage-box <box> status` reads through the read key, and `sage-box <box> read nope` is refused.
10. **[MANUALLY]** `sage-box <box> unlock --hours 1`, one touch. Then **[WE]** `sage-box <box> update` runs inside the window, and the box's log shows the key ID.
11. **[MANUALLY]** Retire the legacy key once windows work: remove `BOX_SSH_KEY` and `BOX_SUDO` from the box file and the legacy key's line from the box, and move `<box>-user` to a humans-only collection.

For the nightly pull:

1. **[MANUALLY]** Make a second ed25519 key with no passphrase and store it in `Agents` as `<box>-backup-key`. Delete the local copy.
2. **[WE]** Add `BOX_BACKUP_KEY`, `BOX_BACKUP_SPARSE` and `BOX_BACKUP_DIR` to the box file, as `docs/boxes/example.env` shows.
3. **[WE]** `sage-box <box> backup --init` makes the encrypted bundle and stores its passphrase with `sage-secret put`.
4. **[WE]** `sage-box <box> update`, then `sage-box <box> --apply` inside a window: brew's rsync on the box, the backup key's line, and the 02:00 daemon with `--dir`. The apply stops first if the box's trellis-crm has no `backup --dir`.
5. **[WE]** `sage-box <box> backup` once, then `sage-box <box> backup --nightly` for 03:30.

## Limits

- The nightly pull runs unattended with the backup key. It can read every record in the staging folder and nothing else.
- A window is everything the box user can do, plus the root helper's verbs. Its limits are time, addresses and the log.
- `BOX_OPERATORS` is only as tight as the tailnet: keep the tailnet ACL to operators reaching boxes on port 22.

## For the client

We look after your box from our own computers, over a private network. We use four kinds of key.

The first key only reads. It can ask your box five fixed questions: is everything running, did last night's backup work, how is the power, which programs are up, which version is installed. It cannot read your mail, your contacts, your files or the backups themselves. Your box answers no other question to this key, and it writes down every question: when it came, and from where.

The second key opens a short window, at most four hours. One of us must unlock it with a physical security key and a touch. Inside the window we can update and repair the box. Your box ends the window on time and logs whose window it was.

The third key belongs to one of us and lives on a physical security key. We use it when we sit down to fix something with you.

The fourth key copies each night's backup to one of our own computers, where it stays encrypted. It can read that one backup folder and nothing else, and it cannot change a thing on your box.

Your login password stays with people. Once windows work on your box, no program of ours holds it.
