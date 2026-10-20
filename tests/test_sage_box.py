"""sage-box: a Mac becomes a Sage.is box in one run, from one file, over SSH, with secrets from the vault."""

import hashlib
import os
import pty
import subprocess
import tempfile
import termios
import time
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "sage-box"
FORMULA = TOOL.parent / "Formula" / "sage-box.rb"

# Stand-ins log each call to $STATE/calls. ssh decodes the script sage-box sent
# (base64 inside the command), appends it to $STATE/remote, saves its stdin to
# $STATE/stdin-N, and answers: the probe from $STATE/probe (the third probe and
# later from $STATE/probe-after when it exists), the newest backup, a restart.
# With $STATE/exec it runs every other script, as the box would, against the
# stand-in git, brew, trellis-crm, ai-ui and docker, and returns the script's
# exit status; $STATE/fail-<step> makes one stand-in fail. curl answers a health
# check, until $STATE/no-health exists. With $STATE/hash-stdin, ssh keeps a
# digest of its stdin and never the bytes, so a test can scan every file for a
# secret. On the box, trellis-crm mailbox writes its argv NUL-separated to
# $STATE/mailbox-argv, a digest of its stdin to $STATE/mailbox-stdin, and exits
# with the code in $STATE/mailbox-exit.
# sage-secret run resolves bw:ITEM, from --env FILE or the environment, to
# secret-ITEM (a mail-* item to one with quotes, spaces and a $), or names a
# missing item; put keeps a digest of the value in $STATE/item-ITEM.
STUBS = {
    "ssh": r'''echo "ssh $*" >> "$STATE/calls"
n=$(( $(cat "$STATE/n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STATE/n"
input="$(cat; printf x)"; input="${input%x}"
if [ -e "$STATE/hash-stdin" ]; then printf '%s' "$input" | shasum -a 256 | cut -d' ' -f1 > "$STATE/stdin-$n"
else printf '%s' "$input" > "$STATE/stdin-$n"; fi
last="${@: -1}"
case "$last" in
  true) left=$(cat "$STATE/down" 2>/dev/null || echo 0); if [ "$left" -gt 0 ]; then echo $((left - 1)) > "$STATE/down"; exit 1; fi; exit 0 ;;
esac
b64="${last#*echo }"; b64="${b64%% | base64*}"
script="$(echo "$b64" | base64 -d 2>/dev/null || echo "$b64" | base64 -D)"
printf '%s\n---\n' "$script" >> "$STATE/remote"
if grep -q 'sudo -S -p' <<< "$script"; then
  head -1 "$STATE/stdin-$n" | grep -qx "secret-pilot-user" || { echo "sudo refused the password from the vault (BOX_SUDO)" >&2; exit 97; }
fi
if grep -q 'echo "arch=' <<< "$script"; then
  p=$(( $(cat "$STATE/probes" 2>/dev/null || echo 0) + 1 )); echo "$p" > "$STATE/probes"
  if [ "$p" -ge 3 ] && [ -f "$STATE/probe-after" ]; then cat "$STATE/probe-after"; else cat "$STATE/probe"; fi
  exit 0
fi
if grep -q 'ls -t' <<< "$script"; then [ -e "$STATE/no-backup" ] || echo "/Users/user/.sage-is/backups/trellis-crm/trellis-20261014-020000.tar.gz"; exit 0; fi
if [ -e "$STATE/exec" ]; then printf '%s' "$input" | bash -c "$(sed 's#/opt/homebrew/bin/brew#/nonexistent/brew#' <<< "$script")"; exit $?; fi
exit 0''',
    "ssh-agent": 'case "${1:-}" in -s) echo "SSH_AUTH_SOCK=/tmp/sage-box-test; export SSH_AUTH_SOCK;" ;; esac; echo "ssh-agent $*" >> "$STATE/calls"',
    "ssh-add": 'echo "ssh-add $*" >> "$STATE/calls"; cat > "$STATE/ssh-key"',
    "scp": 'echo "scp $*" >> "$STATE/calls"; cp "$STATE/archive" "${@: -1}"',
    "age": '''echo "age $*" >> "$STATE/calls"
while [ $# -gt 0 ]; do case "$1" in -r) shift ;; -o) out="$2"; shift ;; *) in="$1" ;; esac; shift; done
cp "$in" "$out"''',
    "launchctl": 'echo "launchctl $*" >> "$STATE/calls"',
    "sleep": "true",
    "curl": '[ ! -e "$STATE/no-health" ] || exit 22',
    "sage-secret": r'''echo "sage-secret $*" >> "$STATE/calls"
value_of() {
  item="${1#bw:}"; item="${item%%/*}"
  [ ! -e "$STATE/missing-$item" ] || { echo "sage-secret: no item named $item that the agent account can see (for $2)" >&2; return 1; }
  case "$item" in mail-*) printf '%s' "secret-$item it's \"quoted\" and spaced \$HOME" ;; *) printf '%s' "secret-$item" ;; esac
}
case "$1" in
  run) shift
       if [ "$1" = --env ]; then
         while IFS= read -r line; do
           case "$line" in ''|'#'*) continue ;; esac
           export "${line%%=*}=${line#*=}"
         done < "$2"
         shift 2
       fi
       [ "$1" != -- ] || shift
       for key in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=bw:.*/\1/p'); do
         val="$(value_of "${!key}" "$key")" || exit 1
         export "$key=$val"
       done
       exec "$@" ;;
  put) item="$2"; shift 2; replace=0
       for a in "$@"; do [ "$a" != --replace ] || replace=1; done
       value="$(cat)"
       [ -n "$value" ] || { echo "Error: the value is empty; nothing stored" >&2; exit 1; }
       if [ -e "$STATE/item-$item" ] && [ $replace = 0 ]; then echo "Error: $item already exists in Agents-edit. Add --replace to change its password" >&2; exit 1; fi
       printf '%s' "$value" | shasum -a 256 | cut -d' ' -f1 > "$STATE/item-$item"
       echo "Stored $item (password) in Agents-edit / Agents. Use it as bw:$item" ;;
  *) exit 1 ;;
esac''',
    "sage-runtime": 'case "${1:-}" in version) echo "sage-runtime 0.1.0" ;; status) echo "Runtimes:"; echo "  colima running" ;; esac',
    "trellis-crm": r'''case "${1:-}" in
  version) echo "trellis-crm 0.1.1 (image x)" ;;
  update) echo "trellis-crm update" >> "$STATE/calls"
          [ ! -e "$STATE/fail-update" ] || { echo "Error: could not pull ghcr.io/sage-is/trellis:latest."; exit 1; }
          echo "Trellis is running at http://localhost:8030" ;;
  mailbox) shift; echo "trellis-crm mailbox $*" >> "$STATE/calls"
           printf '%s\0' "$@" > "$STATE/mailbox-argv"
           case " $* " in
             *" --stdin "*) shasum -a 256 | cut -d' ' -f1 > "$STATE/mailbox-stdin"; echo stored ;;
             *) echo "front-desk  imap.example.test  disabled" ;;
           esac
           exit "$(cat "$STATE/mailbox-exit" 2>/dev/null || echo 0)" ;;
esac''',
    "ai-ui": r'''case "${1:-}" in
  version) echo "ai-ui 3.2.0_2" ;;
  start) echo "ai-ui start" >> "$STATE/calls"
         [ ! -e "$STATE/fail-ai-ui" ] || { echo "Error: the container runtime is not running."; exit 1; }
         echo "AI-UI is running at http://localhost:8080" ;;
esac''',
    "git": r'''echo "git $*" >> "$STATE/calls"
case " $* " in
  *" pull "*) [ ! -e "$STATE/fail-pull" ] || { echo "fatal: Not possible to fast-forward, aborting." >&2; exit 128; }
              echo "Already up to date." ;;
  *" log "*)  echo "  checkout at 28d5622 the newest commit" ;;
esac''',
    "brew": r'''echo "brew $*" >> "$STATE/calls"
case "${1:-}" in upgrade) [ -e "$STATE/quiet-brew" ] || echo "Warning: sage-runtime 0.1.0 already installed" ;; esac''',
    "docker": r'''echo "docker $*" >> "$STATE/calls"
case "${1:-} ${2:-}" in
  "network connect") [ ! -e "$STATE/fail-connect" ] || { echo "Error response from daemon: endpoint with name sage-ai already exists in network sage-network" >&2; exit 1; } ;;
  "network inspect") cat "$STATE/network" 2>/dev/null || exit 1 ;;
esac''',
}

