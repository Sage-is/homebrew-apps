"""trellis-crm in server mode: the settings file, the docker run, backups, boot and nuke."""

import base64
import hashlib
import json
import os
import plistlib
import pty
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import termios
import unittest
from pathlib import Path

TRELLIS_CRM = Path(__file__).resolve().parent.parent / "trellis-crm"
FORMULA = TRELLIS_CRM.parent / "Formula" / "trellis-crm.rb"
LIB = TRELLIS_CRM.parent / "lib" / "sage-runtime.sh"
# What a container needs to reach this machine as host.docker.internal on every runtime.
HOST_ENTRY = "--add-host=host.docker.internal:host-gateway"
# krunkit's dependencies come from its tap too, and Homebrew 7 refuses each one from an untrusted tap.
KRUNKIT_INSTALL = "brew tap libkrun/krun && brew trust libkrun/krun && brew install krunkit"
# What a krunkit VM needs in Lima's override file: virtiofs (abiosoft/colima#1607),
# and a boot step that mounts its data disk (abiosoft/colima#1614).
VIRTIOFS = ("# Colima 0.10.3 hands Lima 9p for krunkit, and Lima 2.2 refuses it: abiosoft/colima#1607\n"
            "mountType: virtiofs\n")
DATA_DISK_STEP = """\
  - mode: dependency
    script: |
      #!/bin/sh
      for link in /dev/disk/by-label/lima-*; do
        [ -e "$link" ] || continue
        dir="/mnt/${link##*/}"
        mountpoint -q "$dir" || { mkdir -p "$dir" && mount "$link" "$dir"; }
      done
"""
DATA_DISK = ("# krunkit puts the data disk on vdc, where Lima 2.2 looks for vdb; mount it by label: abiosoft/colima#1614\n"
             "provision:\n" + DATA_DISK_STEP)

# Stand-ins log each call. Docker keeps a little state as marker files: the
# runtime answering, the image, the network, the volume, the container. The
# number in $STATE/rm-fails is how many `volume rm` calls fail before one works,
# as when a container just removed still holds the volume. `exec ... manage.py
# mailbox` writes its argv NUL-separated to $STATE/exec-argv and a digest of its
# stdin, never the bytes, to $STATE/exec-stdin, and exits with $STATE/mailbox-exit.
STUBS = {
    # Like docker: --context, else DOCKER_CONTEXT, else the saved context.
    "docker": """ctx="${DOCKER_CONTEXT:-$(cat "$STATE/current-context" 2>/dev/null || echo default)}"
if [[ "${1:-}" == --context ]]; then ctx="$2"; shift 2; fi
echo "docker $ctx $*" >> "$STATE/calls"
case "$1 ${2:-}" in
  "info "*)          [[ -e "$STATE/running-$ctx" ]] ;;
  "context show")    echo "$ctx" ;;
  "context "*)       ;;
  "image inspect")   [[ -e "$STATE/image" ]] ;;
  "pull "*)          [[ ! -e "$STATE/pull-denied" ]] && touch "$STATE/image" ;;
  "network inspect") [[ -e "$STATE/network" ]] ;;
  "network create")  touch "$STATE/network" ;;
  "volume inspect")  [[ -e "$STATE/volume" ]] ;;
  "volume rm")       fails="$(cat "$STATE/rm-fails" 2>/dev/null || echo 0)"
                     if (( fails > 0 )); then echo $(( fails - 1 )) > "$STATE/rm-fails"; exit 1; fi
                     rm -f "$STATE/volume" ;;
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
    if [[ "$*" == *" manage.py mailbox"* ]]; then
      printf '%s\\0' "$@" > "$STATE/exec-argv"
      shasum -a 256 | cut -d' ' -f1 > "$STATE/exec-stdin"
      [[ " $* " != *" --stdin "* ]] || echo stored
      exit "$(cat "$STATE/mailbox-exit" 2>/dev/null || echo 0)"
    fi
    if [[ "$2" == -i ]]; then cat > /dev/null; fi
    if [[ "$*" == *" tar "* ]]; then printf 'TARDATA'; fi ;;
esac""",
    # Lima's home is where Colima 0.10.3 looks (config/files.go). A start keeps Lima's override
    # file as it stood then, since the VM reads it at that moment, and a new VM gets Lima's file
    # with its type. A krunkit VM comes back with Docker on its root disk, $STATE/root-disk, on
    # any boot after its first, unless the override mounts its disk by label
    # (abiosoft/colima#1614). ssh runs the command as if inside the VM, and reads stdin to pass
    # it on, as ssh does.
    "colima": r"""echo "colima $*" >> "$STATE/calls"
home="$HOME/.colima"
if [[ ! -e "$home" && -n "${XDG_CONFIG_HOME:-}" ]]; then home="$XDG_CONFIG_HOME/colima"; fi
if [[ -d "${COLIMA_HOME:-}" ]]; then home="$COLIMA_HOME"; fi
lima="${LIMA_HOME:-$home/_lima}"
case "$1" in
  list)  if [[ -e "$STATE/colima-vm" ]]; then echo '{"name":"default","status":"Stopped"}'; fi ;;
  start) rm -f "$STATE/root-disk"
         if [[ -e "$STATE/colima-vm" ]] && grep -q '^vmType: krunkit' "$lima/colima/lima.yaml" 2>/dev/null \
            && ! grep -q 'by-label/lima-' "$lima/_config/override.yaml" 2>/dev/null; then
           touch "$STATE/root-disk"
         fi
         if [[ " $* " == *" --vm-type "* ]]; then
           all="$*"; type="${all##*--vm-type }"
           mkdir -p "$lima/colima"; echo "vmType: ${type%% *}" > "$lima/colima/lima.yaml"
         fi
         cp "$lima/_config/override.yaml" "$STATE/override-at-start" 2>/dev/null
         touch "$STATE/colima-vm" "$STATE/running-colima" ;;
  stop)  rm -f "$STATE/running-colima" ;;
  ssh)   cat > /dev/null
         [[ ! -e "$STATE/fail-ssh" ]] || exit 255
         while [[ $# -gt 0 && "$1" != -- ]]; do shift; done
         shift; "$@" ;;
esac""",
    # Inside the VM: Docker's folder comes from the data disk, from the root disk after
    # abiosoft/colima#1614 struck, or is no mount at all on a VM made before Colima had a data
    # disk ($STATE/one-disk). $STATE/no-findmnt: a VM without findmnt.
    "findmnt": r"""[[ ! -e "$STATE/no-findmnt" ]] || exit 127
case "${@: -1}" in
  /var/lib/docker) [[ ! -e "$STATE/one-disk" ]] || exit 1
                   if [[ -e "$STATE/root-disk" ]]; then echo "/dev/vda1[/mnt/lima-colima/docker]"
                   else echo "/dev/vdc1[/docker]"; fi ;;
  /)               echo /dev/vda1 ;;
esac""",
    "sysctl": """case "$2" in
  hw.memsize) echo $(( ${MAC_RAM_GIB:-32} * 1073741824 )) ;;
  hw.ncpu)    echo "${MAC_CORES:-10}" ;;
esac""",
    "uname": """if [[ "${1:-}" == -m ]]; then echo "${MAC_ARCH:-arm64}"; else exec /usr/bin/uname "$@"; fi""",
    # git is logged; a stub answers no repository, so nothing is found by remote.
    "git": 'echo "git $*" >> "$STATE/calls"; case "$1" in rev-parse|remote) exit 1 ;; esac',
    "curl": """echo "curl $*" >> "$STATE/calls"
echo '{"ok": true, "worker": "on"}'""",
    # launchd starts Colima as it loads the daemon (RunAtLoad).
    "sudo": """echo "sudo $*" >> "$STATE/calls"
[[ "$*" != "launchctl bootstrap "* ]] || colima start --foreground""",
    "open": 'echo "open $*" >> "$STATE/calls"',
}


