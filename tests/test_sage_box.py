"""sage-box: a Mac becomes a Sage.is box in one run, from one file, over SSH, with secrets from the vault."""

import base64
import hashlib
import os
import pty
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import termios
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "sage-box"
FORMULA = TOOL.parent / "Formula" / "sage-box.rb"

# Stand-ins log each call to $STATE/calls. ssh decodes the script sage-box sent
# (base64 inside the command), appends it to $STATE/remote, saves its stdin to
# $STATE/stdin-N, and answers: the probe from $STATE/probe (the third probe and
# later from $STATE/probe-after when it exists), the newest backup, a restart.
# A read verb instead of a script is the read key's call: ssh logs it to
# $STATE/reads and answers as the box's dispatcher would (the probe, or
# $STATE/read-VERB), or refuses the key while $STATE/read-refused exists. ssh -O
# (a control command) only logs. ssh-add keeps the key it got in $STATE/ssh-key
# and appends it to $STATE/ssh-added; -L derives its public half. ssh-keygen is
# the real one, but -s signs with the test's own CA ($STATE/test-ca), standing
# in for the FIDO device; $STATE/no-touch makes it fail. sudo logs, swallows a
# -S password line, copies the access step's folder to $STATE/installed, and
# refuses the root helper while $STATE/no-helper exists. logger logs.
# With $STATE/exec it runs every other script, as the box would, against the
# stand-in git, brew, trellis-crm, ai-ui and docker, and returns the script's
# exit status; $STATE/fail-<step> makes one stand-in fail. curl answers a health
# check, until $STATE/no-health exists. With $STATE/hash-stdin, ssh keeps a
# digest of its stdin and never the bytes, so a test can scan every file for a
# secret. On the box, trellis-crm mailbox writes its argv NUL-separated to
# $STATE/mailbox-argv, a digest of its stdin to $STATE/mailbox-stdin, and exits
# with the code in $STATE/mailbox-exit.
# sage-secret run resolves bw:ITEM, from --env FILE or the environment, to
# secret-ITEM (a mail-* item to one with quotes, spaces and a $, a *-read-key
# item to the real key in $STATE/read-key), logs each setting it resolved to
# $STATE/resolved, or names a missing item; put keeps a digest of the value in
# $STATE/item-ITEM.
STUBS = {
    "ssh": r'''echo "ssh $*" >> "$STATE/calls"
for a in "$@"; do [ "$a" != -O ] || exit 0; done
n=$(( $(cat "$STATE/n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STATE/n"
input="$(cat; printf x)"; input="${input%x}"
if [ -e "$STATE/hash-stdin" ]; then printf '%s' "$input" | shasum -a 256 | cut -d' ' -f1 > "$STATE/stdin-$n"
else printf '%s' "$input" > "$STATE/stdin-$n"; fi
answer_probe() {
  p=$(( $(cat "$STATE/probes" 2>/dev/null || echo 0) + 1 )); echo "$p" > "$STATE/probes"
  if [ "$p" -ge 3 ] && [ -f "$STATE/probe-after" ]; then cat "$STATE/probe-after"; else cat "$STATE/probe"; fi
}
last="${@: -1}"
case "$last" in
  true) left=$(cat "$STATE/down" 2>/dev/null || echo 0); if [ "$left" -gt 0 ]; then echo $((left - 1)) > "$STATE/down"; exit 1; fi; exit 0 ;;
  probe|backup|power|containers|version)
    echo "$last" >> "$STATE/reads"
    [ ! -e "$STATE/read-refused" ] || { echo "user@100.64.0.9: Permission denied (publickey)." >&2; exit 255; }
    if [ "$last" = probe ]; then answer_probe; else cat "$STATE/read-$last" 2>/dev/null || echo "the box's $last"; fi
    exit 0 ;;
esac
b64="${last#*echo }"; b64="${b64%% | base64*}"
script="$(echo "$b64" | base64 -d 2>/dev/null || echo "$b64" | base64 -D)"
printf '%s\n---\n' "$script" >> "$STATE/remote"
if grep -q 'sudo -S -p' <<< "$script"; then
  head -1 "$STATE/stdin-$n" | grep -qx "secret-pilot-user" || { echo "sudo refused the password from the vault (BOX_SUDO)" >&2; exit 97; }
fi
if grep -q 'echo "arch=' <<< "$script"; then answer_probe; exit 0; fi
if grep -q 'ls -t' <<< "$script"; then [ -e "$STATE/no-backup" ] || echo "/Users/user/.sage-is/backups/trellis-crm/trellis-20261014-020000.tar.gz"; exit 0; fi
if [ -e "$STATE/exec" ]; then printf '%s' "$input" | bash -c "$(sed 's#/opt/homebrew/bin/brew#/nonexistent/brew#' <<< "$script")"; exit $?; fi
exit 0''',
    "ssh-agent": 'case "${1:-}" in -s) echo "SSH_AUTH_SOCK=/tmp/sage-box-test; export SSH_AUTH_SOCK;" ;; esac; echo "ssh-agent $*" >> "$STATE/calls"',
    "ssh-add": r'''echo "ssh-add $*" >> "$STATE/calls"
case " $* " in
  *" -L "*) /usr/bin/ssh-keygen -y -f "$STATE/ssh-key" ;;
  *) umask 077; cat > "$STATE/ssh-key"; { cat "$STATE/ssh-key"; echo "---"; } >> "$STATE/ssh-added" ;;
esac''',
    "ssh-keygen": r'''echo "ssh-keygen $*" >> "$STATE/calls"
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    -s) [ ! -e "$STATE/no-touch" ] || { echo "sign_and_send_pubkey: signing failed: device not found" >&2; exit 1; }
        args+=(-s "$STATE/test-ca"); shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
exec /usr/bin/ssh-keygen "${args[@]}"''',
    "sudo": r'''echo "sudo $*" >> "$STATE/calls"
case " $* " in *" -S "*) IFS= read -r _ || true ;; esac
case " $* " in
  *" -n /usr/local/libexec/sage-box/root "*) [ ! -e "$STATE/no-helper" ] || { echo "sudo: a password is required" >&2; exit 1; } ;;
  *" /bin/bash "*) mkdir -p "$STATE/installed"; cp "${@: -1}"/* "$STATE/installed/" ;;
esac
exit 0''',
    "logger": 'echo "logger $*" >> "$STATE/calls"',
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
  case "$item" in
    mail-*) printf '%s' "secret-$item it's \"quoted\" and spaced \$HOME" ;;
    *-read-key) cat "$STATE/read-key" ;;
    *) printf '%s' "secret-$item" ;;
  esac
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
         echo "$key" >> "$STATE/resolved"
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
       "BOX_READ_KEY": "bw:pilot-read-key", "BOX_OPERATORS": "100.64.0.0/10",
       "BOX_MODE": "source", "BOX_AI_UI": "1", "BOX_CHECKOUT": "/Users/user/src/trellis",
       "BOX_DEPLOY_KEY": "bw:pilot-deploy", "BOX_GHCR_USER": "octo", "BOX_GHCR_TOKEN": "bw:github-packages-read",
       "BOX_PROFILE": "realestate", "BOX_REVIEW_AT": "15:00", "BOX_SHIFT_END": "18:00", "BOX_BACKUP_TO": "age1test"}