BARE = {"arch": "arm64", "os": "15.6", "brew": "missing", "sage_runtime": "missing", "trellis_crm": "missing",
        "ai_ui": "missing", "vm": "", "context": "", "boot": "no", "backup_daemon": "no", "autorestart": "0",
        "sleep": "1", "ghcr": "no", "deploy_key": "no", "checkout": "none", "source": "", "profile": "",
        "review_at": "", "shift_end": "", "trellis": "down", "aiui": "down", "aiui_net": "no", "filevault": "on", "leftovers": "0"}
DONE = {**BARE, "brew": "/opt/homebrew/bin/brew", "sage_runtime": "0.1.0", "trellis_crm": "0.1.1", "ai_ui": "3.2.0_2",
        "vm": "krunkit", "context": "colima", "boot": "yes", "backup_daemon": "yes", "autorestart": "1", "sleep": "0",
        "ghcr": "yes", "deploy_key": "yes", "checkout": "28d5622", "source": "/Users/user/src/trellis",
        "profile": "realestate", "review_at": "15:00", "shift_end": "18:00", "trellis": "up", "aiui": "up", "aiui_net": "yes"}

BOX = {"BOX_HOST": "100.64.0.9", "BOX_USER": "user", "BOX_SSH_KEY": "bw:pilot-ssh-key", "BOX_SUDO": "bw:pilot-user",
       "BOX_MODE": "source", "BOX_AI_UI": "1", "BOX_CHECKOUT": "/Users/user/src/trellis",
       "BOX_DEPLOY_KEY": "bw:pilot-deploy", "BOX_GHCR_USER": "octo", "BOX_GHCR_TOKEN": "bw:github-packages-read",
       "BOX_PROFILE": "realestate", "BOX_REVIEW_AT": "15:00", "BOX_SHIFT_END": "18:00", "BOX_BACKUP_TO": "age1test"}