@unittest.skipUnless(sys.platform == "darwin", "server mode targets a Mac")
class Mac(unittest.TestCase):
    """A fake Mac: its own home, apps folder, LaunchDaemons folder and stand-in commands."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = self.root = Path(scratch.name)
        self.home, self.state, self.daemons = root / "home", root / "state", root / "LaunchDaemons"
        self.bin = root / "bin"
        for folder in (self.home, self.state, self.daemons, self.bin, root / "Applications"):
            folder.mkdir()
        for name, body in STUBS.items():
            self.stub(name, body)
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin",
                    "STATE": str(self.state), "APPLICATIONS_DIR": str(root / "Applications"),
                    "LAUNCH_DAEMONS_DIR": str(self.daemons)}
        self.env_file = self.home / ".sage-is" / "trellis-crm.env"
        self.backups = self.home / ".sage-is" / "backups" / "trellis-crm"
        self.override = self.home / ".colima" / "_lima" / "_config" / "override.yaml"

    def stub(self, name, body):
        stub = self.bin / name
        stub.write_text("#!/bin/bash\n" + body + "\n")
        stub.chmod(0o755)

    def colima(self, *args):
        """Colima, run by hand or by launchd."""
        subprocess.run([self.bin / "colima", *args], env=self.env, check=True, capture_output=True)

    def run_cli(self, *args, answer=None, **env):
        """Run trellis-crm; with `answer`, stdin is a terminal that types it."""
        if answer is None:
            return subprocess.run([TRELLIS_CRM, *args], env=self.env | env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=30)
        terminal, typist = pty.openpty()
        os.write(terminal, answer.encode())
        try:
            return subprocess.run([TRELLIS_CRM, *args], env=self.env | env, capture_output=True, text=True,
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
        for part in ("--name trellis-crm", "--restart unless-stopped", "--network sage-network", HOST_ENTRY,
                     "-p 127.0.0.1:8030:8000", "-v trellis-crm-data:/data",
                     f"--env-file {self.env_file}", "ghcr.io/sage-is/trellis:latest"):
            self.assertIn(part, run)

    def test_the_first_admin_password_is_never_printed_and_the_first_start_says_where_it_is(self):
        # What start prints lands in terminals, transcripts and sage-box's report.
        first = self.ok("start")
        password = self.settings()["DJANGO_SUPERUSER_PASSWORD"]
        self.assertNotIn(password, first.stdout + first.stderr)
        self.assertFalse([call for call in self.calls() if password in call])
        (line,) = [line for line in first.stdout.splitlines() if "Sign in as" in line]
        self.assertIn("Sign in as admin", line)
        self.assertIn("DJANGO_SUPERUSER_PASSWORD", line)
        self.assertIn(str(self.env_file), line)
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
        self.assertTrue([call for call in self.calls() if call.startswith("colima start --profile default --vm-type ")])
        self.assertEqual((self.home / ".sage-is" / "runtime").read_text().strip(), "colima")


class DemoSwitches(Mac):
    """The real Trellis holds real records: a demo's switches never reach it."""

    def test_start_refuses_a_demo_switch_in_the_settings_before_it_changes_anything(self):
        self.ok("start")
        settings = self.env_file.read_text()
        runs = len(self.docker_runs())
        for text, names in (("LOAD_FIXTURES=1", "LOAD_FIXTURES"), ("LOAD_FIXTURES=0", "LOAD_FIXTURES"),
                            ("LOAD_FIXTURES=", "LOAD_FIXTURES"), ("LOAD_FIXTURES", "LOAD_FIXTURES"),
                            ("DEMO_RESET=2026-10-06", "DEMO_RESET"), ("DEMO_RESET", "DEMO_RESET"),
                            ("DEMO_RESET_HOURS=24", "DEMO_RESET_HOURS"), ("DEMO_RESET_HOURS", "DEMO_RESET_HOURS"),
                            ("  LOAD_FIXTURES=1", "LOAD_FIXTURES"),  # docker trims the space
                            ("DEMO_RESET_HOURS=24\nLOAD_FIXTURES=1", "LOAD_FIXTURES, DEMO_RESET_HOURS")):
            with self.subTest(text=text):
                self.env_file.write_text(settings + text + "\n")
                result = self.run_cli("start", "--port", "9000")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)
                self.assertIn(f"sets {names}. ", result.stdout)
                self.assertIn("trellis-crm dev", result.stdout)
                self.assertEqual(self.env_file.read_text(), settings + text + "\n", "start saved its options")
                self.assertEqual(len(self.docker_runs()), runs)
                self.assertTrue((self.state / "up-trellis-crm").exists(), "the running Trellis was taken down")
        # An update ends in a start, so it stops there too, with the running Trellis left up.
        self.env_file.write_text(settings + "LOAD_FIXTURES=1\n")
        result = self.run_cli("update")
        self.assertEqual(result.returncode, 1)
        self.assertIn("sets LOAD_FIXTURES. ", result.stdout)
        self.assertTrue((self.state / "up-trellis-crm").exists())
        self.assertEqual(len(self.docker_runs()), runs)

    def test_only_the_switches_themselves_count(self):
        self.ok("start")
        near_misses = "# LOAD_FIXTURES=1\nLOAD_FIXTURES_NOTE=1\nMY_DEMO_RESET=1\nDEMO_RESET_HOURS_NOTE=1\n"
        self.env_file.write_text(self.env_file.read_text() + near_misses)
        self.ok("start")

    def test_the_real_container_is_told_fixtures_are_off_after_the_settings_file(self):
        self.ok("start")
        (run,) = self.docker_runs()
        self.assertIn(f"--env-file {self.env_file} -e LOAD_FIXTURES=0 ghcr.io/sage-is/trellis:latest", run)


class Logins(Mac):
    """Docker Desktop's credsStore leads through its own helper, which only wraps the Keychain one."""

    def setUp(self):
        super().setUp()
        for helper in ("desktop", "osxkeychain"):
            self.stub(f"docker-credential-{helper}", "exit 0")
        (Path(self.env["APPLICATIONS_DIR"]) / "Docker.app").mkdir()
        self.config = self.home / ".docker" / "config.json"
        self.config.parent.mkdir()
        self.config.write_text(json.dumps({"auths": {"ghcr.io": {}}, "credsStore": "desktop"}))

    def test_with_colima_in_use_the_keychain_keeps_the_logins_and_docker_desktop_stays(self):
        result = self.ok("start")
        self.assertEqual(json.loads(self.config.read_text()), {"auths": {"ghcr.io": {}}, "credsStore": "osxkeychain"})
        (backup,) = self.config.parent.glob("config.json.*.bak")
        self.assertEqual(json.loads(backup.read_text())["credsStore"], "desktop")
        self.assertIn(f"now osxkeychain (backup: {backup})", result.stdout)

    def test_with_docker_desktop_in_use_its_helper_stays(self):
        (self.state / "running-desktop-linux").touch()
        self.ok("start", "--runtime", "docker-desktop")
        self.assertEqual(json.loads(self.config.read_text())["credsStore"], "desktop")