# Real keys, made once: the box's read key (the vault item pilot-read-key), and
# a plain CA the ssh-keygen stand-in signs with in place of the FIDO device.
KEYS = {}
HELPER = "/usr/local/libexec/sage-box/root"
DISPATCHER = "/usr/local/libexec/sage-box/read"
READ_VERBS = ["probe", "backup", "power", "containers", "version"]
ROOT_VERBS = ["homebrew", "power", "boot-daemon", "backup-daemon", "docker-sock", "reboot"]


def sha16(data):
    return hashlib.sha256(data).hexdigest()[:16]


def sk_pub(seed, comment="alex-ca"):
    """An ed25519-sk public key line (the FIDO CA's .pub), built from its wire format: no device needed."""
    def field(b):
        return struct.pack(">I", len(b)) + b
    blob = field(b"sk-ssh-ed25519@openssh.com") + field(hashlib.sha256(seed.encode()).digest()) + field(b"ssh:")
    return f"sk-ssh-ed25519@openssh.com {base64.b64encode(blob).decode()} {comment}\n"


def setUpModule():
    folder = Path(tempfile.mkdtemp())
    KEYS["folder"] = folder
    for name in ("read", "ca"):
        subprocess.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"test {name}", "-f", str(folder / name)], check=True)
    KEYS["read"] = (folder / "read").read_text()
    KEYS["read_pub"] = " ".join((folder / "read.pub").read_text().split()[:2])
    # A finished box holds what --apply puts there: the probe's access and keys lines are digests of it.
    mac = Mac()
    mac.setUp()
    try:
        mac.box()
        mac.probe(DONE)
        mac.ok("pilot", "--apply")
        files = mac.payloads()
        DONE["access"] = sha16(files["read"] + files["root"])
        DONE["keys"] = sha16(files["lines"])
    finally:
        mac.doCleanups()


def tearDownModule():
    shutil.rmtree(KEYS["folder"], ignore_errors=True)


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
        (self.state / "read-key").write_text(KEYS["read"])
        shutil.copy(KEYS["folder"] / "ca", self.state / "test-ca")
        (self.state / "test-ca").chmod(0o600)
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

    def lines_of(self, name):
        path = self.state / name
        return path.read_text().splitlines() if path.exists() else []

    def reads(self):
        """The read verbs the read key asked for."""
        return self.lines_of("reads")

    def resolved(self):
        """The box file settings sage-secret resolved from the vault."""
        return self.lines_of("resolved")

    def ssh_calls(self):
        return [c for c in self.calls() if c.startswith("ssh ")]

    def payloads(self):
        """What step access and step keys would put on the box, decoded from the scripts they sent:
        read, root, sudoers and install (the access step's files), and lines (the tagged authorized_keys lines)."""
        files = {}
        for script in self.remote():
            for b64, name in re.findall(r"^echo '([A-Za-z0-9+/=]+)' \| base64 -d > \"\$D/(\w+)\"$", script, re.M):
                files[name] = base64.b64decode(b64)
            for b64 in re.findall(r"^INSTALL=\"\$\(echo '([A-Za-z0-9+/=]+)' \| /usr/bin/base64 -d\)\"$", script, re.M):
                files["install"] = base64.b64decode(b64)
            for b64 in re.findall(r"^  echo ([A-Za-z0-9+/=]+) \| base64 -d$", script, re.M):
                files["lines"] = base64.b64decode(b64)
        return files

    def ca(self, name="alex", seed=None):
        """A FIDO CA's public key in ~/.sage-is/ca, as every operator Mac keeps them."""
        folder = self.home / ".sage-is" / "ca"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.pub"
        path.write_text(sk_pub(seed or name))
        return path

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