class Mac(unittest.TestCase):
    """This Mac, faked: its own home, state folder and stand-in commands."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.root = root
        self.home, self.state, self.bin, self.agents = root / "home", root / "state", root / "bin", root / "LaunchAgents"
        for folder in (self.home, self.state, self.bin, self.agents):
            folder.mkdir()
        for name, body in STUBS.items():
            stub = self.bin / name
            stub.write_text("#!/bin/bash\n" + body + "\n")
            stub.chmod(0o755)
        self.boxes = self.home / ".sage-is" / "boxes"
        self.boxes.mkdir(parents=True)
        self.backups = root / "backups"
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "STATE": str(self.state),
                    "SAGE_BOXES_DIR": str(self.boxes), "LAUNCH_AGENTS_DIR": str(self.agents)}

    def box(self, name="pilot", mode=0o600, **overrides):
        values = {**BOX, "BOX_BACKUP_DIR": str(self.backups), **overrides}
        path = self.boxes / f"{name}.env"
        path.write_text("# a test box\n" + "".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
        path.chmod(mode)
        return path

    def probe(self, fields, after=None):
        (self.state / "probe").write_text("".join(f"{k}={v}\n" for k, v in fields.items()))
        if after:
            (self.state / "probe-after").write_text("".join(f"{k}={v}\n" for k, v in after.items()))

    def run_cli(self, *args):
        return subprocess.run([TOOL, *args], env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def ok(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def remote(self):
        path = self.state / "remote"
        return [s for s in path.read_text().split("\n---\n") if s.strip()] if path.exists() else []

    def stdins(self):
        return "".join(p.read_text() for p in sorted(self.state.glob("stdin-*")))


class Plan(Mac):
    def test_a_dry_run_lists_every_step_and_changes_nothing(self):
        self.box()
        self.probe(BARE)
        out = self.ok("pilot").stdout
        for word in ("tools", "runtime", "power", "boot", "key", "checkout", "registry", "trellis", "ai-ui", "backups"):
            self.assertRegex(out, rf"would +{word} ", out)
        self.assertIn("install Homebrew", out)
        self.assertIn("--apply", out)
        self.assertEqual(len(self.remote()), 1, "only the probe reached the box")
        self.assertTrue(any(c.startswith("ssh-add") for c in self.calls()))
        self.assertEqual((self.state / "ssh-key").read_text().strip(), "secret-pilot-ssh-key")

    def test_a_finished_box_has_nothing_to_change(self):
        self.box()
        self.probe(DONE)
        out = self.ok("pilot").stdout
        self.assertIn("Nothing to change.", out)
        self.assertNotIn("would", out)
        self.assertEqual(len(self.remote()), 1)

    def test_apply_runs_the_steps_in_order_with_every_secret_on_stdin(self):
        self.box()
        self.probe(BARE, after=DONE)
        out = self.ok("pilot", "--apply").stdout
        scripts = self.remote()
        text = "\n".join(scripts)
        order = ["installer -pkg /tmp/Homebrew.pkg", "brew install sage-runtime trellis-crm ai-ui", "sage-runtime use colima",
                 "pmset -a sleep 0 autorestart 1", "trellis-crm boot --print", "cat > $HOME/.ssh/trellis-deploy",
                 "git clone -q git@github.com:Sage-is/trellis.git", "docker login ghcr.io -u octo --password-stdin",
                 "trellis-crm start --profile \"$BOX_PROFILE\"", "ai-ui start", "docker network connect sage-network sage-ai 2>/dev/null || true",
                 "launchctl bootstrap system /Library/LaunchDaemons/is.sage.trellis-backup.plist"]
        positions = [text.index(part) for part in order]
        self.assertEqual(positions, sorted(positions), order)
        self.assertIn("--source /Users/user/src/trellis", text)
        self.assertIn("REVIEW_AT=$BOX_REVIEW_AT", text)
        self.assertNotIn("secret-", text, "a secret landed in a script")
        self.assertNotIn("pipefail", text, "only the update's scripts stop on a failing pipe; the others end in grep | tail")
        stdin = self.stdins()
        for secret in ("secret-pilot-user", "secret-pilot-deploy", "secret-github-packages-read"):
            self.assertIn(secret, stdin)
        self.assertIn("Done. pilot is a Sage.is box.", out)

    def test_image_mode_runs_the_image_alone_and_peer_mode_installs_no_trellis(self):
        self.box(BOX_MODE="image", BOX_CHECKOUT=None, BOX_DEPLOY_KEY=None)
        self.probe(BARE)
        out = self.ok("pilot").stdout
        self.assertNotRegex(out, r"would +(key|checkout) ")
        self.assertIn("--no-source", out)
        self.box(name="laptop", BOX_MODE="peer", BOX_AI_UI="0", BOX_CHECKOUT=None, BOX_DEPLOY_KEY=None,
                 BOX_GHCR_USER=None, BOX_GHCR_TOKEN=None)
        out = self.ok("laptop").stdout
        for word in ("registry", "trellis", "ai-ui", "backups", "key"):
            self.assertNotRegex(out, rf"would +{word} ")
        self.assertRegex(out, r"would +tools +.*brew install sage-runtime trellis-crm \(found")

    def test_a_missing_vault_item_is_named_and_the_box_is_never_reached(self):
        self.box()
        self.probe(BARE)
        (self.state / "missing-pilot-user").touch()
        result = self.run_cli("pilot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no item named pilot-user", result.stderr)
        self.assertEqual(self.remote(), [])

    def test_the_box_file_must_be_private_and_name_absolute_paths(self):
        self.box(mode=0o644)
        result = self.run_cli("pilot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mode 600", result.stderr)
        self.box(BOX_CHECKOUT="~/src/trellis")
        result = self.run_cli("pilot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute path", result.stderr)
        self.assertEqual(self.remote(), [])

    def test_a_refused_sudo_stops_the_run(self):
        self.box(BOX_SUDO="bw:wrong-item")
        self.probe({**BARE, "brew": "/opt/homebrew/bin/brew", "sage_runtime": "0.1.0", "trellis_crm": "0.1.1", "ai_ui": "3.2.0_2", "vm": "krunkit"})
        result = self.run_cli("pilot", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sudo refused the password", result.stderr)

    def test_reboot_waits_for_the_box_to_come_back_and_verifies_again(self):
        self.box()
        self.probe(DONE)
        (self.state / "down").write_text("2")
        out = self.ok("pilot", "--apply", "--reboot").stdout
        self.assertIn("shutdown -r now", "\n".join(self.remote()))
        self.assertIn("Back after", out)
        self.assertIn("Verify after the restart", out)
        result = self.run_cli("pilot", "--reboot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--reboot goes with --apply", result.stderr)


class Upkeep(Mac):
    def test_update_upgrades_pulls_the_checkout_and_updates_trellis(self):
        self.box()
        self.probe(DONE)
        self.ok("pilot", "update")
        scripts = self.remote()
        text = "\n".join(scripts)
        order = ["brew upgrade sage-runtime trellis-crm ai-ui", 'git -C "$BOX_CHECKOUT" pull --ff-only', "trellis-crm update", "ai-ui start",
                 "docker network connect sage-network sage-ai 2>/dev/null || true"]
        positions = [text.index(part) for part in order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(scripts), 5, "four steps, then the probe")
        for script in scripts[:4]:
            self.assertIn("\nset -eo pipefail\n", script, "each step stops the update when it fails, also behind | tail")
        self.assertNotIn("pipefail", scripts[4], "not in prelude, which the probe's grep | tail lines share")

    def test_backup_pulls_the_newest_archive_encrypts_it_and_keeps_fourteen(self):
        self.box()
        (self.state / "archive").write_text("the record")
        self.backups.mkdir()
        for i in range(15):
            old = self.backups / f"trellis-202609{i:02d}-020000.tar.gz.age"
            old.write_text("old")
            stamp = time.time() - 86400 * (20 - i)
            os.utime(old, (stamp, stamp))
        out = self.ok("pilot", "backup").stdout
        new = self.backups / "trellis-20261014-020000.tar.gz.age"
        self.assertEqual(new.read_text(), "the record")
        self.assertEqual(len(list(self.backups.glob("*.age"))), 14)
        self.assertFalse((self.backups / "trellis-20260900-020000.tar.gz.age").exists(), "the oldest went")
        self.assertTrue(any(c.startswith("age -r age1test -o") for c in self.calls()))
        self.assertIn("keeping the last 14", out)

    def test_backup_needs_an_archive_on_the_box_and_the_two_settings(self):
        self.box()
        (self.state / "no-backup").touch()
        result = self.run_cli("pilot", "backup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no backup on the box yet", result.stderr)
        self.box(BOX_BACKUP_TO=None)
        result = self.run_cli("pilot", "backup")
        self.assertIn("BOX_BACKUP_TO", result.stderr)

    def test_backup_nightly_installs_an_agent_on_this_mac(self):
        self.box()
        out = self.ok("pilot", "backup", "--nightly").stdout
        plist = self.agents / "is.sage.box-backup-pilot.plist"
        text = plist.read_text()
        self.assertIn("<string>pilot</string><string>backup</string>", text)
        self.assertIn("<key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer>", text)
        self.assertIn(str(plist), out)
        self.assertTrue(any("launchctl bootstrap gui/" in c for c in self.calls()))

    def test_status_reads_the_box_once(self):
        self.box()
        self.probe(DONE)
        out = self.ok("pilot", "status").stdout
        self.assertIn("trellis   up, profile realestate, from /Users/user/src/trellis", out)
        self.assertIn("vm        krunkit", out)
        self.assertEqual(len(self.remote()), 1)

    def test_local_reports_this_mac_without_ssh(self):
        out = self.ok("local").stdout
        self.assertIn("sage-runtime  sage-runtime 0.1.0", out)
        self.assertIn("trellis-crm   trellis-crm 0.1.1", out)
        self.assertEqual(self.remote(), [])


class OnTheBox(Mac):
    """The update's scripts and the AI-UI start run here as the box would: against stand-in git, brew, trellis-crm, ai-ui and docker."""

    def setUp(self):
        super().setUp()
        self.box()
        self.probe(DONE)
        (self.state / "exec").touch()

    def stand_in_fails(self, step):
        (self.state / f"fail-{step}").touch()

    def stops_at(self, step, label, shown, never):
        self.stand_in_fails(step)
        result = self.run_cli("pilot", "update")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(shown, result.stdout, "the failing step's own last line reaches the terminal")
        self.assertIn(f"the update stopped at {label} on the box", result.stderr)
        self.assertNotIn("Verify:", result.stdout)
        calls = "\n".join(self.calls())
        for later in never:
            self.assertNotIn(later, calls)

    def test_a_clean_update_runs_the_steps_in_order_and_joins_sage_ai_to_the_network(self):
        out = self.ok("pilot", "update").stdout
        calls = "\n".join(self.calls())
        order = ["brew upgrade sage-runtime trellis-crm ai-ui", "git -C /Users/user/src/trellis pull --ff-only", "trellis-crm update",
                 "ai-ui start", "docker network connect sage-network sage-ai"]
        positions = [calls.index(part) for part in order]
        self.assertEqual(positions, sorted(positions), calls)
        self.assertIn("checkout at 28d5622", out)
        self.assertIn("network   sage-ai is on sage-network", out)

    def test_a_pull_that_cannot_fast_forward_stops_the_update(self):
        self.stops_at("pull", "git pull", "fatal: Not possible to fast-forward", ["trellis-crm update", "ai-ui start", "network connect"])

    def test_a_failed_trellis_update_stops_the_update(self):
        self.stops_at("update", "trellis-crm update", "Error: could not pull", ["ai-ui start", "network connect"])

    def test_a_failed_ai_ui_start_stops_the_update_before_the_reconnect(self):
        self.stops_at("ai-ui", "ai-ui start", "Error: the container runtime", ["network connect"])

    def test_a_quiet_brew_upgrade_does_not_stop_the_update(self):
        (self.state / "quiet-brew").touch()
        self.ok("pilot", "update")
        self.assertIn("trellis-crm update", self.calls())

    def test_a_sage_ai_already_on_the_network_does_not_stop_the_update(self):
        self.stand_in_fails("connect")
        self.ok("pilot", "update")
        self.assertIn("docker network connect sage-network sage-ai", self.calls())

    def test_apply_joins_sage_ai_to_the_network_after_it_starts_ai_ui(self):
        self.probe({**DONE, "aiui": "down", "aiui_net": "no"}, after=DONE)
        out = self.ok("pilot", "--apply").stdout
        calls = self.calls()
        self.assertRegex(out, r"doing +ai-ui ")
        self.assertLess(calls.index("ai-ui start"), calls.index("docker network connect sage-network sage-ai"))
        self.assertIn("Done. pilot is a Sage.is box.", out)

    def test_apply_goes_on_when_sage_ai_is_already_on_the_network(self):
        self.stand_in_fails("connect")
        self.probe({**DONE, "aiui": "down"}, after=DONE)
        out = self.ok("pilot", "--apply").stdout
        self.assertIn("docker network connect sage-network sage-ai", self.calls())
        self.assertIn("Done. pilot is a Sage.is box.", out)


