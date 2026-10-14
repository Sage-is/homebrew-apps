"""sage-box: a Mac becomes a Sage.is box in one run, from one file, over SSH, with secrets from the vault."""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "sage-box"
FORMULA = TOOL.parent / "Formula" / "sage-box.rb"

# Stand-ins log each call to $STATE/calls. ssh decodes the script sage-box sent
# (base64 inside the command), appends it to $STATE/remote, saves its stdin to
# $STATE/stdin-N, and answers: the probe from $STATE/probe (the third probe and
# later from $STATE/probe-after when it exists), the newest backup, a restart.
# sage-secret resolves bw:ITEM to secret-ITEM, or names a missing item.
STUBS = {
    "ssh": r'''echo "ssh $*" >> "$STATE/calls"
n=$(( $(cat "$STATE/n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STATE/n"
cat > "$STATE/stdin-$n"
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
exit 0''',
    "ssh-agent": 'case "${1:-}" in -s) echo "SSH_AUTH_SOCK=/tmp/sage-box-test; export SSH_AUTH_SOCK;" ;; esac; echo "ssh-agent $*" >> "$STATE/calls"',
    "ssh-add": 'echo "ssh-add $*" >> "$STATE/calls"; cat > "$STATE/ssh-key"',
    "scp": 'echo "scp $*" >> "$STATE/calls"; cp "$STATE/archive" "${@: -1}"',
    "age": '''echo "age $*" >> "$STATE/calls"
while [ $# -gt 0 ]; do case "$1" in -r) shift ;; -o) out="$2"; shift ;; *) in="$1" ;; esac; shift; done
cp "$in" "$out"''',
    "launchctl": 'echo "launchctl $*" >> "$STATE/calls"',
    "sleep": "true",
    "sage-secret": r'''echo "sage-secret $1" >> "$STATE/calls"
[ "$1" = run ] || exit 1
shift; file="$2"; shift 3
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  key="${line%%=*}"; val="${line#*=}"
  case "$val" in
    bw:*) item="${val#bw:}"; item="${item%%/*}"
          [ ! -e "$STATE/missing-$item" ] || { echo "sage-secret: no item named $item that the agent account can see (for $key)" >&2; exit 1; }
          val="secret-$item" ;;
  esac
  export "$key=$val"
done < "$file"
exec "$@"''',
    "sage-runtime": 'case "${1:-}" in version) echo "sage-runtime 0.1.0" ;; status) echo "Runtimes:"; echo "  colima running" ;; esac',
    "trellis-crm": 'case "${1:-}" in version) echo "trellis-crm 0.1.1 (image x)" ;; esac',
    "ai-ui": 'case "${1:-}" in version) echo "ai-ui 3.2.0_2" ;; esac',
}

BARE = {"arch": "arm64", "os": "15.6", "brew": "missing", "sage_runtime": "missing", "trellis_crm": "missing",
        "ai_ui": "missing", "vm": "", "context": "", "boot": "no", "backup_daemon": "no", "autorestart": "0",
        "sleep": "1", "ghcr": "no", "deploy_key": "no", "checkout": "none", "source": "", "profile": "",
        "review_at": "", "shift_end": "", "trellis": "down", "aiui": "down", "filevault": "on", "leftovers": "0"}
DONE = {**BARE, "brew": "/opt/homebrew/bin/brew", "sage_runtime": "0.1.0", "trellis_crm": "0.1.1", "ai_ui": "3.2.0_2",
        "vm": "krunkit", "context": "colima", "boot": "yes", "backup_daemon": "yes", "autorestart": "1", "sleep": "0",
        "ghcr": "yes", "deploy_key": "yes", "checkout": "28d5622", "source": "/Users/user/src/trellis",
        "profile": "realestate", "review_at": "15:00", "shift_end": "18:00", "trellis": "up", "aiui": "up"}

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
                 "trellis-crm start --profile \"$BOX_PROFILE\"", "ai-ui start", "launchctl bootstrap system /Library/LaunchDaemons/is.sage.trellis-backup.plist"]
        positions = [text.index(part) for part in order]
        self.assertEqual(positions, sorted(positions), order)
        self.assertIn("--source /Users/user/src/trellis", text)
        self.assertIn("REVIEW_AT=$BOX_REVIEW_AT", text)
        self.assertNotIn("secret-", text, "a secret landed in a script")
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
        text = "\n".join(self.remote())
        order = ["brew upgrade sage-runtime trellis-crm ai-ui", 'git -C "$BOX_CHECKOUT" pull --ff-only', "trellis-crm update", "ai-ui start"]
        positions = [text.index(part) for part in order]
        self.assertEqual(positions, sorted(positions))

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