class Plan(Mac):
    def test_a_dry_run_lists_every_step_and_changes_nothing(self):
        self.box()
        self.probe(BARE)
        out = self.ok("pilot").stdout
        for word in ("access", "keys", "tools", "runtime", "power", "boot", "key", "checkout", "registry", "trellis", "ai-ui", "backups"):
            self.assertRegex(out, rf"would +{word} ", out)
        self.assertIn("install Homebrew", out)
        self.assertIn("--apply", out)
        self.assertIn("through the read key", out)
        self.assertEqual(self.remote(), [], "no script reached the box")
        self.assertEqual(self.reads(), ["probe"], "the read key's probe, and nothing else")
        self.assertEqual((self.state / "ssh-key").read_text().strip(), KEYS["read"].strip())

    def test_a_finished_box_has_nothing_to_change(self):
        self.box()
        self.probe(DONE)
        out = self.ok("pilot").stdout
        self.assertIn("Nothing to change.", out)
        self.assertNotIn("would", out)
        self.assertEqual(self.remote(), [])
        self.assertEqual(self.reads(), ["probe"])

    def test_apply_runs_the_steps_in_order_with_every_secret_on_stdin(self):
        self.box()
        self.probe(BARE, after=DONE)
        out = self.ok("pilot", "--apply").stdout
        scripts = self.remote()
        text = "\n".join(scripts)
        order = ["sudo -S -p", 'F="$HOME/.ssh/authorized_keys"', f"sudo -n {HELPER} homebrew", "brew install sage-runtime trellis-crm ai-ui",
                 "sage-runtime use colima", f"sudo -n {HELPER} power", f"sudo -n {HELPER} boot-daemon", "cat > $HOME/.ssh/trellis-deploy",
                 "git clone -q git@github.com:Sage-is/trellis.git", "docker login ghcr.io -u octo --password-stdin",
                 "trellis-crm start --profile \"$BOX_PROFILE\"", "ai-ui start", "docker network connect sage-network sage-ai 2>/dev/null || true",
                 f"sudo -n {HELPER} backup-daemon"]
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
        (self.state / "missing-pilot-read-key").touch()
        result = self.run_cli("pilot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no item named pilot-read-key", result.stderr)
        self.assertEqual(self.remote(), [])
        self.assertEqual(self.reads(), [])

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
        self.assertIn(f"sudo -n {HELPER} reboot", "\n".join(self.remote()))
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
        self.assertIn("window    locked", out)
        self.assertEqual(self.remote(), [])
        self.assertEqual(self.reads(), ["probe"], "one read, through the read key")

    def test_status_says_whether_the_nightly_backup_is_loaded_and_fresh(self):
        # A plist on disk once read as "nightly backup yes" while launchd had never loaded it.
        self.box()
        for found, want in (({"backup_daemon": "yes", "backup_age": "5"}, "nightly   loaded (02:00); newest backup 5 h old"),
                            ({"backup_daemon": "yes", "backup_age": "30"}, "WARNING: the newest backup is 30 h old"),
                            ({"backup_daemon": "unloaded", "backup_age": "none"}, "WARNING: installed but not loaded"),
                            ({"backup_daemon": "no"}, "WARNING: no backup on the box yet")):
            with self.subTest(found=found):
                self.probe({**DONE, **found})
                self.assertIn(want, self.ok("pilot", "status").stdout)

    def test_apply_loads_a_backup_daemon_that_launchd_never_loaded(self):
        self.box()
        self.probe({**DONE, "backup_daemon": "unloaded"}, after=DONE)
        out = self.ok("pilot", "--apply").stdout
        self.assertRegex(out, r"doing +backups ")
        self.assertIn(f"sudo -n {HELPER} backup-daemon", "\n".join(self.remote()))

    def test_tool_constants_never_take_the_box_file_prefix(self):
        # load_box clears every BOX_ variable before it reads a box file, so a constant named BOX_... vanishes.
        lines = TOOL.read_text().splitlines()
        first_function = next(i for i, line in enumerate(lines) if re.match(r"^[a-z_]+\(\) \{", line))
        constants = [line for line in lines[:first_function] if re.match(r"^BOX_[A-Z_]+=", line)]
        self.assertEqual(constants, [])

    def test_status_reports_the_ups(self):
        self.box()
        self.probe({**DONE, "ups": "Back-UPS ES 600, 87%, on AC"})
        self.assertIn("ups       Back-UPS ES 600, 87%, on AC", self.ok("pilot", "status").stdout)

    def test_status_calls_a_silent_probe_unknown(self):
        self.box()
        self.probe(DONE)
        self.assertIn("ups       unknown", self.ok("pilot", "status").stdout)

    def test_local_reports_this_mac_without_ssh(self):
        out = self.ok("local").stdout
        self.assertIn("sage-runtime  sage-runtime 0.1.0", out)
        self.assertIn("trellis-crm   trellis-crm 0.1.1", out)
        self.assertEqual(self.remote(), [])


PMSET_NONE = "Now drawing from 'AC Power'\n"
PMSET_AC = "Now drawing from 'AC Power'\n -Back-UPS ES 600 (id=1234567)\t87%; charging; 0:20 remaining present: true\n"
PMSET_BATTERY = "Now drawing from 'UPS Power'\n -Back-UPS ES 600 (id=1234567)\t54%; discharging; 0:12 remaining present: true\n"
PMSET_HOSTILE = "Now drawing from 'AC Power'\n -Evil=UPS,\"$(x)\" (id=9)\t10%; charging; present: true\n"


