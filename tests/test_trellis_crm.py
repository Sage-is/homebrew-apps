"""trellis-crm in server mode: the settings file, the docker run, backups, boot and nuke."""

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
# runtime answering, the image, the network, the volume, the container.
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
        root = Path(scratch.name)
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
        for part in ("--name trellis-crm", "--restart unless-stopped", "--network sage-net", HOST_ENTRY,
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
        self.assertTrue([call for call in self.calls() if call.startswith("colima start --profile default --vm-type ")])
        self.assertEqual((self.home / ".sage-is" / "runtime").read_text().strip(), "colima")


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

    def test_stop_removes_only_the_dev_container(self):
        repo = self.checkout()
        self.ok("start")
        self.ok("dev", "--dir", str(repo))
        self.ok("dev", "--stop")
        self.assertFalse((self.state / "container-trellis-crm-dev").exists())
        self.assertTrue((self.state / "up-trellis-crm").exists())


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
        self.assertTrue(result.stdout.startswith("trellis-crm latest "), result.stdout)

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
