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
NEW_SECRET = "freshly-made-token-789"
SECRETS = (CF_TOKEN, SESSION, "agent-master-pw", "client-secret-val", "the-user", "a note", "field-val", NEW_SECRET)

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
  list)    if [[ "$2" == organizations ]]; then echo '[{"id":"org-ro","name":"Agents"},{"id":"org-rw","name":"Agents-edit"}]'; exit 0; fi
           if [[ "$2" == collections && "$3" == --organizationid ]]; then
             if [[ "$4" == org-rw ]]; then cat "$STATE/rw-collections" 2>/dev/null || echo '[{"id":"col-rw","name":"Default collection"}]'
             else echo '[{"id":"col-ro","name":"Default collection"}]'; fi; exit 0; fi
           if [[ "$2" == collections ]]; then echo '[{"name":"Agents"}]'; exit 0; fi
           printf '['; first=1; for f in "$STATE"/items/*.json; do [[ $first == 1 ]] || printf ','; first=0; cat "$f"; done; printf ']' ;;
  create|edit)
           [[ "$*" == *"--session session-key-xyz"* ]] || exit 1
           python3 -c 'import base64, json, sys
d = json.loads(base64.b64decode(sys.stdin.read()))
d.setdefault("id", "id-" + d["name"])
open(sys.argv[1] + "/" + d["name"] + ".json", "w").write(json.dumps(d))' "$STATE/items" ;;
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


class Put(Base):
    """Values the agent makes go into the organization it may edit, by stdin, never on screen."""

    def stored(self, name):
        return json.loads((self.state / "items" / f"{name}.json").read_text())

    def test_a_value_from_the_environment_lands_in_agents_edit_and_nowhere_visible(self):
        result = self.run_tool("put", "yt-tunnel-token", "--from-env", "NEW_TOKEN", NEW_TOKEN=NEW_SECRET)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = self.stored("yt-tunnel-token")
        self.assertEqual(item["login"]["password"], NEW_SECRET)
        self.assertEqual((item["organizationId"], item["collectionIds"]), ("org-rw", ["col-rw"]))
        self.assertIn("Stored yt-tunnel-token (password) in Agents-edit / Default collection. Use it as bw:yt-tunnel-token", result.stdout)
        self.assertNotIn(NEW_SECRET, (self.state / "calls").read_text(), "the value reached a command line")
        self.assertTrue((self.state / "locked").exists())

    def test_a_file_or_stdin_works_and_a_custom_field_is_hidden(self):
        secret_file = self.work / "token"
        secret_file.write_text(NEW_SECRET + "\n")
        self.assertEqual(self.run_tool("put", "ghcr", "--from-file", str(secret_file), "--field", "api_token",
                                       "--username", "opencoca").returncode, 0)
        item = self.stored("ghcr")
        self.assertEqual(item["fields"], [{"name": "api_token", "value": NEW_SECRET, "type": 1}])
        self.assertEqual(item["login"]["username"], "opencoca")
        result = subprocess.run([TOOL, "put", "piped", "--stdin"], env=self.env, input=NEW_SECRET,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(NEW_SECRET, result.stdout + result.stderr)
        self.assertEqual(self.stored("piped")["login"]["password"], NEW_SECRET)

    def test_what_it_stores_reads_back_through_run(self):
        self.run_tool("put", "made-here", "--from-env", "V", V=NEW_SECRET)
        cmd, out = self.child_saw("X")
        self.assertEqual(self.run_tool("run", *cmd, X="bw:made-here").returncode, 0)
        self.assertEqual(json.loads(out.read_text()), {"X": NEW_SECRET})

    def test_a_name_used_outside_the_write_organization_is_refused(self):
        result = self.run_tool("put", "cloudflare-tunnel-startr", "--from-env", "V", V=NEW_SECRET)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists outside Agents-edit", result.stderr)
        self.assertEqual(self.stored("cloudflare-tunnel-startr")["login"]["password"], CF_TOKEN)
        self.assertTrue((self.state / "locked").exists())

    def test_changing_an_existing_item_needs_replace_and_keeps_its_other_fields(self):
        self.run_tool("put", "rotating", "--from-env", "V", "--username", "svc", V="first-value-111")
        self.run_tool("put", "rotating", "--from-env", "V", "--field", "notes", "--replace", V="a kept note")
        result = self.run_tool("put", "rotating", "--from-env", "V", V=NEW_SECRET)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Add --replace", result.stderr)
        self.assertEqual(self.run_tool("put", "rotating", "--from-env", "V", "--replace", V=NEW_SECRET).returncode, 0)
        item = self.stored("rotating")
        self.assertEqual((item["login"]["password"], item["login"]["username"], item["notes"]),
                         (NEW_SECRET, "svc", "a kept note"))
        self.assertTrue(any(c.startswith("bw edit item id-rotating") for c in self.calls()))

    def test_sources_and_destinations_are_checked_before_anything_is_written(self):
        for args, env, message in (
                (("--from-env", "V", "--stdin"), {"V": NEW_SECRET}, "exactly one source"),
                ((), {}, "exactly one source"),
                (("--from-env", "EMPTY"), {"EMPTY": ""}, "the value is empty"),
                (("--from-env", "V", "--org", "Nope"), {"V": NEW_SECRET}, "no organization named Nope"),
                (("--from-env", "V", "--collection", "Other"), {"V": NEW_SECRET}, "has no collection named Other")):
            with self.subTest(args=args):
                result = self.run_tool("put", "x", *args, **env)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
        (self.state / "rw-collections").write_text('[]')
        self.assertIn("sees no collection in Agents-edit", self.run_tool("put", "x", "--from-env", "V", V=NEW_SECRET).stderr)
        (self.state / "rw-collections").write_text('[{"id":"a","name":"One"},{"id":"b","name":"Two"}]')
        result = self.run_tool("put", "x", "--from-env", "V", V=NEW_SECRET)
        self.assertIn("name one with --collection", result.stderr)
        self.assertFalse((self.state / "items" / "x.json").exists())


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
