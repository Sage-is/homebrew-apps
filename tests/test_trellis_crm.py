"""trellis-crm in server mode: the settings file, the docker run, backups, boot and nuke."""

import os
import plistlib
import pty
import pwd
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TRELLIS_CRM = Path(__file__).resolve().parent.parent / "trellis-crm"

# Stand-ins log each call. Docker keeps a little state as marker files: the
# runtime answering, the image, the network, the volume, the container.
STUBS = {
    "docker": """echo "docker ${DOCKER_CONTEXT:-} $*" >> "$STATE/calls"
case "$1 ${2:-}" in
  "info "*)          [[ -e "$STATE/running-${DOCKER_CONTEXT:-default}" ]] ;;
  "context "*)       cat "$STATE/current-context" 2>/dev/null || echo default ;;
  "image inspect")   [[ -e "$STATE/image" ]] ;;
  "pull "*)          [[ ! -e "$STATE/pull-denied" ]] && touch "$STATE/image" ;;
  "network inspect") [[ -e "$STATE/network" ]] ;;
  "network create")  touch "$STATE/network" ;;
  "volume inspect")  [[ -e "$STATE/volume" ]] ;;
  "volume rm")       rm -f "$STATE/volume" ;;
  "container inspect")
    name="${@: -1}"
    [[ -e "$STATE/container-$name" ]] || exit 1
    if [[ "$*" == *State.Running* ]]; then
      if [[ -e "$STATE/up-$name" ]]; then echo true; else echo false; fi
    fi ;;
  "run -d")
    name=trellis-crm; prev=""
    for a in "$@"; do [[ "$prev" == --name ]] && name="$a"; prev="$a"; done
    touch "$STATE/container-$name" "$STATE/up-$name" "$STATE/volume" ;;
  "run --rm")        cat > "$STATE/restored" ;;
  "stop "*)          rm -f "$STATE/up-${@: -1}" ;;
  "rm "*)            rm -f "$STATE/container-${@: -1}" "$STATE/up-${@: -1}" ;;
  "exec "*)
    if [[ "$2" == -i ]]; then cat > /dev/null; fi
    if [[ "$*" == *" tar "* ]]; then printf 'TARDATA'; fi ;;
esac""",
    "colima": """echo "colima $*" >> "$STATE/calls"
case "$1" in
  list)  if [[ -e "$STATE/colima-vm" ]]; then echo '{"name":"default","status":"Stopped"}'; fi ;;
  start) touch "$STATE/colima-vm" "$STATE/running-colima" ;;
esac""",
    "curl": """echo "curl $*" >> "$STATE/calls"
echo '{"ok": true, "worker": "on"}'""",
    "sudo": 'echo "sudo $*" >> "$STATE/calls"',
    "open": 'echo "open $*" >> "$STATE/calls"',
}