class Network(Mac):
    def probe_lines(self):
        """The probe's two lines about AI-UI as sage-box sends them: does it answer, then is sage-ai on sage-network."""
        self.box()
        self.probe(DONE)
        self.ok("pilot")
        lines = self.remote()[0].splitlines()
        health = next(l for l in lines if "8080/health" in l)
        net = next(l for l in lines if l.startswith('echo "aiui_net='))
        self.assertLess(lines.index(health), lines.index(net), "the network line reads $aiui, which the line before it sets")
        return health, net

    def run_here(self, script):
        return subprocess.run(["/bin/bash", "-c", script], env=self.env, capture_output=True, text=True, timeout=30)

    def test_the_probe_asks_whether_sage_ai_is_on_sage_network(self):
        _, line = self.probe_lines()
        for names, want in (("trellis-crm\nsage-ai\n", "yes"), ("trellis-crm\n", "no"),
                            ("sage-ai-dev\ntrellis-crm\n", "no"), (None, "no")):
            with self.subTest(running_on_the_network=names):
                if names is None:
                    (self.state / "network").unlink(missing_ok=True)
                else:
                    (self.state / "network").write_text(names)
                result = self.run_here(f"aiui=up; {line}")
                self.assertEqual(result.stdout.strip(), f"aiui_net={want}", result.stderr)

    def test_the_probe_does_not_ask_the_engine_while_sage_ai_is_down(self):
        _, line = self.probe_lines()
        (self.state / "network").write_text("trellis-crm\nsage-ai\n")
        result = self.run_here(f"aiui=down; {line}")
        self.assertEqual(result.stdout.strip(), "aiui_net=no", result.stderr)
        self.assertEqual([c for c in self.calls() if c.startswith("docker")], [], "a half-started engine holds a docker call open for minutes")

    def test_the_network_line_follows_the_answer_of_the_aiui_line(self):
        health, line = self.probe_lines()
        (self.state / "network").write_text("trellis-crm\nsage-ai\n")
        for answers, want in ((True, ["aiui=up", "aiui_net=yes"]), (False, ["aiui=down", "aiui_net=no"])):
            with self.subTest(health_check_answers=answers):
                (self.state / "no-health").unlink(missing_ok=True)
                if not answers:
                    (self.state / "no-health").touch()
                result = self.run_here(f"{health}\n{line}")
                self.assertEqual(result.stdout.split(), want, result.stderr)

    def test_verify_fails_when_sage_ai_is_off_sage_network(self):
        self.box()
        self.probe({**DONE, "aiui_net": "no"})
        result = self.run_cli("pilot", "update")
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stdout, r"network +sage-ai is not on sage-network \(on the box: docker network connect sage-network sage-ai\)")
        self.assertIn("something is down after the update", result.stderr)

    def test_a_probe_that_gives_no_answer_counts_as_off_the_network(self):
        self.box()
        self.probe({k: v for k, v in DONE.items() if k != "aiui_net"})
        result = self.run_cli("pilot", "update")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sage-ai is not on sage-network", result.stdout)
        self.assertNotIn("unbound variable", result.stderr)

    def test_apply_fails_its_verify_when_sage_ai_is_off_sage_network(self):
        self.box()
        self.probe(DONE, after={**DONE, "aiui_net": "no"})
        result = self.run_cli("pilot", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("something is still down", result.stderr)

    def test_verify_judges_the_network_only_while_ai_ui_is_up(self):
        self.box()
        self.probe({**DONE, "aiui": "down", "aiui_net": "no"})
        result = self.run_cli("pilot", "update")
        self.assertNotEqual(result.returncode, 0, "AI-UI down fails the run on its own")
        self.assertRegex(result.stdout, r"ai-ui +down")
        self.assertNotRegex(result.stdout, r"(?m)^ +network ", "no network remedy while AI-UI itself is down")
        self.assertIn("something is down after the update", result.stderr)

    def test_a_peer_box_has_no_sage_network_to_judge(self):
        # A peer box runs no Trellis, and only trellis-crm makes sage-network.
        self.box(BOX_MODE="peer", BOX_AI_UI="1", BOX_CHECKOUT=None, BOX_DEPLOY_KEY=None, BOX_GHCR_USER=None, BOX_GHCR_TOKEN=None)
        self.probe({**DONE, "aiui": "up", "aiui_net": "no"})
        for args in (("update",), ("--apply",)):
            with self.subTest(args=args):
                out = self.ok("pilot", *args).stdout
                self.assertRegex(out, r"ai-ui +up")
                self.assertNotRegex(out, r"(?m)^ +network ")

    def test_a_box_without_ai_ui_is_not_asked_about_sage_ai(self):
        self.box(BOX_AI_UI="0")
        self.probe({**DONE, "aiui": "up", "aiui_net": "no"})  # something answers on :8080, but this box does not run AI-UI
        out = self.ok("pilot", "update").stdout
        self.assertNotIn("sage-ai", out)


MAIL_SECRET = "secret-mail-front it's \"quoted\" and spaced $HOME"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Mailbox(Mac):
    """trellis-crm mailbox on the box: arguments as typed, the password on stdin alone, the exit code back."""

    def setUp(self):
        super().setUp()
        self.box()
        self.probe(DONE)
        for marker in ("exec", "hash-stdin"):
            (self.state / marker).touch()

    def typed(self, *args, answer):
        """Run sage-box with stdin a terminal that types `answer`. Typed ahead into a terminal that echoes, the
        echo would wait for a reader, and read -s blocks until it drains (tcsetattr TCSADRAIN): turn echo off."""
        terminal, typist = pty.openpty()
        attrs = termios.tcgetattr(typist)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(typist, termios.TCSANOW, attrs)
        os.write(terminal, answer.encode())
        try:
            return subprocess.run([TOOL, *args], env=self.env, capture_output=True, text=True, stdin=typist, timeout=60)
        finally:
            os.close(terminal)
            os.close(typist)

    def box_argv(self):
        path = self.state / "mailbox-argv"
        return path.read_text().split("\0")[:-1] if path.exists() else None

    def assert_nowhere(self, secret, result):
        """Not in what sage-box printed, not in any command line a stand-in saw, not in any file of the test."""
        self.assertNotIn(secret, result.stdout + result.stderr)
        for call in self.calls():
            self.assertNotIn(secret, call)
        files = [p for p in self.root.rglob("*") if p.is_file()]
        self.assertTrue(files)
        for path in files:
            self.assertNotIn(secret.encode(), path.read_bytes(), path)

    def test_arguments_reach_the_box_as_typed(self):
        args = ["add", "Front desk", "--host", "imap.example.test", "--username", "o'neil \"desk\"@example.test",
                "--read-from", "2026-10-01", "--mode", "bcc"]
        out = self.ok("pilot", "mailbox", *args).stdout
        self.assertEqual(self.box_argv(), args)
        self.assertIn("front-desk", out)
        self.ok("pilot", "mailbox", "list")
        self.assertEqual(self.box_argv(), ["list"])

    def test_the_exit_code_comes_back(self):
        for code in (0, 1, 2):
            with self.subTest(code=code):
                (self.state / "mailbox-exit").write_text(str(code))
                self.assertEqual(self.run_cli("pilot", "mailbox", "check", "front desk").returncode, code)

    def test_from_resolves_the_item_here_and_the_password_reaches_the_box_on_stdin(self):
        result = self.ok("pilot", "mailbox", "secret", "front desk", "--from", "bw:mail-front/password")
        self.assertEqual(self.box_argv(), ["secret", "front desk", "--stdin"])
        self.assertEqual((self.state / "mailbox-stdin").read_text().strip(), digest(MAIL_SECRET))
        self.assertIn("stored", result.stdout)
        runs = [c for c in self.calls() if c.startswith("sage-secret run")]
        self.assertEqual(len(runs), 2, "the box file, then the one item")
        self.assertEqual(runs[1], "sage-secret run -- /bin/sh -c printf \"%s\" \"$SAGE_BOX_MAILBOX_SECRET\"",
                         "the reference rides in the child's environment, not on sage-secret's command line")
        self.assert_nowhere(MAIL_SECRET, result)

    def test_from_takes_only_a_vault_reference_and_never_echoes_what_it_got(self):
        result = self.run_cli("pilot", "mailbox", "secret", "front", "--from", "hunter2 typed here")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bw:ITEM", result.stderr)
        self.assertNotIn("hunter2", result.stdout + result.stderr)
        result = self.run_cli("pilot", "mailbox", "secret", "front", "hunter2")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("hunter2", result.stdout + result.stderr)
        self.assertEqual(self.remote(), [])

    def test_a_missing_item_stops_before_the_box(self):
        (self.state / "missing-mail-front").touch()
        result = self.run_cli("pilot", "mailbox", "secret", "front", "--from", "bw:mail-front")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no item named mail-front", result.stderr)
        self.assertIsNone(self.box_argv())

    def test_prompt_with_vault_stores_the_password_then_sends_it(self):
        password = "Tr0ub4dor & 'horse' \"staple\" $PATH"
        result = self.typed("pilot", "mailbox", "secret", "front desk", "--prompt", "--vault", "mail-front-desk",
                            answer=f"{password}\n{password}\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        put = calls.index("sage-secret put mail-front-desk --stdin")
        self.assertLess(put, calls.index("trellis-crm mailbox secret front desk --stdin"))
        self.assertEqual((self.state / "item-mail-front-desk").read_text().strip(), digest(password))
        self.assertEqual((self.state / "mailbox-stdin").read_text().strip(), digest(password))
        self.assertIn("Use it as bw:mail-front-desk", result.stdout)
        self.assert_nowhere(password, result)

    def test_prompt_alone_sends_without_the_vault(self):
        result = self.typed("pilot", "mailbox", "secret", "front", "--prompt", answer="s3cret pw\ns3cret pw\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse([c for c in self.calls() if c.startswith("sage-secret put")])
        self.assertEqual((self.state / "mailbox-stdin").read_text().strip(), digest("s3cret pw"))
        self.assert_nowhere("s3cret pw", result)

    def test_two_entries_that_differ_change_nothing(self):
        result = self.typed("pilot", "mailbox", "secret", "front", "--prompt", "--vault", "mail-front",
                            answer="first try\nsecond try\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differ", result.stderr)
        self.assertFalse([c for c in self.calls() if c.startswith("sage-secret put")])
        self.assertIsNone(self.box_argv())
        self.assert_nowhere("first try", result)

    def test_an_item_that_exists_needs_replace(self):
        (self.state / "item-mail-front").write_text("an older value\n")
        result = self.typed("pilot", "mailbox", "secret", "front", "--prompt", "--vault", "mail-front", answer="new pw\nnew pw\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--replace", result.stderr)
        self.assertIsNone(self.box_argv(), "a refused vault write sends nothing to the box")
        result = self.typed("pilot", "mailbox", "secret", "front", "--prompt", "--vault", "mail-front", "--replace",
                            answer="new pw\nnew pw\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sage-secret put mail-front --stdin --replace", self.calls())
        self.assertEqual((self.state / "mailbox-stdin").read_text().strip(), digest("new pw"))

    def test_a_refused_push_says_the_vault_holds_it(self):
        (self.state / "mailbox-exit").write_text("1")
        result = self.typed("pilot", "mailbox", "secret", "front", "--prompt", "--vault", "mail-front", answer="pw 1\npw 1\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("pilot mailbox secret front --from bw:mail-front", result.stderr)

    def test_prompt_needs_a_terminal(self):
        result = self.run_cli("pilot", "mailbox", "secret", "front", "--prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--from bw:ITEM", result.stderr)
        self.assertEqual(self.remote(), [])

    def test_clear_goes_straight_through(self):
        self.ok("pilot", "mailbox", "secret", "front desk", "--clear")
        self.assertEqual(self.box_argv(), ["secret", "front desk", "--clear"])

    def test_one_way_in_at_a_time(self):
        for args in ((), ("--from", "bw:mail-front", "--clear"), ("--clear", "--vault", "mail-front"), ("--prompt", "--replace")):
            with self.subTest(args=args):
                result = self.run_cli("pilot", "mailbox", "secret", "front", *args)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote(), [])

    def test_a_peer_box_has_no_mailboxes(self):
        self.box(BOX_MODE="peer", BOX_CHECKOUT=None, BOX_DEPLOY_KEY=None, BOX_GHCR_USER=None, BOX_GHCR_TOKEN=None)
        result = self.run_cli("pilot", "mailbox", "list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("peer box", result.stderr)
        self.assertEqual(self.remote(), [])


class Shape(unittest.TestCase):
    def test_the_formula_installs_the_tool_beside_its_lib_and_the_example(self):
        text = FORMULA.read_text()
        for line in ('libexec.install "sage-box"', '(libexec/"lib").install "lib/sage-runtime.sh"',
                     'bin.write_exec_script libexec/"sage-box"', 'man1.install "sage-box.1"',
                     '(pkgshare/"boxes").install "docs/boxes/example.env"', 'depends_on "age"'):
            self.assertIn(f"  {line}\n", text, line)

    def test_the_example_names_no_host_login_or_secret(self):
        example = (TOOL.parent / "docs" / "boxes" / "example.env").read_text()
        self.assertIn("BOX_SSH_KEY=bw:", example)
        self.assertNotIn("100.109", example)
        self.assertNotIn("realtor", example.lower())


if __name__ == "__main__":
    unittest.main()