class Shell(Mac):
    """docker obeys an exported DOCKER_HOST over its context, and Colima's bare start obeys COLIMA_PROFILE."""

    def test_what_the_shell_exports_steers_neither_docker_nor_colima(self):
        for name in ("docker", "colima"):
            self.stub(name, f'echo "{name} ${{DOCKER_HOST-unset}} ${{COLIMA_PROFILE-unset}}" >> "$STATE/env"\n'
                      + STUBS[name])
        self.ok("start", DOCKER_HOST="unix:///tmp/elsewhere.sock", COLIMA_PROFILE="build")
        self.assertEqual(set((self.state / "env").read_text().splitlines()), {"docker unset unset", "colima unset unset"})


class NewVM(Mac):
    """The first start makes Colima's VM: krunkit on Apple Silicon, vz without it."""

    def start_new_vm(self, **mac):
        """Start Trellis where Colima has no VM yet."""
        for marker in ("colima-vm", "running-colima", "override-at-start"):
            (self.state / marker).unlink(missing_ok=True)
        return self.ok("start", **mac)

    def create(self, **mac):
        """Start Trellis where Colima has no VM yet; return the output and the call that made the VM."""
        out = self.start_new_vm(**mac).stdout
        return out, [call for call in self.calls() if call.startswith("colima start")][-1]

    def test_apple_silicon_with_krunkit_gets_a_krunkit_vm_on_virtiofs_that_mounts_its_data_disk(self):
        self.stub("krunkit", "exit 0")
        out, start = self.create()
        self.assertEqual(start, "colima start --profile default --vm-type krunkit --mount-type virtiofs "
                                "--mount-inotify --memory 12 --cpu 5 --disk 100")
        self.assertNotIn(KRUNKIT_INSTALL, out)
        self.assertEqual((self.state / "override-at-start").read_text(), VIRTIOFS + DATA_DISK)

    def test_apple_silicon_without_krunkit_says_how_to_get_it_and_uses_vz_with_rosetta(self):
        out, start = self.create()
        self.assertEqual(start, "colima start --profile default --vm-type vz --vz-rosetta --mount-type virtiofs "
                                "--mount-inotify --memory 8 --cpu 5 --disk 100")
        (warning,) = [line for line in out.splitlines() if "krunkit" in line]
        self.assertIn("default", warning)
        self.assertIn(KRUNKIT_INSTALL, warning)
        self.assertFalse(self.override.exists())

    def test_an_intel_mac_gets_vz_without_rosetta(self):
        self.stub("krunkit", "exit 0")
        out, start = self.create(MAC_ARCH="x86_64")
        self.assertEqual(start, "colima start --profile default --vm-type vz --mount-type virtiofs --mount-inotify "
                                "--memory 8 --cpu 5 --disk 100")
        self.assertNotIn("krunkit", out)
        self.assertFalse(self.override.exists())

    def test_krunkit_takes_half_the_memory_and_vz_a_third_within_bounds(self):
        sizes = {("8", "2"): (4, 4, 2), ("16", "4"): (8, 5, 2), ("64", "20"): (12, 8, 8)}
        for (ram, cores), (krunkit_memory, vz_memory, cpus) in sizes.items():
            with self.subTest(ram=ram, cores=cores):
                self.stub("krunkit", "exit 0")
                _, start = self.create(MAC_RAM_GIB=ram, MAC_CORES=cores)
                self.assertTrue(start.endswith(f" --memory {krunkit_memory} --cpu {cpus} --disk 100"), start)
                (self.bin / "krunkit").unlink()
                _, start = self.create(MAC_RAM_GIB=ram, MAC_CORES=cores)
                self.assertTrue(start.endswith(f" --memory {vz_memory} --cpu {cpus} --disk 100"), start)

    def test_without_sysctl_on_the_path_either_vm_gets_the_floor(self):
        (self.bin / "sysctl").unlink()  # the real one is in /usr/sbin, which this PATH leaves out
        _, vz = self.create()
        self.stub("krunkit", "exit 0")
        _, krunkit = self.create()
        self.assertIn("--vm-type krunkit", krunkit)
        for start in (vz, krunkit):
            self.assertTrue(start.endswith(" --memory 4 --cpu 2 --disk 100"), start)

    def test_the_override_keeps_what_it_held_and_each_fix_lands_once(self):
        self.stub("krunkit", "exit 0")
        self.override.parent.mkdir(parents=True)
        for held in ("cpuType: host\n", "cpuType: host"):  # an editor may leave off the last newline
            with self.subTest(held=held):
                self.override.write_text(held)
                self.create()
                self.create()
                self.assertEqual(self.override.read_text(), "cpuType: host\n" + VIRTIOFS + DATA_DISK)

    def test_a_mount_type_the_override_already_sets_stays(self):
        self.stub("krunkit", "exit 0")
        self.override.parent.mkdir(parents=True)
        self.override.write_text("mountType: 9p\n")
        out, _ = self.create()
        self.assertEqual(self.override.read_text(), "mountType: 9p\n" + DATA_DISK)
        self.assertNotIn("QEMU", out)

    def test_only_a_top_level_mount_type_counts(self):
        # Neither sets Lima's mountType: skipping on one would let Colima's 9p reach the krunkit VM.
        self.stub("krunkit", "exit 0")
        self.override.parent.mkdir(parents=True)
        for held in ("# mountType: 9p\n", "vmOpts:\n  mountType: 9p\n"):
            with self.subTest(held=held):
                self.override.write_text(held)
                self.create()
                self.assertEqual(self.override.read_text(), held + VIRTIOFS + DATA_DISK)

    def test_an_override_that_mounts_the_disks_by_label_gets_no_second_step(self):
        # One written by hand words its notes and its step its own way.
        self.stub("krunkit", "exit 0")
        self.override.parent.mkdir(parents=True)
        held = ("mountType: virtiofs\nprovision:\n  - mode: dependency\n    script: |\n"
                "      mount /dev/disk/by-label/lima-colima /mnt/lima-colima\n")
        self.override.write_text(held)
        out, _ = self.create()
        self.assertEqual(self.override.read_text(), held)
        self.assertNotIn(str(self.override), out)

    def test_an_override_with_its_own_provision_list_gets_the_step_to_add_by_hand(self):
        # A second provision key would break the file.
        self.stub("krunkit", "exit 0")
        self.override.parent.mkdir(parents=True)
        held = "provision:\n  - mode: system\n    script: echo hi\n"
        self.override.write_text(held)
        result = self.start_new_vm()
        self.assertEqual(self.override.read_text(), held + VIRTIOFS)
        self.assertIn(f"{self.override} has its own provision list. Add this step to it", result.stderr)
        self.assertIn(DATA_DISK_STEP, result.stderr)

    def test_adding_the_settings_names_the_file_and_what_each_does(self):
        self.stub("krunkit", "exit 0")
        out, _ = self.create()
        virtiofs, data_disk = [line for line in out.splitlines() if str(self.override) in line]
        self.assertIn("QEMU", virtiofs)
        self.assertIn("mounts the VM's data disk (abiosoft/colima#1614)", data_disk)

    def assert_the_vm_read_the_override(self):
        self.assertEqual((self.state / "override-at-start").read_text(), VIRTIOFS + DATA_DISK)

    def test_colima_home_moves_the_override(self):
        self.stub("krunkit", "exit 0")
        colima_home = self.home / "colima-elsewhere"
        self.create(COLIMA_HOME=str(colima_home))
        self.assertEqual((colima_home / "_lima" / "_config" / "override.yaml").read_text(), VIRTIOFS + DATA_DISK)
        self.assert_the_vm_read_the_override()
        self.assertFalse(self.override.exists())

    def test_lima_home_moves_the_override(self):
        self.stub("krunkit", "exit 0")
        self.create(LIMA_HOME=str(self.home / "lima"))
        self.assert_the_vm_read_the_override()
        self.assertFalse((self.home / ".colima").exists())

    def test_xdg_config_home_moves_the_override_without_making_a_colima_folder(self):
        # Colima ignores $XDG_CONFIG_HOME, and the profiles kept there, once ~/.colima exists.
        self.stub("krunkit", "exit 0")
        self.create(XDG_CONFIG_HOME=str(self.home / ".config"))
        self.assert_the_vm_read_the_override()
        self.assertFalse((self.home / ".colima").exists())

    def test_an_existing_colima_folder_beats_xdg_config_home(self):
        self.stub("krunkit", "exit 0")
        (self.home / ".colima").mkdir()
        self.create(XDG_CONFIG_HOME=str(self.home / ".config"))
        self.assert_the_vm_read_the_override()
        self.assertFalse((self.home / ".config").exists())

    def test_an_existing_vm_starts_as_it_is(self):
        self.stub("krunkit", "exit 0")
        (self.state / "colima-vm").touch()
        self.ok("start")
        self.assertIn("colima start", self.calls())
        self.assertNotIn("--vm-type", " ".join(self.calls()))
        self.assertFalse(self.override.exists())


