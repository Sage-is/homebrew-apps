"""sage-secret: bw: references resolved from a stand-in Vaultwarden, values never printed, vault locked after."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "sage-secret"
CF_TOKEN = "cf-token-value-123"
SESSION = "session-key-xyz"
SECRETS = (CF_TOKEN, SESSION, "agent-master-pw", "client-secret-val", "the-user", "a note", "field-val")

# Each item is a JSON file in $STATE/items; "late" items appear only after `bw sync`.
STUBS = {
    "bw": r"""echo "bw $*" >> "$STATE/calls"
echo "$BITWARDENCLI_APPDATA_DIR" > "$STATE/appdata"
case "$1" in
  status)  if [[ -e "$STATE/logged-in" ]]; then s=locked; else s=unauthenticated; fi
           echo "{\"status\":\"$s\",\"serverUrl\":\"https://vault.example\",\"userEmail\":\"agents@sage.is\"}" ;;
  login)   [[ "$BW_CLIENTID" == client-id-val && "$BW_CLIENTSECRET" == client-secret-val ]] || exit 1
           touch "$STATE/logged-in" ;;
  unlock)  [[ "$SAGE_SECRET_BW_PASSWORD" == agent-master-pw ]] || { echo "Invalid master password." >&2; exit 1; }
           printf 'session-key-xyz' ;;
  get)     [[ "$*" == *"--session session-key-xyz"* ]] || exit 1
           f="$STATE/items/$3.json"; [[ -e "$f" ]] || { echo "Not found." >&2; exit 1; }; cat "$f" ;;
  sync)    cp "$STATE"/late/*.json "$STATE/items/" 2>/dev/null; true ;;
  list)    if [[ "$2" == organizations ]]; then echo '[{"name":"Sage.is"}]'; exit 0; fi
           if [[ "$2" == collections ]]; then echo '[{"name":"Agents"}]'; exit 0; fi
           printf '['; first=1; for f in "$STATE"/items/*.json; do [[ $first == 1 ]] || printf ','; first=0; cat "$f"; done; printf ']' ;;
  lock)    touch "$STATE/locked" ;;
  config)  echo "$3" > "$STATE/server" ;;
esac""",
    "security": r"""echo "security $*" >> "$STATE/calls"
case "$1" in
  find-generic-password)
    for a; do last="$a"; done; acct=""; prev=""; for a; do [[ "$prev" == -a ]] && acct="$a"; prev="$a"; done
    f="$STATE/keychain/$acct"; [[ -e "$f" ]] || exit 44; cat "$f" ;;
  add-generic-password) exit 0 ;;
esac""",
}


@unittest.skipUnless(sys.platform == "darwin", "the Keychain is macOS")
class Base(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.state, self.bin, self.work = root / "state", root / "bin", root / "work"
        for folder in (self.state, self.state / "items", self.state / "late", self.state / "keychain", self.bin, self.work):
            folder.mkdir()
        for name, body in STUBS.items():
            (self.bin / name).write_text("#!/bin/bash\n" + body + "\n")
            (self.bin / name).chmod(0o755)
        for account, value in (("BW_CLIENTID", "client-id-val"), ("BW_CLIENTSECRET", "client-secret-val"),
                               ("BW_PASSWORD", "agent-master-pw")):
            (self.state / "keychain" / account).write_text(value)
        self.item("cloudflare-tunnel-startr", password=CF_TOKEN, username="the-user", notes="a note",
                  fields=[{"name": "account", "value": "field-val"}])
        self.env = {"HOME": str(root), "PATH": f"{self.bin}:/usr/bin:/bin", "STATE": str(self.state)}

    def item(self, name, where="items", password=None, username=None, notes=None, fields=None):
        data = {"name": name, "login": {"password": password, "username": username}, "notes": notes, "fields": fields or []}
        (self.state / where / f"{name}.json").write_text(json.dumps(data))

    def run_tool(self, *args, **env):
        result = subprocess.run([TOOL, *args], env=self.env | env, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=30)
        for secret in SECRETS:
            self.assertNotIn(secret, result.stdout + result.stderr, "a secret reached the screen")
        return result

    def child_saw(self, *keys):
        """A command that writes the named variables into a file, for the test to read."""
        out = self.work / "seen.json"
        code = f"import json,os; json.dump({{k: os.environ.get(k) for k in {list(keys)!r}}}, open({str(out)!r}, 'w'))"
        return ["--", "python3", "-c", code], out

    def calls(self):
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []


class Run(Base):
    def test_references_from_a_file_and_the_environment_reach_the_command(self):
        env_file = self.work / "tunnel.env"
        env_file.write_text("# tunnel tool\nCLOUDFLARE_API_TOKEN=bw:cloudflare-tunnel-startr\nPLAIN=kept as is\n")
        cmd, out = self.child_saw("CLOUDFLARE_API_TOKEN", "PLAIN", "CF_USER", "CF_NOTES", "CF_ACCOUNT")
        result = self.run_tool("run", "--env", str(env_file), *cmd,
                               CF_USER="bw:cloudflare-tunnel-startr/username",
                               CF_NOTES="bw:cloudflare-tunnel-startr/notes",
                               CF_ACCOUNT="bw:cloudflare-tunnel-startr/account")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(out.read_text()), {
            "CLOUDFLARE_API_TOKEN": CF_TOKEN, "PLAIN": "kept as is", "CF_USER": "the-user",
            "CF_NOTES": "a note", "CF_ACCOUNT": "field-val"})

    def test_the_first_run_logs_the_agent_in_with_its_api_key(self):
        cmd, _ = self.child_saw("X")
        self.run_tool("run", *cmd, X="bw:cloudflare-tunnel-startr")
        calls = self.calls()
        self.assertTrue(any(c.startswith("bw login --apikey") for c in calls), calls)
        self.assertLess(next(i for i, c in enumerate(calls) if c.startswith("bw login")),
                        next(i for i, c in enumerate(calls) if c.startswith("bw unlock")))

    def test_no_references_means_no_vault_at_all(self):
        cmd, out = self.child_saw("X")
        self.assertEqual(self.run_tool("run", *cmd, X="plain").returncode, 0)
        self.assertEqual(json.loads(out.read_text()), {"X": "plain"})
        self.assertFalse([c for c in self.calls() if c.startswith("bw")])

    def test_the_vault_is_locked_after_the_command_even_when_it_fails(self):
        result = self.run_tool("run", "--", "python3", "-c", "raise SystemExit(3)", X="bw:cloudflare-tunnel-startr")
        self.assertEqual(result.returncode, 3)
        self.assertTrue((self.state / "locked").exists())

    def test_a_new_item_is_found_after_one_sync(self):
        self.item("resend-api", where="late", password="re_live_key")
        cmd, out = self.child_saw("RESEND")
        self.run_tool("run", *cmd, RESEND="bw:resend-api")
        self.assertEqual(json.loads(out.read_text()), {"RESEND": "re_live_key"})
        self.assertEqual(sum(c.startswith("bw sync") for c in self.calls()), 1)

    def test_a_missing_item_stops_before_the_command_and_names_only_the_item(self):
        cmd, out = self.child_saw("X")
        result = self.run_tool("run", *cmd, X="bw:no-such-item")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no item named no-such-item", result.stderr)
        self.assertIn("(for X)", result.stderr)
        self.assertFalse(out.exists())

    def test_a_missing_field_is_named(self):
        cmd, _ = self.child_saw("X")
        result = self.run_tool("run", *cmd, X="bw:cloudflare-tunnel-startr/totp")
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no totp", result.stderr)

    def test_no_keychain_entry_says_to_run_setup(self):
        (self.state / "keychain" / "BW_PASSWORD").unlink()
        cmd, _ = self.child_saw("X")
        result = self.run_tool("run", *cmd, X="bw:cloudflare-tunnel-startr")
        self.assertEqual(result.returncode, 1)
        self.assertIn("sage-secret setup", result.stderr)

    def test_no_bitwarden_cli_says_how_to_install_it(self):
        (self.bin / "bw").unlink()
        cmd, _ = self.child_saw("X")
        result = self.run_tool("run", *cmd, X="bw:cloudflare-tunnel-startr")
        self.assertEqual(result.returncode, 1)
        self.assertIn("brew install bitwarden-cli", result.stderr)


class Isolation(Base):
    def test_the_agent_keeps_its_own_bw_folder(self):
        cmd, _ = self.child_saw("X")
        self.run_tool("run", *cmd, X="bw:cloudflare-tunnel-startr")
        self.assertEqual((self.state / "appdata").read_text().strip(), self.env["HOME"] + "/.sage-is/bw-agent")


class CheckAndSetup(Base):
    def test_check_lists_names_and_never_values(self):
        out = self.run_tool("check").stdout
        self.assertIn("agents@sage.is", out)
        self.assertIn("1 items visible: cloudflare-tunnel-startr", out)
        self.assertIn("1 collections visible: Agents", out)
        self.assertTrue(any(c.startswith("bw sync") for c in self.calls()), "check must sync before it lists")
        self.assertTrue((self.state / "locked").exists())

    def test_setup_points_bw_at_the_server_and_lets_security_prompt(self):
        result = self.run_tool("setup", "--server", "https://vault.example")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.state / "server").read_text().strip(), "https://vault.example")
        adds = [c for c in self.calls() if c.startswith("security add-generic-password")]
        self.assertEqual(len(adds), 3)
        self.assertTrue(all(c.endswith(" -w") for c in adds), adds)


if __name__ == "__main__":
    unittest.main()