@unittest.skipUnless(sys.platform == "darwin", "server mode targets a Mac")
class Mac(unittest.TestCase):
    """A fake Mac: its own home, apps folder, LaunchDaemons folder and stand-in commands."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.home, self.state, self.daemons = root / "home", root / "state", root / "LaunchDaemons"
        self.bin = root / "bin"
        for folder in (self.home, self.state, self.daemons, self.bin, root / "Applications"):
            folder.mkdir()
        for name, body in STUBS.items():
            stub = self.bin / name
            stub.write_text("#!/bin/bash\n" + body + "\n")
            stub.chmod(0o755)
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin",
                    "STATE": str(self.state), "APPLICATIONS_DIR": str(root / "Applications"),
                    "LAUNCH_DAEMONS_DIR": str(self.daemons)}
        self.env_file = self.home / ".sage-is" / "trellis-crm.env"
        self.backups = self.home / ".sage-is" / "backups" / "trellis-crm"

    def run_cli(self, *args, answer=None):
        """Run trellis-crm; with `answer`, stdin is a terminal that types it."""
        if answer is None:
            return subprocess.run([TRELLIS_CRM, *args], env=self.env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=30)
        terminal, typist = pty.openpty()
        os.write(terminal, answer.encode())
        try:
            return subprocess.run([TRELLIS_CRM, *args], env=self.env, capture_output=True, text=True,
                                  stdin=typist, timeout=30)
        finally:
            os.close(terminal)
            os.close(typist)

    def ok(self, *args, **kwargs):
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def docker_runs(self):
        return [call for call in self.calls() if " run -d " in call]

    def settings(self):
        lines = self.env_file.read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


class FirstStart(Mac):
    def test_the_settings_file_is_private_and_holds_fresh_secrets(self):
        self.ok("start")
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        settings = self.settings()
        for key in ("DJANGO_SUPERUSER_PASSWORD", "CRM_API_KEY", "INTAKE_SECRET"):
            self.assertGreaterEqual(len(settings[key]), 20, key)
        self.assertGreaterEqual(len(settings["DJANGO_SECRET_KEY"]), 40)
        self.assertEqual(settings["RUN_WORKER"], "1")
        self.assertEqual(settings["AI_UI_URL"], "http://sage-ai:8080")

    def test_the_container_restarts_with_docker_and_listens_on_loopback_only(self):
        self.ok("start")
        (run,) = self.docker_runs()
        for part in ("--name trellis-crm", "--restart unless-stopped", "--network sage-net",
                     "-p 127.0.0.1:8030:8000", "-v trellis-crm-data:/data",
                     f"--env-file {self.env_file}", "ghcr.io/sage-is/trellis:latest"):
            self.assertIn(part, run)

    def test_the_first_admin_password_is_shown_once(self):
        first = self.ok("start")
        self.assertIn(f"Sign in as admin with {self.settings()['DJANGO_SUPERUSER_PASSWORD']}", first.stdout)
        self.assertNotIn("Sign in as", self.ok("start").stdout)

    def test_a_second_start_keeps_the_secrets(self):
        self.ok("start")
        before = self.env_file.read_text()
        self.ok("start")
        self.assertEqual(self.env_file.read_text(), before)

    def test_a_new_settings_file_on_an_old_volume_promises_no_password(self):
        (self.state / "volume").touch()
        result = self.ok("start")
        self.assertNotIn("Sign in as", result.stdout)
        self.assertIn("already had an admin", result.stdout)

    def test_it_waits_for_the_health_check_before_saying_it_runs(self):
        self.ok("start")
        calls = self.calls()
        health = next(i for i, call in enumerate(calls) if "http://127.0.0.1:8030/crm/health/" in call)
        self.assertGreater(health, calls.index(self.docker_runs()[0]))

    def test_colima_is_the_runtime_and_ai_ui_sees_the_same_choice(self):
        self.ok("start")
        self.assertIn("colima start --vm-type vz --memory 4", self.calls())
        self.assertEqual((self.home / ".sage-is" / "runtime").read_text().strip(), "colima")


class Options(Mac):
    def test_options_are_saved_and_the_next_start_reuses_them(self):
        result = self.ok("start", "--port", "9000", "--bind", "0.0.0.0",
                         "--url", "https://crm.example.com/", "--profile", "realestate")
        self.assertIn("open to the network. Put TLS in front of it.", result.stdout)
        self.ok("start")
        settings = self.settings()
        self.assertEqual(settings["TRELLIS_PORT"], "9000")
        self.assertEqual(settings["CRM_PUBLIC_URL"], "https://crm.example.com")
        self.assertEqual(settings["CRM_PROFILE"], "realestate")
        self.assertIn("-p 0.0.0.0:9000:8000", self.docker_runs()[-1])
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)

    def test_a_new_port_moves_a_local_url_but_not_a_public_one(self):
        self.ok("start", "--port", "9000")
        self.assertEqual(self.settings()["CRM_PUBLIC_URL"], "http://localhost:9000")
        self.ok("start", "--url", "https://crm.example.com")
        self.ok("start", "--port", "9100")
        self.assertEqual(self.settings()["CRM_PUBLIC_URL"], "https://crm.example.com")

    def test_a_tag_picks_the_image(self):
        self.ok("start", "--tag", "0.1.0")
        self.assertIn("ghcr.io/sage-is/trellis:0.1.0", self.docker_runs()[0])


class Network(Mac):
    def test_the_shared_network_is_made_once_and_ai_ui_joins_it(self):
        (self.state / "container-sage-ai").touch()
        self.ok("start")
        self.assertIn("docker colima network create sage-net", self.calls())
        self.assertIn("docker colima network connect sage-net sage-ai", self.calls())
        self.ok("start")
        self.assertEqual(self.calls().count("docker colima network create sage-net"), 1)

    def test_without_ai_ui_nothing_is_connected(self):
        self.ok("start")
        self.assertFalse([call for call in self.calls() if "network connect" in call])


class Images(Mac):
    def pulls(self):
        return [call for call in self.calls() if " pull " in call]

    def test_start_pulls_only_a_missing_image_and_update_always_pulls(self):
        self.ok("start")
        self.ok("start")
        self.assertEqual(len(self.pulls()), 1)
        self.ok("update")
        self.assertEqual(len(self.pulls()), 2)

    def test_update_backs_up_before_it_pulls(self):
        self.ok("start")
        self.ok("update", "--tag", "0.2.0")
        calls = self.calls()
        backup = next(i for i, call in enumerate(calls) if " tar " in call)
        self.assertLess(backup, calls.index("docker colima pull ghcr.io/sage-is/trellis:0.2.0"))
        self.assertIn("ghcr.io/sage-is/trellis:0.2.0", self.docker_runs()[-1])

    def test_a_denied_pull_says_how_to_sign_in(self):
        (self.state / "pull-denied").touch()
        result = self.run_cli("start")
        self.assertEqual(result.returncode, 1)
        self.assertIn("docker login ghcr.io", result.stdout)
        self.assertFalse(self.docker_runs())


class StopAndStatus(Mac):
    def test_stop_keeps_the_container_so_a_reboot_leaves_it_stopped(self):
        self.ok("start")
        result = self.ok("stop")
        calls = self.calls()
        stop = calls.index("docker colima stop trellis-crm")
        self.assertFalse([call for call in calls[stop:] if " rm " in call])
        self.assertTrue((self.state / "container-trellis-crm").exists())
        self.assertIn("across reboots", result.stdout)

    def test_status_before_any_start(self):
        self.assertIn("not set up here", self.ok("status").stdout)

    def test_commands_follow_the_runtime_ai_ui_saved(self):
        (self.home / ".sage-is").mkdir()
        (self.home / ".sage-is" / "runtime").write_text("docker-desktop\n")
        self.run_cli("status")
        self.assertIn("docker desktop-linux container inspect trellis-crm", self.calls())


class Backups(Mac):
    def test_a_backup_is_a_private_archive_named_by_time(self):
        self.ok("start")
        result = self.ok("backup")
        (archive,) = self.backups.glob("trellis-crm-*.tar.gz")
        self.assertEqual(archive.read_text(), "TARDATA")
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        self.assertIn(str(archive), result.stdout)

    def test_a_backup_needs_trellis_running(self):
        result = self.run_cli("backup")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Start it first", result.stdout)

    def test_restore_asks_first(self):
        backup = self.home / "old.tar.gz"
        backup.write_text("OLD")
        result = self.run_cli("restore", str(backup))
        self.assertEqual(result.returncode, 1)
        self.assertIn("--yes", result.stdout)
        self.assertFalse((self.state / "restored").exists())

    def test_restore_backs_up_then_unpacks_then_starts(self):
        self.ok("start")
        backup = self.home / "old.tar.gz"
        backup.write_text("OLD")
        self.ok("restore", str(backup), "--yes")
        self.assertEqual((self.state / "restored").read_text(), "OLD")
        self.assertEqual(len(list(self.backups.glob("*.tar.gz"))), 1)
        self.assertEqual(len(self.docker_runs()), 2)


class Boot(Mac):
    def test_the_daemon_runs_colima_as_this_user_with_a_home_and_a_path(self):
        plist = plistlib.loads(self.ok("boot", "--print").stdout.encode())
        self.assertEqual(plist["Label"], "is.sage.colima")
        self.assertEqual(plist["UserName"], pwd.getpwuid(os.getuid()).pw_name)
        self.assertEqual(plist["ProgramArguments"], [str(self.bin / "colima"), "start", "--foreground"])
        self.assertEqual(plist["EnvironmentVariables"]["HOME"], str(self.home))
        self.assertTrue(plist["EnvironmentVariables"]["PATH"].startswith(str(self.bin)))
        self.assertTrue(plist["RunAtLoad"])
        self.assertGreater(plist["ExitTimeOut"], 30)

    def test_install_hands_the_plist_to_launchd(self):
        self.ok("boot")
        calls = self.calls()
        self.assertTrue([call for call in calls if call.startswith("sudo install -m 644 -o root -g wheel ")])
        self.assertIn("colima stop", calls)
        self.assertEqual(calls[-1], f"sudo launchctl bootstrap system {self.daemons}/is.sage.colima.plist")

    def test_only_colima_starts_with_nobody_signed_in(self):
        result = self.run_cli("boot", "--runtime", "docker-desktop")
        self.assertEqual(result.returncode, 1)
        self.assertIn("only Colima starts at boot", result.stdout)


class Nuke(Mac):
    def test_dry_run_removes_nothing(self):
        self.ok("start")
        self.assertIn("[dry-run] Nothing was removed.", self.ok("nuke", "--dry-run").stdout)
        self.assertTrue(self.env_file.exists())
        self.assertFalse([call for call in self.calls() if "volume rm" in call])

    def test_it_takes_only_yes(self):
        self.ok("start")
        self.assertEqual(self.run_cli("nuke", answer="y\n").returncode, 1)
        self.assertTrue(self.env_file.exists())

    def test_yes_removes_the_container_the_volume_and_the_settings(self):
        self.ok("start")
        self.ok("nuke", answer="yes\n")
        self.assertIn("docker colima volume rm trellis-crm-data", self.calls())
        self.assertFalse(self.env_file.exists())

    def test_a_server_needs_force(self):
        (self.daemons / "is.sage.colima.plist").touch()
        result = self.run_cli("nuke", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nuke --force", result.stdout)
        self.ok("nuke", "--yes", "--force")
        self.assertIn("docker colima volume rm trellis-crm-data", self.calls())


class Usage(Mac):
    def test_no_command_prints_usage(self):
        self.assertIn("Usage: trellis-crm", self.ok().stdout)

    def test_an_unknown_command_fails(self):
        result = self.run_cli("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown command: launch", result.stdout)


class DevMode(Mac):
    """A checkout runs beside the real Trellis: its own container, volume and port, loopback, synthetic data."""

    def checkout(self, name="trellis"):
        repo = self.home / "src" / name
        (repo / ".git").mkdir(parents=True)
        (repo / "manage.py").write_text("")
        return repo

    def test_dev_runs_the_checkout_beside_the_real_one(self):
        repo = self.checkout()
        self.ok("start")
        before = len(self.calls())
        self.ok("dev", "--dir", str(repo))
        dev_calls = self.calls()[before:]
        (real, dev) = self.docker_runs()
        self.assertIn("--name trellis-crm-dev", dev)
        self.assertIn("-p 127.0.0.1:8031:8000", dev)
        self.assertIn(f"-v {repo}:/app", dev)
        self.assertIn("-v trellis-crm-dev-data:/data", dev)
        self.assertIn("-e DJANGO_DEBUG=1", dev)
        self.assertIn("-e LOAD_FIXTURES=1", dev)
        self.assertNotIn("--env-file", dev)
        self.assertTrue((self.state / "up-trellis-crm").exists(), "the real Trellis stopped")
        self.assertFalse([c for c in dev_calls if c.endswith(" trellis-crm")], "dev touched the real container")
        self.assertTrue(any("exec trellis-crm-dev python manage.py dev_superuser" in c for c in dev_calls))

    def test_the_checkout_is_remembered_in_the_shared_projects_file(self):
        repo = self.checkout()
        self.ok("dev", "--dir", str(repo))
        projects = (self.home / ".sage-is" / "projects").read_text().splitlines()
        self.assertIn(f"trellis={repo}", projects)
        self.assertEqual(self.ok("dev", "--where").stdout.strip(), str(repo))

    def test_a_folder_that_is_not_trellis_is_refused(self):
        other = self.home / "notes"
        other.mkdir()
        result = self.run_cli("dev", "--dir", str(other))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a Trellis checkout", result.stdout)
        self.assertEqual(self.docker_runs(), [])

    def test_a_checkout_outside_home_is_refused(self):
        outside = self.home.parent / "elsewhere"
        (outside / ".git").mkdir(parents=True)
        (outside / "manage.py").write_text("")
        result = self.run_cli("dev", "--dir", str(outside))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside your home folder", result.stdout)

    def test_stop_removes_only_the_dev_container(self):
        repo = self.checkout()
        self.ok("start")
        self.ok("dev", "--dir", str(repo))
        self.ok("dev", "--stop")
        self.assertFalse((self.state / "container-trellis-crm-dev").exists())
        self.assertTrue((self.state / "up-trellis-crm").exists())


if __name__ == "__main__":
    unittest.main()