class DataDisk(Mac):
    """Docker keeps Trellis's image and volume on the VM's data disk, which a krunkit VM leaves
    unmounted after its first boot unless the override's step mounts it (abiosoft/colima#1614)."""

    def test_a_krunkit_vm_keeps_docker_on_its_data_disk_after_a_restart(self):
        # launchd restarts the VM at every power-on (trellis-crm boot).
        self.stub("krunkit", "exit 0")
        self.ok("start")
        for step in ("stop", "start"):
            self.colima(step)
        self.ok("start")
        self.assertFalse((self.state / "root-disk").exists())

    def test_docker_on_the_root_disk_stops_trellis_before_it_touches_docker_and_a_restart_mends_it(self):
        # A krunkit VM an older ai-ui made, and saved the runtime for, without the boot step.
        (self.home / ".sage-is").mkdir()
        (self.home / ".sage-is" / "runtime").write_text("colima\n")
        self.override.parent.mkdir(parents=True)
        self.colima("start", "--vm-type", "krunkit")
        for launchd_started_it in (False, True):
            with self.subTest(launchd_started_it=launchd_started_it):
                self.override.write_text(VIRTIOFS)
                self.colima("stop")
                if launchd_started_it:
                    self.colima("start")
                before = len(self.calls())
                result = self.run_cli("start")
                self.assertEqual(result.returncode, 1)
                self.assertIn("Error: Docker in the colima VM runs on the VM's root disk, not its data disk",
                              result.stderr)
                self.assertIn("then run this again: colima restart", result.stderr)
                # Only questions: no image, volume, network or container.
                self.assertEqual([call for call in self.calls()[before:] if call.startswith("docker ")
                                  and call.split()[2] not in ("info", "context")], [])
                self.assertEqual(self.override.read_text(), VIRTIOFS + DATA_DISK)
        for step in ("stop", "start"):  # colima restart
            self.colima(step)
        self.ok("start")
        self.assertEqual(len(self.docker_runs()), 1)

    def test_only_a_krunkit_vm_gets_the_boot_step(self):
        # vz mounts its data disk itself, so the step would not mend what put Docker on the root disk.
        self.colima("start", "--vm-type", "vz")
        (self.state / "root-disk").touch()
        result = self.run_cli("start")
        self.assertEqual(result.returncode, 1)
        self.assertIn("then run this again: colima restart", result.stderr)
        self.assertFalse(self.override.exists())

    def test_a_vm_without_a_data_disk_passes_and_a_question_with_no_answer_only_warns(self):
        # Colima before its data disk kept Docker on the root disk by design.
        (self.state / "one-disk").touch()
        self.assertNotIn("could not ask", self.ok("start").stderr)
        for fault in ("fail-ssh", "no-findmnt"):
            with self.subTest(fault):
                (self.state / fault).touch()
                result = self.ok("start")
                self.assertIn("Warning: could not ask the colima VM which disk Docker uses", result.stderr)
                (self.state / fault).unlink()
        self.assertEqual(len(self.docker_runs()), 3)

    def test_docker_desktop_is_not_asked(self):
        # The fault is Colima's, and asking a VM that is not in use would only warn.
        (self.state / "running-desktop-linux").touch()
        self.ok("start", "--runtime", "docker-desktop")
        self.assertFalse([call for call in self.calls() if call.startswith("colima ")])


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
        self.assertIn("docker colima network create sage-network", self.calls())
        self.assertIn("docker colima network connect sage-network sage-ai", self.calls())
        self.ok("start")
        self.assertEqual(self.calls().count("docker colima network create sage-network"), 1)

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

    def test_the_daemon_finds_colima_where_this_shell_does(self):
        # Colima picks its home from these; without them the daemon would start a new VM in ~/.colima.
        where = {"COLIMA_HOME": str(self.home / "colima"), "LIMA_HOME": str(self.home / "lima"),
                 "XDG_CONFIG_HOME": str(self.home / ".config")}
        plist = plistlib.loads(self.ok("boot", "--print", **where).stdout.encode())
        for key, value in where.items():
            self.assertEqual(plist["EnvironmentVariables"].get(key), value, key)
        plain = plistlib.loads(self.ok("boot", "--print").stdout.encode())
        self.assertEqual(set(plain["EnvironmentVariables"]), {"HOME", "PATH"})

    def test_install_hands_the_plist_to_launchd(self):
        self.ok("boot")
        calls = self.calls()
        self.assertTrue([call for call in calls if call.startswith("sudo install -m 644 -o root -g wheel ")])
        self.assertIn("colima stop", calls)
        self.assertEqual([call for call in calls if call.startswith("sudo ")][-1],
                         f"sudo launchctl bootstrap system {self.daemons}/is.sage.colima.plist")

    def test_a_krunkit_vm_gets_its_boot_step_before_launchd_restarts_it(self):
        # A VM made without the step would come back from that restart, and every power-on, on its root disk.
        self.override.parent.mkdir(parents=True)
        for vm_type, after in (("vz", VIRTIOFS), ("krunkit", VIRTIOFS + DATA_DISK)):
            with self.subTest(vm_type):
                self.override.write_text(VIRTIOFS)
                self.colima("start", "--vm-type", vm_type)
                self.ok("boot", "--print")
                self.assertEqual(self.override.read_text(), VIRTIOFS)
                self.ok("boot")
                self.assertEqual(self.override.read_text(), after)
                self.assertEqual((self.state / "override-at-start").read_text(), after)
                self.assertFalse((self.state / "root-disk").exists())

    def test_a_krunkit_vm_stays_out_of_launchd_until_its_own_provision_list_has_the_step(self):
        # YAML refuses a second provision list, so the step is printed to add by hand.
        self.override.parent.mkdir(parents=True)
        held = VIRTIOFS + "provision:\n  - mode: system\n    script: echo hi\n"
        self.override.write_text(held)
        self.colima("start", "--vm-type", "krunkit")
        before = len(self.calls())
        result = self.run_cli("boot")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Add the step above to {self.override}, then run this again.", result.stdout)
        self.assertIn(DATA_DISK_STEP, result.stderr)
        self.assertEqual([call for call in self.calls()[before:] if call.startswith(("sudo ", "colima "))], [])
        self.assertEqual(self.override.read_text(), held)
        self.override.write_text(held + DATA_DISK_STEP)
        self.ok("boot")
        self.assertFalse((self.state / "root-disk").exists())

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

    def test_every_dev_option_is_listed_and_the_formula_still_finds_its_line(self):
        usage = self.ok("--help").stdout
        self.assertIn("dev    [--dir DIR]", usage)  # the test block of Formula/trellis-crm.rb reads this line
        # The synopsis runs from "  dev" to the first description line, which sits 24 columns in.
        synopsis = re.search(r"^  dev    .*?(?=^ {24})", usage, re.M | re.S).group(0)
        for option in ("[--dir DIR]", "[--port PORT]", "[--profile NAME]", "[--env-file FILE]", "[--reset]",
                       "[--link-ai-ui]", "[--where]", "[--stop]"):
            self.assertIn(option, synopsis)


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
        self.assertIn(HOST_ENTRY, dev)
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

    def dev_env(self, text, mode=0o600):
        path = self.home / ".sage-is" / "trellis-dev.env"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return path

    def link_env(self, more=""):
        """The env file --link-ai-ui needs: the dev install's own AI-UI key and a test Space."""
        return self.dev_env("AI_UI_API_KEY=ai-key-value-77\nNOTIFY_SPACE_ID=space-9\n" + more)

    def test_an_env_file_reaches_the_container_and_no_value_reaches_a_command_line(self):
        repo = self.checkout()
        path = self.dev_env("CRM_API_KEY=dev-key-value-42\nDEMO_STORY=1\n# a comment\n")
        result = self.ok("dev", "--dir", str(repo), "--env-file", str(path))
        (dev,) = self.docker_runs()
        self.assertIn(f"--env-file {path}", dev)
        self.assertFalse([c for c in self.calls() if "dev-key-value-42" in c])
        self.assertNotIn("dev-key-value-42", result.stdout)
        self.assertIn("CRM_API_KEY DEMO_STORY", result.stdout)

    def test_an_env_file_others_can_read_is_refused(self):
        repo = self.checkout()
        result = self.run_cli("dev", "--dir", str(repo), "--env-file", str(self.dev_env("DEMO_STORY=1\n", 0o644)))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("chmod 600", result.stdout)
        self.assertEqual(self.docker_runs(), [])

    def test_mail_settings_are_refused_with_the_ai_ui_link_and_without_it(self):
        repo = self.checkout()
        for options in ((), ("--link-ai-ui",)):
            for line in ("IMAP_DEV_PASSWORD=x", "SMTP_PASSWORD=x"):
                with self.subTest(line=line, options=options):
                    path = self.dev_env(line + "\n")
                    result = self.run_cli("dev", "--dir", str(repo), "--env-file", str(path), *options)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"sets {line.split('=')[0]}", result.stdout)
                    self.assertIn("Dev mode takes only", result.stdout)
                    self.assertNotIn("--link-ai-ui", result.stdout, "the link would not mend a mail setting")
        self.assertEqual(self.docker_runs(), [])

    def test_the_ai_ui_settings_are_refused_without_the_link_and_the_error_names_it(self):
        repo = self.checkout()
        for name in ("AI_UI_URL", "AI_UI_API_KEY", "NOTIFY_SPACE_ID"):
            with self.subTest(name=name):
                result = self.run_cli("dev", "--dir", str(repo), "--env-file", str(self.dev_env(f"{name}=x\n")))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"sets {name}", result.stdout)
                self.assertIn("trellis-crm dev --link-ai-ui", result.stdout)
        self.assertEqual(self.docker_runs(), [])
        self.assertFalse([c for c in self.calls() if " network " in c], "refused after docker was touched")

    def test_the_link_lets_the_env_file_carry_the_ai_ui_key_and_space(self):
        repo = self.checkout()
        path = self.link_env("DEMO_STORY=1\n")
        result = self.ok("dev", "--dir", str(repo), "--link-ai-ui", "--env-file", str(path))
        (dev,) = self.docker_runs()
        self.assertIn(f"--env-file {path}", dev)
        self.assertIn("--network sage-network", dev)
        self.assertFalse([c for c in self.calls() if "ai-key-value-77" in c])
        self.assertNotIn("ai-key-value-77", result.stdout)
        self.assertIn("AI_UI_API_KEY NOTIFY_SPACE_ID DEMO_STORY", result.stdout)

    def test_the_link_joins_the_shared_network_and_connects_ai_ui(self):
        repo = self.checkout()
        (self.state / "container-sage-ai").touch()
        result = self.ok("dev", "--dir", str(repo), "--link-ai-ui", "--env-file", str(self.link_env()))
        (dev,) = self.docker_runs()
        self.assertIn("--name trellis-crm-dev", dev)
        self.assertIn("--network sage-network", dev)
        calls = self.calls()
        for step in ("docker colima network create sage-network", "docker colima network connect sage-network sage-ai"):
            self.assertIn(step, calls)
            self.assertLess(calls.index(step), calls.index(dev), f"{step} came after the dev container")
        self.assertIn("Linked to AI-UI", result.stdout)
        self.assertIn("http://trellis-crm-dev:8000", result.stdout)
        self.assertIn("http://sage-ai:8080", result.stdout)
        self.assertNotIn("ai-ui start", result.stdout)

    def test_the_link_shares_the_network_the_real_trellis_made(self):
        repo = self.checkout()
        (self.state / "container-sage-ai").touch()
        self.ok("start")
        self.ok("dev", "--dir", str(repo), "--link-ai-ui", "--env-file", str(self.link_env()))
        self.assertEqual(self.calls().count("docker colima network create sage-network"), 1)
        real, dev = self.docker_runs()
        for run in (real, dev):
            self.assertIn("--network sage-network", run)
        self.assertTrue((self.state / "up-trellis-crm").exists(), "the real Trellis stopped")

    def test_without_the_link_the_dev_install_stays_off_the_network(self):
        repo = self.checkout()
        (self.state / "container-sage-ai").touch()
        self.ok("dev", "--dir", str(repo))
        (dev,) = self.docker_runs()
        self.assertNotIn("--network", dev)
        self.assertFalse([c for c in self.calls() if " network " in c])

    def test_the_link_without_ai_ui_running_says_how_to_connect_it_later(self):
        repo = self.checkout()
        result = self.ok("dev", "--dir", str(repo), "--link-ai-ui", "--env-file", str(self.link_env()))
        self.assertIn("--network sage-network", self.docker_runs()[0])
        self.assertFalse([c for c in self.calls() if "network connect" in c])
        self.assertIn("sage-ai is not here yet", result.stdout)
        self.assertIn("trellis-crm dev --link-ai-ui again", result.stdout)
        self.assertNotIn("Linked to AI-UI", result.stdout, "it claimed a link to an AI-UI that is not there")
        self.assertNotIn("Notices go to", result.stdout, "it claimed a delivery to an AI-UI that is not there")

    def test_a_line_that_is_not_name_value_is_refused_by_its_number_and_never_printed(self):
        repo = self.checkout()
        secret = "leaked-secret-token-12345"
        bad = {"a blank value": "CRM_API_KEY=", "a value of spaces": "CRM_API_KEY=   ", "a bare name": "CRM_API_KEY",
               "no name": f"={secret}", "no equals sign": secret, "a space in the name": f"export CRM_API_KEY={secret}",
               "an indented line": f"  CRM_API_KEY={secret}", "a carriage return": f"CRM_API_KEY={secret}\r"}
        for what, line in bad.items():
            for options in ((), ("--link-ai-ui",)):
                with self.subTest(what, options=options):
                    path = self.dev_env(f"# keys\n\nDEMO_STORY=1\n{line}\nDEMO_PASSWORD=x\n")
                    result = self.run_cli("dev", "--dir", str(repo), "--env-file", str(path), *options)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("line 4 is not NAME=value", result.stdout)
                    self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(self.docker_runs(), [])
        # Comments, blank lines, a last line with no newline and a value with spaces or = in it are fine.
        path = self.dev_env("# keys\n\n   \n  # an indented note\nCRM_API_KEY=a=b c\nDEMO_STORY=1")
        self.ok("dev", "--dir", str(repo), "--env-file", str(path))

    def test_the_ai_ui_address_is_never_in_the_file_and_the_link_sets_it(self):
        repo = self.checkout()
        address = "AI_UI_URL=http://elsewhere.example:9999\n"
        for options, text in (((), address), (("--link-ai-ui",), "AI_UI_API_KEY=k\nNOTIFY_SPACE_ID=s\n" + address)):
            with self.subTest(options=options):
                path = self.dev_env(text)
                result = self.run_cli("dev", "--dir", str(repo), "--env-file", str(path), *options)
                self.assertEqual(result.returncode, 1)
                self.assertIn("sets AI_UI_URL", result.stdout)
                self.assertIn("trellis-crm dev --link-ai-ui sets it, to http://sage-ai:8080", result.stdout)
        self.assertEqual(self.docker_runs(), [])
        self.ok("dev", "--dir", str(repo), "--link-ai-ui", "--env-file", str(self.link_env()))
        self.assertIn("-e AI_UI_URL=http://sage-ai:8080", self.docker_runs()[-1])
        self.ok("dev", "--dir", str(repo))
        self.assertNotIn("AI_UI_URL", self.docker_runs()[-1])
        usage = " ".join(self.ok("--help").stdout.split())
        self.assertIn("needs the file to carry AI_UI_API_KEY and NOTIFY_SPACE_ID", usage)
        self.assertNotIn("AI_UI_URL", usage)

    def test_the_link_needs_an_env_file_that_carries_the_ai_ui_key_and_space(self):
        repo = self.checkout()
        result = self.run_cli("dev", "--dir", str(repo), "--link-ai-ui")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--link-ai-ui needs --env-file", result.stdout)
        for held, missing in (("AI_UI_API_KEY=k\n", "NOTIFY_SPACE_ID"), ("NOTIFY_SPACE_ID=s\n", "AI_UI_API_KEY"),
                              ("DEMO_STORY=1\n", "AI_UI_API_KEY"), ("# nothing\n", "AI_UI_API_KEY")):
            with self.subTest(held=held):
                path = self.dev_env(held)
                result = self.run_cli("dev", "--dir", str(repo), "--link-ai-ui", "--env-file", str(path))
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"{missing} is not there", result.stdout)
        self.assertEqual(self.docker_runs(), [])
        self.assertFalse([c for c in self.calls() if " network " in c], "refused after docker was touched")

    def test_the_summary_says_which_space_gets_the_notices_and_through_what(self):
        repo = self.checkout()
        (self.state / "container-sage-ai").touch()
        result = self.ok("dev", "--dir", str(repo), "--link-ai-ui", "--env-file", str(self.link_env()))
        self.assertIn("Notices go to Space space-9 through http://sage-ai:8080", result.stdout)
        self.assertNotIn("ai-key-value-77", result.stdout + result.stderr)

    def test_the_link_turns_back_the_real_trellis_key_and_space_without_printing_them(self):
        repo = self.checkout()
        self.ok("start")
        real = {"AI_UI_API_KEY": "real-key-value-31", "NOTIFY_SPACE_ID": "real-space-5"}
        own = {"AI_UI_API_KEY": "dev-key-value-8", "NOTIFY_SPACE_ID": "dev-space-2"}
        self.env_file.write_text(self.env_file.read_text() + "".join(f"{k}={v}\n" for k, v in real.items()))
        runs, before = len(self.docker_runs()), len(self.calls())
        for name in own:
            with self.subTest(name=name):
                path = self.dev_env("".join(f"{k}={v}\n" for k, v in (own | {name: real[name]}).items()))
                result = self.run_cli("dev", "--dir", str(repo), "--link-ai-ui", "--env-file", str(path))
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"{name} as the real Trellis", result.stdout)
                for value in (*real.values(), *own.values()):
                    self.assertNotIn(value, result.stdout + result.stderr)
        self.assertEqual(len(self.docker_runs()), runs)
        self.assertFalse([c for c in self.calls()[before:] if " network " in c], "refused after docker was touched")
        # Its own key and a test Space pass.
        self.ok("dev", "--dir", str(repo), "--link-ai-ui",
                "--env-file", str(self.dev_env("".join(f"{k}={v}\n" for k, v in own.items()))))
        self.assertEqual(len(self.docker_runs()), runs + 1)

    def test_the_dev_install_tells_the_app_its_own_address_and_the_file_cannot_change_it(self):
        repo = self.checkout()
        self.ok("start", "--url", "https://crm.example.com")
        self.ok("dev", "--dir", str(repo))
        self.ok("dev", "--dir", str(repo), "--port", "9100")
        _, first, second = self.docker_runs()
        self.assertIn("-e CRM_PUBLIC_URL=http://localhost:8031", first)
        self.assertIn("-e CRM_PUBLIC_URL=http://localhost:9100", second)
        path = self.dev_env("CRM_PUBLIC_URL=https://crm.example.com\n")
        result = self.run_cli("dev", "--dir", str(repo), "--env-file", str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("sets CRM_PUBLIC_URL", result.stdout)

    def test_reset_removes_only_the_dev_volume(self):
        repo = self.checkout()
        self.ok("dev", "--dir", str(repo), "--reset")
        removed = [c for c in self.calls() if " volume rm " in c]
        self.assertEqual(len(removed), 1)
        self.assertTrue(removed[0].endswith(" trellis-crm-dev-data"))

    def test_stop_removes_only_the_dev_container(self):
        repo = self.checkout()
        self.ok("start")
        self.ok("dev", "--dir", str(repo))
        self.ok("dev", "--stop")
        self.assertFalse((self.state / "container-trellis-crm-dev").exists())
        self.assertTrue((self.state / "up-trellis-crm").exists())

    def stick_volume(self, fails):
        """Docker holds the volume through that many removals; the pause between tries takes no time."""
        (self.state / "rm-fails").write_text(f"{fails}\n")
        self.stub("sleep", 'echo "sleep $*" >> "$STATE/calls"')

    def test_reset_tries_the_volume_up_to_five_times_and_claims_a_reset_only_once_it_is_gone(self):
        repo = self.checkout()
        self.ok("dev", "--dir", str(repo))  # makes the dev volume
        self.stick_volume(2)
        before = len(self.calls())
        result = self.ok("dev", "--dir", str(repo), "--reset")
        calls = self.calls()[before:]
        self.assertEqual(len([c for c in calls if " volume rm " in c]), 3)
        self.assertEqual(calls.count("sleep 1"), 2)
        self.assertIn("Dev data reset", result.stdout)
        self.assertIn("-v trellis-crm-dev-data:/data", self.docker_runs()[-1])
        # A volume that stays: five tries, then an error, with no reset claimed and no dev container over the old data.
        self.stick_volume(99)
        runs, before = len(self.docker_runs()), len(self.calls())
        result = self.run_cli("dev", "--dir", str(repo), "--reset")
        calls = self.calls()[before:]
        self.assertEqual(result.returncode, 1)
        self.assertIn("the dev volume is still there; nothing was reset", result.stdout)
        self.assertNotIn("Dev data reset", result.stdout)
        self.assertEqual(len([c for c in calls if " volume rm " in c]), 5)
        self.assertEqual(len(self.docker_runs()), runs)

    def test_a_checkout_that_also_serves_the_real_trellis_is_flagged_once_and_not_refused(self):
        repo, other = self.checkout(), self.checkout("trellis-two")
        link = self.home / "src" / "trellis-by-another-name"
        link.symlink_to(repo)
        warning = "This checkout also serves the real Trellis: an edit here changes both."
        self.ok("start", "--source", str(repo))
        for what, folder, flagged in (("the folder itself", repo, 1), ("a link to it", link, 1),
                                      ("another checkout", other, 0)):
            with self.subTest(what):
                result = self.ok("dev", "--dir", str(folder))
                self.assertEqual(result.stdout.count(warning), flagged)
                self.assertIn("Dev mode is running", result.stdout)
        self.ok("start", "--no-source")  # the real one runs the image's code: no checkout to share
        self.assertNotIn(warning, self.ok("dev", "--dir", str(repo)).stdout)


class SourceMode(Mac):
    """Server mode from a checkout: the checkout's code replaces the image's; everything else stays."""

    def checkout(self):
        repo = self.home / "src" / "trellis"
        (repo / ".git").mkdir(parents=True)
        (repo / "manage.py").write_text("")
        return repo

    def test_start_mounts_the_checkout_over_the_code_and_keeps_server_mode(self):
        repo = self.checkout()
        self.ok("start", "--source", str(repo))
        (run,) = self.docker_runs()
        for part in (f"-v {repo}:/app", "-v trellis-crm-data:/data", "--restart unless-stopped",
                     f"--env-file {self.env_file}", "ghcr.io/sage-is/trellis:latest"):
            self.assertIn(part, run)
        self.assertEqual(self.settings()["TRELLIS_SOURCE"], str(repo))
        self.ok("start")  # the next plain start keeps the checkout
        self.assertIn(f"-v {repo}:/app", self.docker_runs()[-1])
        self.ok("start", "--no-source")
        self.assertNotIn(":/app", self.docker_runs()[-1])

    def test_update_pulls_the_checkout_before_the_image(self):
        repo = self.checkout()
        self.ok("start", "--source", str(repo))
        self.ok("update")
        calls = self.calls()
        after_pull = calls[calls.index(f"git -C {repo} pull --ff-only"):]
        self.assertIn("docker colima pull ghcr.io/sage-is/trellis:latest", after_pull)  # the image follows the checkout
        self.assertIn(f"-v {repo}:/app", self.docker_runs()[-1])

    def test_a_folder_that_is_not_trellis_or_outside_home_is_refused(self):
        other = self.home / "notes"
        other.mkdir()
        result = self.run_cli("start", "--source", str(other))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a Trellis checkout", result.stdout)
        outside = self.home.parent / "elsewhere"
        (outside / ".git").mkdir(parents=True)
        (outside / "manage.py").write_text("")
        result = self.run_cli("start", "--source", str(outside))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside your home folder", result.stdout)
        self.assertEqual(self.docker_runs(), [])

    def test_a_vanished_checkout_stops_start_with_the_way_out(self):
        repo = self.checkout()
        self.ok("start", "--source", str(repo))
        shutil.rmtree(repo)
        result = self.run_cli("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--no-source", result.stdout)
        self.assertEqual(len(self.docker_runs()), 1)


class MailboxKey(Mac):
    """MAILBOX_SECRETS_KEY encrypts the mailbox passwords in Trellis's database: one Fernet key, made once."""

    def key_lines(self):
        return [line for line in self.env_file.read_text().splitlines() if line.startswith("MAILBOX_SECRETS_KEY=")]

    def assert_fernet(self, key):
        self.assertEqual(len(key), 44)
        self.assertEqual(len(base64.urlsafe_b64decode(key)), 32)
        self.assertNotRegex(key, r"[+/]")

    def test_a_new_settings_file_carries_a_key_and_says_what_losing_it_costs(self):
        result = self.ok("start")
        self.assert_fernet(self.settings()["MAILBOX_SECRETS_KEY"])
        self.assertEqual(len(self.key_lines()), 1)
        text = self.env_file.read_text()
        note = text[:text.index("MAILBOX_SECRETS_KEY=")].splitlines()[-2]
        self.assertIn("mailbox passwords", note)
        self.assertIn("entered again", note)
        self.assertNotIn("Added MAILBOX_SECRETS_KEY", result.stdout)
        self.assertNotIn(self.settings()["MAILBOX_SECRETS_KEY"], result.stdout + result.stderr)

    def test_an_old_settings_file_gets_one_key_once_and_keeps_every_other_line(self):
        self.ok("start")
        old = "".join(line + "\n" for line in self.env_file.read_text().splitlines() if "MAILBOX" not in line)
        self.env_file.write_text(old)
        result = self.ok("start")
        self.assertEqual([line for line in result.stdout.splitlines() if "MAILBOX_SECRETS_KEY" in line],
                         [f"Added MAILBOX_SECRETS_KEY to {self.env_file}: it encrypts mailbox passwords, so keep it with the file."])
        text = self.env_file.read_text()
        self.assertTrue(text.startswith(old), "every line before stays as it was")
        (line,) = self.key_lines()
        key = line.split("=", 1)[1]
        self.assert_fernet(key)
        self.assertNotIn(key, result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        again = self.ok("start")
        self.assertNotIn("MAILBOX_SECRETS_KEY", again.stdout)
        self.assertEqual(self.env_file.read_text(), text, "never a second key")

    def test_an_empty_key_is_filled_in_its_place(self):
        self.ok("start")
        text = self.env_file.read_text()
        key = self.settings()["MAILBOX_SECRETS_KEY"]
        self.env_file.write_text(text.replace(f"MAILBOX_SECRETS_KEY={key}\n", "MAILBOX_SECRETS_KEY=\n"))
        self.assertIn("Added MAILBOX_SECRETS_KEY", self.ok("start").stdout)
        (line,) = self.key_lines()
        self.assert_fernet(line.split("=", 1)[1])
        self.assertEqual(self.env_file.read_text().count("mailbox passwords"), 1, "the note stays one")


class Mailboxes(Mac):
    """trellis-crm mailbox: Trellis's own command in the container, arguments as typed, stdin through, the exit code back."""

    PREFIX = ["exec", "-i", "trellis-crm", "python", "manage.py", "mailbox"]

    def setUp(self):
        super().setUp()
        self.ok("start")

    def piped(self, *args, data):
        return subprocess.run([TRELLIS_CRM, *args], env=self.env, capture_output=True, text=True, input=data, timeout=30)

    def typed(self, *args, answer):
        """stdin a terminal that types `answer`, echo off: read -s waits for an undrained echo (tcsetattr TCSADRAIN)."""
        terminal, typist = pty.openpty()
        attrs = termios.tcgetattr(typist)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(typist, termios.TCSANOW, attrs)
        os.write(terminal, answer.encode())
        try:
            return subprocess.run([TRELLIS_CRM, *args], env=self.env, capture_output=True, text=True, stdin=typist, timeout=30)
        finally:
            os.close(terminal)
            os.close(typist)

    def container_argv(self):
        path = self.state / "exec-argv"
        return path.read_text().split("\0")[:-1] if path.exists() else None

    def container_stdin(self):
        return (self.state / "exec-stdin").read_text().strip()

    def assert_nowhere(self, secret, result):
        self.assertNotIn(secret, result.stdout + result.stderr)
        for call in self.calls():
            self.assertNotIn(secret, call)
        for path in (p for p in self.root.rglob("*") if p.is_file()):
            self.assertNotIn(secret.encode(), path.read_bytes(), path)

    def test_arguments_reach_the_container_as_typed(self):
        args = ["add", "Front desk", "--host", "imap.example.test", "--username", "o'neil \"desk\"@example.test",
                "--read-from", "2026-10-01", "--port", "993", "--mode", "personal", "--owner", "agent one"]
        self.ok("mailbox", *args)
        self.assertEqual(self.container_argv(), self.PREFIX + args)
        self.ok("mailbox", "list", "--runtime", "colima")
        self.assertEqual(self.container_argv(), self.PREFIX + ["list"])

    def test_the_password_goes_on_stdin_and_nowhere_else(self):
        secret = "pa ss 'word' \"x\" $HOME"
        result = self.piped("mailbox", "secret", "front desk", "--stdin", data=secret)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.container_argv(), self.PREFIX + ["secret", "front desk", "--stdin"])
        self.assertEqual(self.container_stdin(), hashlib.sha256(secret.encode()).hexdigest())
        self.assertIn("stored", result.stdout)
        self.assert_nowhere(secret, result)

    def test_at_a_terminal_the_password_is_asked_for_without_echo(self):
        result = self.typed("mailbox", "secret", "front", "--stdin", answer="typed pw\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.container_stdin(), hashlib.sha256(b"typed pw").hexdigest())
        self.assertIn("Mailbox password:", result.stderr)
        self.assert_nowhere("typed pw", result)

    def test_the_exit_code_comes_back(self):
        for code in (0, 1, 2):
            with self.subTest(code=code):
                (self.state / "mailbox-exit").write_text(str(code))
                self.assertEqual(self.run_cli("mailbox", "check", "front").returncode, code)
                self.assertEqual(self.piped("mailbox", "secret", "front", "--stdin", data="x").returncode, code)

    def test_it_needs_trellis_running(self):
        self.ok("stop")
        result = self.run_cli("mailbox", "list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Start it first", result.stderr)
        self.assertIsNone(self.container_argv())

    def test_usage_lists_it(self):
        usage = self.ok("--help").stdout
        self.assertIn("mailbox list | add NAME ... | check NAME | enable NAME | disable NAME", usage)
        self.assertIn("mailbox secret NAME (--stdin | --clear)", usage)


class Lib(unittest.TestCase):
    """trellis-crm sources the runtime code from lib/ beside itself: in a checkout, and in the formula's libexec."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def place(self, folder):
        """trellis-crm and its lib, as a checkout or libexec holds them."""
        (folder / "lib").mkdir(parents=True)
        shutil.copy2(TRELLIS_CRM, folder / "trellis-crm")
        shutil.copy2(LIB, folder / "lib" / "sage-runtime.sh")
        return folder / "trellis-crm"

    def run_tool(self, tool):
        return subprocess.run([tool, "version"], env={"HOME": str(self.root), "PATH": "/usr/bin:/bin"},
                              capture_output=True, text=True, timeout=30)

    def assert_runs(self, tool):
        result = self.run_tool(tool)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"^trellis-crm \d+\.\d+\.\d+ \(image ghcr\.io/sage-is/trellis:latest\)")

    def test_an_exec_script_in_bin_runs_the_tool_beside_its_lib(self):
        tool = self.place(self.root / "libexec")
        (self.root / "bin").mkdir()
        script = self.root / "bin" / "trellis-crm"  # what bin.write_exec_script writes
        script.write_text(f'#!/bin/bash\nexec "{tool}" "$@"\n')
        script.chmod(0o755)
        self.assert_runs(script)

    def test_a_link_to_the_tool_finds_its_lib(self):
        # A link may lead to another link, and a relative one leads from its own folder.
        self.place(self.root / "tap")
        links = self.root / "links"
        links.mkdir()
        (links / "relative").symlink_to(Path("..") / "tap" / "trellis-crm")
        (self.root / "bin").mkdir()
        (self.root / "bin" / "trellis-crm").symlink_to(links / "relative")
        self.assert_runs(self.root / "bin" / "trellis-crm")

    def test_without_its_lib_it_says_how_to_mend_it(self):
        shutil.copy2(TRELLIS_CRM, self.root / "trellis-crm")
        result = self.run_tool(self.root / "trellis-crm")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr,
                         f"Error: {self.root}/lib/sage-runtime.sh is missing. Reinstall the formula (brew reinstall "
                         "trellis-crm), or run trellis-crm from a checkout of the tap, which has lib/.\n")

    def test_the_tool_keeps_no_copy_of_the_shared_code(self):
        # One copy for sage-runtime, trellis-crm and ai-ui: a function or setting here would shadow the lib's.
        defined = re.compile(r"(?m)^([A-Za-z_]+)(?:\(\) \{$|=)")
        self.assertEqual(set(defined.findall(TRELLIS_CRM.read_text())) & set(defined.findall(LIB.read_text())), set())


class Formula(unittest.TestCase):
    """The tool goes beside its lib; krunkit's own formula refuses Intel, so only Apple Silicon may depend on it."""

    def test_the_formula_installs_the_tool_beside_its_lib(self):
        text = FORMULA.read_text()
        for line in ('libexec.install "trellis-crm"', '(libexec/"lib").install "lib/sage-runtime.sh"',
                     'bin.write_exec_script libexec/"trellis-crm"'):
            self.assertIn(f"    {line}\n", text)
        self.assertNotIn('bin.install "trellis-crm"', text)
        # The version pin lands in the script before it moves.
        self.assertLess(text.index('inreplace "trellis-crm"'), text.index('libexec.install "trellis-crm"'))

    def test_only_a_release_pins_its_image_and_a_head_install_runs_latest(self):
        # A --HEAD build's version is HEAD-<commit>, which tags no image in the registry.
        text = FORMULA.read_text()
        self.assertEqual(TRELLIS_CRM.read_text().count('\nDEFAULT_TAG="latest"\n'), 1)
        self.assertIn("""    inreplace "trellis-crm", 'DEFAULT_TAG="latest"', "DEFAULT_TAG=\\"#{version}\\"" """
                      "unless build.head?\n", text)
        self.assertIn('    tag = build.head? ? "latest" : version\n', text[text.index("  test do\n"):])

    def test_krunkit_is_an_apple_silicon_dependency_and_the_caveats_say_how_to_get_it(self):
        text = FORMULA.read_text()
        self.assertIn('\n  on_arm do\n    depends_on "libkrun/krun/krunkit"\n  end\n', text)
        self.assertEqual(text.count('depends_on "libkrun/krun/krunkit"'), 1)
        caveats = " ".join(text[text.index("def caveats"):].split())
        self.assertIn(KRUNKIT_INSTALL, caveats)
        self.assertNotIn("--formula", caveats)
        # Lima applies the override to the VMs in its own home only: Colima's, not ~/.lima's.
        self.assertIn("Every Colima VM", caveats)
        self.assertIn("a boot step that mounts the VM's data disk", caveats)


if __name__ == "__main__":
    unittest.main()