class UpsProbe(Mac):
    """The ups_state function the probe ships to the box, run here against canned `pmset -g ps` output."""

    def ups(self, pmset_output):
        self.box()
        self.probe(DONE)
        self.ok("pilot", "--as-me")  # a dry run through an admin door sends the probe as a script
        script = self.remote()[0]
        start = script.index("ups_state () ")
        function = script[start:script.index("\n}\n", start) + 3]
        result = subprocess.run(["bash", "-c", function + "\nups_state"], input=pmset_output,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('echo "ups=$(pmset -g ps 2>/dev/null | ups_state)"', script)
        return result.stdout

    def test_no_ups_is_none(self):
        self.assertEqual(self.ups(PMSET_NONE), "none\n")

    def test_a_laptop_battery_is_not_a_ups(self):
        self.assertEqual(self.ups("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=4)\t95%; discharging; 4:00 remaining present: true\n"), "none\n")

    def test_a_ups_on_ac(self):
        self.assertEqual(self.ups(PMSET_AC), "Back-UPS ES 600, 87%, on AC\n")

    def test_a_ups_on_battery(self):
        self.assertEqual(self.ups(PMSET_BATTERY), "Back-UPS ES 600, 54%, on battery\n")

    def test_garbage_and_silence_are_unknown(self):
        for text in ("", "pmset: command not found\n", "\x00\xff garbage\n", " -Back-UPS (id=1)\t87%\n"):
            self.assertEqual(self.ups(text), "unknown\n", repr(text))

    def test_a_hostile_name_cannot_break_the_line(self):
        self.assertEqual(self.ups(PMSET_HOSTILE), "EvilUPSx, 10%, on AC\n")


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
        self.ok("pilot", "--as-me")
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

class ReadKey(Mac):
    """The read door: the read key alone, from the vault, answered by the box's dispatcher."""

    def setUp(self):
        super().setUp()
        self.box()
        self.probe(DONE)

    def test_read_verbs_never_load_admin_material(self):
        # sage-secret stops on any item it cannot read: with the admin items gone, read and status still work.
        for item in ("pilot-ssh-key", "pilot-user", "pilot-deploy", "github-packages-read"):
            (self.state / f"missing-{item}").touch()
        (self.state / "read-power").write_text("== pmset -g\n sleep 0\n")
        self.assertIn("sleep 0", self.ok("pilot", "read", "power").stdout)
        self.assertIn("window    locked", self.ok("pilot", "status").stdout)
        self.ok("pilot")
        self.assertEqual(set(self.resolved()), {"BOX_READ_KEY"})
        self.assertNotIn("secret-", (self.state / "ssh-added").read_text(), "only the read key reached an agent")
        self.assertEqual(self.remote(), [], "no script: no admin door was opened")
        self.assertEqual(self.reads(), ["power", "probe", "probe"])

    def test_only_the_read_key_is_offered(self):
        self.ok("pilot", "read", "version")
        [call] = self.ssh_calls()
        self.assertIn("ssh -F none ", call, "no ssh_config: it would offer id_rsa beside the read key")
        self.assertIn("-o BatchMode=yes -o IdentitiesOnly=yes", call)
        self.assertEqual(len(re.findall("IdentityFile=", call)), 1)
        path = re.search(r'IdentityFile="([^"]+)"', call).group(1)
        self.assertEqual(Path(path).read_text().split()[:2], KEYS["read_pub"].split(), "the public half of the read key")
        self.assertEqual(call.split()[-2:], ["user@100.64.0.9", "version"], "the verb alone: no script, no arguments")
        self.assertNotIn("ControlMaster", call)
        self.assertEqual((self.state / "ssh-key").read_text().strip(), KEYS["read"].strip())

    def test_read_takes_one_known_verb(self):
        for args in (("cat",), (), ("probe", "extra"), ("probe;id",), ("PROBE",)):
            with self.subTest(args=args):
                result = self.run_cli("pilot", "read", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("read takes one verb", result.stderr)
        self.assertEqual(self.ssh_calls(), [])
        self.assertEqual(self.resolved(), [], "a refused verb reaches neither the vault nor the box")

    def test_a_box_without_the_read_key_says_how_and_never_falls_back(self):
        (self.state / "read-refused").touch()
        for args in (("read", "probe"), ("status",), ()):
            with self.subTest(args=args):
                result = self.run_cli("pilot", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("sage-box pilot --as-me --apply", result.stderr)
                self.assertIn("Nothing fell back to the admin key", result.stderr)
        self.assertEqual(set(self.resolved()), {"BOX_READ_KEY"})
        self.assertEqual(self.remote(), [])

    def test_a_box_file_without_a_read_key_says_what_to_add(self):
        self.box(BOX_READ_KEY=None)
        result = self.run_cli("pilot", "status")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BOX_READ_KEY=bw:pilot-read-key", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_a_secret_in_the_box_file_is_refused(self):
        self.box(BOX_READ_KEY="-----BEGIN OPENSSH PRIVATE KEY-----")
        result = self.run_cli("pilot", "status")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BOX_READ_KEY", result.stderr)
        self.assertIn("must be a vault reference", result.stderr)
        self.assertNotIn("BEGIN", result.stderr)

    def test_the_box_answer_cannot_name_a_variable_of_ours(self):
        marker = self.root / "evaluated"
        self.probe({**DONE, f"x[$(touch {marker})]": "1", "trellis": "up"})
        self.ok("pilot", "status")
        self.assertFalse(marker.exists())

    def test_a_box_name_cannot_walk_paths(self):
        for name in ("../pilot", "pi/lot", "-pilot", ".pilot"):
            result = self.run_cli(name, "status")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("a box name is", result.stderr)


class OnTheBoxAccess(Mac):
    """The read dispatcher, the root helper and the sudoers drop-in as step access writes them, run or checked here.
    The two scripts run with /bin/bash, the box's bash 3.2, with stand-ins first on their PATH."""

    def setUp(self):
        super().setUp()
        self.box()
        self.probe(BARE, after=DONE)
        self.ok("pilot", "--apply")
        self.files = self.payloads()
        for name, body in (("pmset", 'echo "pmset $*" >> "$STATE/calls"; echo " sleep 0"'),
                           ("fdesetup", "echo FileVault is On."), ("launchctl", 'echo "launchctl $*" >> "$STATE/calls"')):
            (self.bin / name).write_text(f"#!/bin/bash\n{body}\n")
            (self.bin / name).chmod(0o755)
        self.before = len(self.calls())

    def script(self, name):
        path = self.root / name
        path.write_text(self.files[name].decode().replace("\nPATH=/", f"\nPATH={self.bin}:/", 1))
        return path

    def dispatch(self, command):
        env = {**self.env, "SSH_CLIENT": "100.64.0.5 51234 22"}
        if command is not None:
            env["SSH_ORIGINAL_COMMAND"] = command
        return subprocess.run(["/bin/bash", str(self.script("read"))], env=env, capture_output=True, timeout=60)

    def new_calls(self):
        return self.calls()[self.before:]

    def test_the_dispatcher_refuses_anything_but_its_verbs(self):
        marker = self.root / "ran"
        for command in (None, "", "rm -rf /", "probe; id", "probe extra", "PROBE", f"$(touch {marker})",
                        f"`touch {marker}`", "probe\nid", "../read", "verb_probe"):
            with self.subTest(command=command):
                result = self.dispatch(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(b"refused", result.stderr)
                self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists(), "nothing the caller sent ran")
        logged = self.new_calls()
        self.assertEqual(len(logged), 11)
        for line in logged:
            self.assertTrue(line.startswith("logger -t sage-box-read refused="), line)
            self.assertRegex(line, r"^logger -t sage-box-read refused=[A-Za-z0-9._?-]* client=100\.64\.0\.5 51234 22$",
                             "the log holds harmless characters only")

    def test_the_dispatcher_caps_its_output(self):
        (self.bin / "pmset").write_text("#!/bin/bash\nhead -c 200000 /dev/zero | tr '\\0' x\n")
        result = self.dispatch("power")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), 65536)

    def test_each_verb_answers_and_every_call_is_logged(self):
        for verb in READ_VERBS:
            with self.subTest(verb=verb):
                result = self.dispatch(verb)
                self.assertEqual(result.returncode, 0, result.stderr)
        logged = [c for c in self.new_calls() if c.startswith("logger")]
        self.assertEqual(logged, [f"logger -t sage-box-read verb={v} client=100.64.0.5 51234 22" for v in READ_VERBS])
        probe = self.dispatch("probe").stdout.decode()
        for key in ("arch", "trellis", "ups", "access", "keys"):
            self.assertRegex(probe, rf"(?m)^{key}=")
        self.assertIn("sage-box-read 0.", self.dispatch("version").stdout.decode())

    def test_the_dispatcher_probes_with_the_same_text_as_an_admin_door(self):
        self.ok("pilot", "--as-me")
        script = self.remote()[-1]
        body = script[script.index('echo "arch='):]
        self.assertIn("verb_probe() {\n" + body + "\n}", self.files["read"].decode())

    def test_the_root_helper_refuses_anything_but_its_verbs(self):
        for args in ((), ("nope",), ("power", "extra"), ("--help",), ("POWER",), ("power;id",), ("",), ("verb_power",)):
            with self.subTest(args=args):
                result = subprocess.run(["/bin/bash", str(self.script("root")), *args], env=self.env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("refused", result.stderr)
        self.assertEqual(self.new_calls(), [], "nothing ran")

    def test_the_sudoers_drop_in_names_only_the_helper_and_passes_visudo(self):
        rules = [line for line in self.files["sudoers"].decode().splitlines() if line.strip() and not line.startswith("#")]
        self.assertEqual(len(rules), 1, rules)
        user, commands = rules[0].split(" ALL=(root) NOPASSWD: ")
        self.assertEqual(user, "user")
        self.assertEqual(commands.split(", "), [f"{HELPER} {verb}" for verb in ROOT_VERBS], "the helper, one verb each")
        self.assertEqual(re.findall(r"(?m)^  ([a-z-]+)\) verb_", self.files["root"].decode()), ROOT_VERBS, "the helper's verbs are sudoers' verbs")
        visudo = shutil.which("visudo", path="/usr/sbin:/usr/bin:/sbin:/bin")
        if not visudo:
            self.skipTest("no visudo here")
        path = self.root / "sage-box.sudoers"
        path.write_bytes(self.files["sudoers"])
        result = subprocess.run([visudo, "-cf", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_root_installs_only_copies_that_match_the_digests_on_its_own_command_line(self):
        [script] = [s for s in self.remote() if "sudo -S" in s]
        found = re.search(r'sage-box-access "\$D" ([0-9a-f]{64}) ([0-9a-f]{64}) ([0-9a-f]{64})$', script, re.M)
        self.assertIsNotNone(found, "the digests ride on sudo's command line")
        digests = found.groups()
        self.assertEqual(digests, tuple(hashlib.sha256(self.files[name]).hexdigest() for name in ("read", "root", "sudoers")))
        self.assertNotIn('"$D/install"', script, "the installer never sits in a file the box user could change")

        def install(folder):
            return subprocess.run(["/bin/bash", "-c", self.files["install"].decode(), "sage-box-access", str(folder), *digests],
                                  capture_output=True, text=True, timeout=60)

        for case, swap in (("as written", None), ("root swapped after it was written", b"\nid\n")):
            with self.subTest(case=case):
                folder = self.root / f"D-{'swapped' if swap else 'kept'}"
                folder.mkdir()
                for name in ("read", "root", "sudoers"):
                    (folder / name).write_bytes(self.files[name])
                if swap:
                    (folder / "root").write_bytes(self.files["root"] + swap)
                    result = install(folder)
                    self.assertEqual(result.returncode, 3, result.stderr)
                    self.assertIn("access: root changed after it was written; nothing installed", result.stderr)
                else:
                    # Not root here, so it stops at the first install; what matters is that every check passed.
                    self.assertNotIn("changed after it was written", install(folder).stderr)

    def test_root_installs_the_drop_in_only_after_visudo(self):
        install = self.files["install"].decode()
        order = ['check sudoers "$4"', f"/usr/local/libexec /usr/local/libexec/sage-box", 'install -m 755 -o root -g wheel "$own/root"', 'visudo -cf "$tmp"',
                 'install -m 440 -o root -g wheel "$tmp" "$SUDOERS_FILE"', "visudo -c >/dev/null"]
        install = install.replace('"$LIBEXEC_DIR"', "/usr/local/libexec/sage-box")
        positions = [install.index(part) for part in order]
        self.assertEqual(positions, sorted(positions))

    def test_the_box_side_scripts_pass_bash_3_and_shellcheck(self):
        for name in ("read", "root", "install"):
            with self.subTest(name=name):
                path = self.script(name)
                result = subprocess.run(["/bin/bash", "-n", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                if shutil.which("shellcheck", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin"):
                    result = subprocess.run([shutil.which("shellcheck", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin"),
                                             "-S", "warning", "-s", "bash", str(path)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout)


class Keys(Mac):
    """Step keys on the box, run here: the tagged lines replaced, every other line as it was, a second run the same."""

    def setUp(self):
        super().setUp()
        self.box()
        (self.state / "exec").touch()
        self.ssh = self.home / ".ssh"
        self.ssh.mkdir(mode=0o700)
        self.auth = self.ssh / "authorized_keys"

    def apply(self):
        self.probe({**DONE, "keys": "stale"}, after=DONE)
        return self.ok("pilot", "--apply")

    def test_the_lines_are_idempotent_and_other_lines_stay(self):
        self.ca("alex")
        self.ca("alex-spare")
        read_b64 = KEYS["read_pub"].split()[1]
        person = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPersonsOwnKeyPersonsOwnKeyPersonsOwnKey person@laptop"
        comment = "# a note that mentions sage-box: keep it"
        self.auth.write_text("\n".join([person, f"ssh-ed25519 {read_b64} pasted by hand",
                                        "ssh-ed25519 AAAAold sage-box:read:pilot", comment]) + "\n")
        self.auth.chmod(0o644)
        self.apply()
        first = self.auth.read_text()
        self.apply()
        self.assertEqual(self.auth.read_text(), first, "a second apply changes nothing")
        lines = first.splitlines()
        self.assertEqual(lines[:2], [person, comment], "other lines as they were, in their order")
        tagged = [line for line in lines if line.split()[-1].startswith("sage-box:")]
        self.assertEqual([line.split()[-1] for line in tagged], ["sage-box:read:pilot", "sage-box:ca:alex-spare", "sage-box:ca:alex"])
        self.assertEqual(len(lines), 5)
        self.assertEqual(sum(read_b64 in line for line in lines), 1, "the read key once: an untagged copy would be a full login")
        self.assertEqual(tagged[0], f'restrict,from="100.64.0.0/10",command="{DISPATCHER}" {KEYS["read_pub"]} sage-box:read:pilot')
        for line in tagged[1:]:
            self.assertTrue(line.startswith('restrict,cert-authority,principals="sage-window",from="100.64.0.0/10" sk-ssh-ed25519@openssh.com '), line)
        self.assertEqual(stat.S_IMODE(self.auth.stat().st_mode), 0o600)
        self.assertEqual(len(list(self.ssh.glob("authorized_keys.sage-box-*"))), 1, "one dated copy; the second run changed nothing")
        # The probe's keys line, run on this file, gives the digest step keys wants: the next dry run says ok.
        probe = self.remote()[0]
        line = next(l for l in probe.splitlines() if l.startswith('echo "keys='))
        result = subprocess.run(["/bin/bash", "-c", line], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "keys=" + sha16("".join(l + "\n" for l in tagged).encode()))
        self.assertEqual(result.stdout.strip(), "keys=" + sha16(self.payloads()["lines"]))

    def test_a_box_without_authorized_keys_gets_one(self):
        self.apply()
        self.assertEqual(self.auth.read_text().splitlines()[0].split()[-1], "sage-box:read:pilot")
        self.assertEqual(stat.S_IMODE(self.auth.stat().st_mode), 0o600)
        self.assertEqual(list(self.ssh.glob("authorized_keys.sage-box-*")), [])

    def test_a_ca_that_is_not_a_fido_key_stops_the_run(self):
        folder = self.home / ".sage-is" / "ca"
        folder.mkdir(parents=True)
        (folder / "laptop.pub").write_text((KEYS["folder"] / "ca.pub").read_text())
        self.probe(DONE)
        for args in ((), ("--apply",)):
            with self.subTest(args=args):
                result = self.run_cli("pilot", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("only FIDO CAs", result.stderr)
        self.assertFalse(self.auth.exists())


class Access(Mac):
    """Step access: root's files go in with the login password once, typed by a person or, on the legacy door, from the vault."""

    def test_legacy_sends_the_vault_password_on_stdin_once(self):
        self.box()
        self.probe({**DONE, "access": "none"}, after=DONE)
        out = self.ok("pilot", "--apply").stdout
        self.assertRegex(out, r"doing +access ")
        [script] = [s for s in self.remote() if "sudo -S" in s]
        self.assertEqual(script.count('| /usr/bin/sudo -S -p ""'), 2, "the password checked alone, then the installer, both from one stdin line")
        self.assertNotRegex(script, r"(^|[|;&] *)sudo ", "where the password meets root, sudo goes by its full path")
        self.assertNotIn("sudo -n", script)
        self.assertNotIn("secret-", "\n".join(self.remote()))
        self.assertIn("secret-pilot-user", self.stdins())

    def test_other_doors_need_the_password_typed_at_a_terminal(self):
        self.box(BOX_SSH_KEY=None, BOX_SUDO=None)
        self.probe({**DONE, "access": "none"}, after=DONE)
        result = self.run_cli("pilot", "--as-me", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("typed at a terminal", result.stderr)
        self.assertFalse([s for s in self.remote() if "sudo -S" in s], "no password, no attempt")
        result = self.typed("pilot", "--as-me", "--apply", answer="secret-pilot-user\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("BOX_SUDO", self.resolved())
        self.assertIn("secret-pilot-user", self.stdins(), "it reached the box on stdin")
        self.assertNotIn("secret-pilot-user", "\n".join(self.remote()) + result.stdout + result.stderr)

    def test_a_box_without_the_helper_says_how(self):
        self.box()
        self.probe({**DONE, "sleep": "1"}, after=DONE)
        (self.state / "exec").touch()
        (self.state / "no-helper").touch()
        result = self.run_cli("pilot", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the root helper did not run power", result.stderr)
        self.assertIn("sage-box pilot --as-me --apply", result.stderr)


class Window(Mac):
    """unlock signs a short certificate with the person's FIDO CA; admin verbs use it, and stop without it."""

    def setUp(self):
        super().setUp()
        self.box(BOX_SSH_KEY=None, BOX_SUDO=None)
        self.probe(DONE)
        self.ca("alex")
        handle = self.home / ".ssh" / "sage-box-ca"
        handle.parent.mkdir(mode=0o700)
        handle.write_text("a FIDO key handle: the private half stays on the device\n")
        handle.chmod(0o600)
        Path(f"{handle}.pub").write_text(sk_pub("alex"))
        self.env["SAGE_BOX_CA"] = str(handle)
        self.window = self.home / ".sage-is" / "unlock" / "pilot"

    def signs(self):
        return [c for c in self.calls() if c.startswith("ssh-keygen") and " -s " in c]

    def test_unlock_refuses_hours_outside_one_to_four_and_never_trims(self):
        for hours in ("0", "5", "6", "2h", "", "01", "-1", "4.5", "10", " 2", "two"):
            with self.subTest(hours=hours):
                result = self.run_cli("pilot", "unlock", "--hours", hours)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("refused, not trimmed", result.stderr)
        self.assertFalse(self.window.exists())
        self.assertEqual(self.signs(), [])

    def test_unlock_signs_one_to_four_hours_for_sage_window_only(self):
        for hours in ("1", "2", "3", "4", None):
            with self.subTest(hours=hours):
                args = ("--hours", hours) if hours else ()
                out = self.ok("pilot", "unlock", *args).stdout
                sign = self.signs()[-1]
                self.assertIn(f"-s {self.env['SAGE_BOX_CA']} ", sign)
                self.assertRegex(sign, r" -I [^ ]+-pilot-\d{8}T\d{6}Z ")
                self.assertIn(f" -n sage-window -V +{hours or 2}h -O clear -O source-address=100.64.0.0/10 ", sign)
                until = datetime.strptime(re.search(r"window open until (\d\d:\d\d)", out).group(1), "%H:%M")
                want = datetime.now() + timedelta(hours=int(hours or 2))
                self.assertLessEqual(abs((until.hour * 60 + until.minute) - (want.hour * 60 + want.minute)) % 1439, 1)
        self.assertEqual(stat.S_IMODE(self.window.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.window / "key").stat().st_mode), 0o600)
        self.assertEqual(self.resolved(), [], "unlock asks the vault for nothing")
        self.assertIn("window    open until", self.ok("pilot", "status").stdout)

    def test_unlock_needs_a_fido_ca_the_boxes_trust(self):
        pub = Path(self.env["SAGE_BOX_CA"] + ".pub")
        pub.write_text((KEYS["folder"] / "ca.pub").read_text())
        self.assertIn("FIDO device", self.run_cli("pilot", "unlock").stderr)
        pub.write_text(sk_pub("someone else"))
        self.assertIn("no box trusts it", self.run_cli("pilot", "unlock").stderr)
        del self.env["SAGE_BOX_CA"]
        self.assertIn("SAGE_BOX_CA names", self.run_cli("pilot", "unlock").stderr)
        (self.state / "no-touch").touch()
        self.env["SAGE_BOX_CA"] = str(pub)[:-4]
        pub.write_text(sk_pub("alex"))
        result = self.run_cli("pilot", "unlock")
        self.assertIn("stays locked", result.stderr)
        self.assertFalse((self.window / "key").exists(), "no half-made window")
        self.assertEqual(len(self.signs()), 1)

    def test_admin_verbs_use_the_window_alone_over_one_shared_connection(self):
        self.ok("pilot", "unlock", "--hours", "1")
        out = self.ok("pilot", "update").stdout
        self.assertIn("inside the window until", out)
        calls = [c for c in self.ssh_calls() if "bash -c" in c]
        self.assertEqual(len(calls), 5)
        for call in calls:
            self.assertIn("-o BatchMode=yes -o IdentitiesOnly=yes -o IdentityAgent=none", call)
            self.assertIn(f'IdentityFile="{self.window}/key"', call)
            self.assertIn(f'CertificateFile="{self.window}/key-cert.pub"', call)
            self.assertIn(f'-o ControlMaster=auto -o ControlPath="{self.home}/.sage-is/ssh/window-%C"', call)
            persist = int(re.search(r"ControlPersist=(\d+)", call).group(1))
            self.assertTrue(0 < persist <= 600, persist)
        self.assertEqual(self.resolved(), [], "a window needs nothing from the vault")
        self.assertFalse((self.state / "ssh-added").exists(), "no agent: the certificate alone")

    def test_admin_verbs_stop_when_locked(self):
        for args in (("update",), ("--apply",), ("backup",), ("mailbox", "list"), ("--apply", "--reboot")):
            with self.subTest(args=args):
                result = self.run_cli("pilot", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("locked: run sage-box pilot unlock", result.stderr)
        self.assertEqual(self.remote(), [])
        self.assertEqual(self.reads(), [])
        self.assertEqual(self.resolved(), [], "a locked box stops before the vault")

    def test_an_expired_or_foreign_certificate_is_locked_and_its_connection_closed(self):
        self.window.mkdir(parents=True)
        key = self.window / "key"
        subprocess.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        for principal, validity, said in (("sage-window", "20200101:20200102", "the last window closed at"),
                                          ("someone", "-5m:+1h", "locked")):
            with self.subTest(principal=principal):
                (self.window / "key-cert.pub").unlink(missing_ok=True)
                subprocess.run(["/usr/bin/ssh-keygen", "-q", "-s", str(self.state / "test-ca"), "-I", "old", "-n", principal,
                                "-V", validity, f"{key}.pub"], check=True)
                result = self.run_cli("pilot", "update")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("locked: run sage-box pilot unlock", result.stderr)
                self.assertIn(said, result.stderr)
        self.assertTrue([c for c in self.ssh_calls() if " -O exit " in c and "window-%C" in c], "a connection from that window is closed")
        self.assertEqual(self.remote(), [])

    def test_lock_ends_the_window_and_its_connection(self):
        self.ok("pilot", "unlock")
        self.ok("pilot", "update")
        self.assertIn("pilot: locked.", self.ok("pilot", "lock").stdout)
        self.assertFalse(self.window.exists())
        closes = [c for c in self.ssh_calls() if " -O exit " in c]
        self.assertTrue(closes and f'ControlPath="{self.home}/.sage-is/ssh/window-%C"' in closes[-1])
        self.assertIn("window    locked", self.ok("pilot", "status").stdout)
        self.assertIn("locked: run sage-box pilot unlock", self.run_cli("pilot", "update").stderr)


class Doors(Mac):
    """Every admin door shares one connection a run; each keeps its own socket."""

    def test_legacy_warns_and_shares_one_connection(self):
        self.box()
        self.probe(DONE)
        result = self.ok("pilot", "update")
        self.assertEqual(result.stderr.count("legacy admin key"), 1)
        for call in [c for c in self.ssh_calls() if "bash -c" in c]:
            self.assertIn("-F none -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -o BatchMode=yes", call)
            self.assertIn(f'-o ControlMaster=auto -o ControlPath="{self.home}/.sage-is/ssh/legacy-%C" -o ControlPersist=600', call)
        self.assertEqual(sorted(set(self.resolved())), ["BOX_SSH_KEY"], "update needs the door's key and nothing else")
        self.assertEqual(stat.S_IMODE((self.home / ".sage-is" / "ssh").stat().st_mode), 0o700)

    def test_as_me_uses_the_person_s_own_agent(self):
        self.box(BOX_SSH_KEY=None, BOX_SUDO=None)
        self.probe(DONE)
        self.ok("pilot", "--as-me", "update")
        self.ok("pilot", "update", "--as-me")
        calls = [c for c in self.ssh_calls() if "bash -c" in c]
        self.assertEqual(len(calls), 10)
        for call in calls:
            self.assertIn(f'-o ControlMaster=auto -o ControlPath="{self.home}/.sage-is/ssh/me-%C" -o ControlPersist=600', call)
            self.assertNotIn("BatchMode", call, "a FIDO key may ask for its PIN")
            self.assertNotIn("IdentityFile", call)
        self.assertFalse([c for c in self.calls() if c.startswith(("ssh-agent", "ssh-add"))], "the person's agent, not one of ours")
        self.assertEqual(self.resolved(), [])


class Shape(unittest.TestCase):
    def test_the_formula_installs_the_tool_beside_its_lib_and_the_example(self):
        text = FORMULA.read_text()
        for line in ('libexec.install "sage-box"', '(libexec/"lib").install "lib/sage-runtime.sh"',
                     'bin.write_exec_script libexec/"sage-box"', 'man1.install "sage-box.1"',
                     '(pkgshare/"boxes").install "docs/boxes/example.env"', 'depends_on "age"'):
            self.assertIn(f"  {line}\n", text, line)

    def test_the_example_names_no_host_login_or_secret(self):
        example = (TOOL.parent / "docs" / "boxes" / "example.env").read_text()
        self.assertIn("BOX_READ_KEY=bw:", example)
        self.assertIn("BOX_OPERATORS=100.64.0.0/10", example)
        self.assertIn("BOX_SSH_KEY=bw:", example)
        self.assertNotIn("100.109", example)
        self.assertNotIn("realtor", example.lower())


if __name__ == "__main__":
    unittest.main()
