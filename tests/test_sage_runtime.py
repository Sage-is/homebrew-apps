"""sage-runtime: switching docker between Colima, Docker Desktop and OrbStack, and copying data across."""

import json
import os
import pty
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "sage-runtime"

# Stand-ins log each call. Each docker context has a running marker and a
# folder per volume under $STATE/vol-<context>/.
STUBS = {
    "docker": r"""ctx="${DOCKER_CONTEXT:-$(cat "$STATE/current-context" 2>/dev/null || echo default)}"
if [[ "${1:-}" == --context ]]; then ctx="$2"; shift 2; fi
echo "docker $ctx $*" >> "$STATE/calls"
vols="$STATE/vol-$ctx"
case "$1 ${2:-}" in
  "info "*)        [[ -e "$STATE/running-$ctx" ]] ;;
  "context show")  cat "$STATE/current-context" 2>/dev/null || echo default ;;
  "context use")   echo "$3" > "$STATE/current-context" ;;
  "context ls")    echo default; echo colima; echo desktop-linux ;;
  "volume ls")     ls "$vols" 2>/dev/null ;;
  "volume inspect") [[ -d "$vols/$3" ]] ;;
  "buildx version") [[ ! -e "$STATE/no-buildx" ]] ;;
  "ps --format")     ls "$STATE/run-$ctx" 2>/dev/null ;;
  "inspect -f")      cat "$STATE/run-$ctx/${@: -1}" 2>/dev/null ;;
  "save "*)        printf 'IMAGE %s' "$2" ;;
  "load "*)        cat > "$STATE/loaded-$ctx" ;;
  "run "*)
    vol=""; prev=""
    for a in "$@"; do [[ "$prev" == -v ]] && vol="${a%%:*}"; prev="$a"; done
    dir="$vols/$vol"; mkdir -p "$dir"
    last="${*: -1}"
    if [[ "$*" == *" -cf "* ]]; then tar -C "$dir" -cf - .
    elif [[ "$*" == *" -xf "* ]]; then tar -C "$dir" -xf -
    elif [[ "$*" == *" ls -A "* ]]; then ls -A "$dir"
    elif [[ "$*" == *" find /v -mindepth 1 -delete"* ]]; then find "$dir" -mindepth 1 -delete
    elif [[ "$*" == *" sh -c "* ]]; then sh -c "${last//\/v/$dir}"
    fi ;;
esac""",
    "colima": r"""echo "colima $*" >> "$STATE/calls"
case "$1" in
  list)  if [[ -e "$STATE/colima-vm" ]]; then echo '{"name":"default","status":"Stopped"}'; fi ;;
  start) touch "$STATE/colima-vm" "$STATE/running-colima" ;;
  stop)  rm -f "$STATE/running-colima" ;;
esac""",
    "open": r"""echo "open $*" >> "$STATE/calls"
case "${@: -1}" in
  Docker)   touch "$STATE/running-desktop-linux" ;;
  OrbStack) touch "$STATE/running-orbstack" ;;
esac""",
    "osascript": r"""echo "osascript $*" >> "$STATE/calls"
case "$*" in
  *'"Docker"'*)   rm -f "$STATE/running-desktop-linux" ;;
  *'"OrbStack"'*) rm -f "$STATE/running-orbstack" ;;
esac""",
    "sysctl": r"""case "$2" in
  hw.memsize) echo $(( ${MAC_RAM_GIB:-32} * 1073741824 )) ;;
  hw.ncpu)    echo "${MAC_CORES:-10}" ;;
esac""",
    "docker-credential-osxkeychain": "exit 0",
}


@unittest.skipUnless(sys.platform == "darwin", "sage-runtime is for macOS")
class Mac(unittest.TestCase):
    """A fake Mac: its own home, apps folder, Homebrew prefix and stand-in commands."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.home, self.apps, self.state, self.bin = root / "home", root / "Applications", root / "state", root / "bin"
        self.brew = root / "brew"
        for folder in (self.home, self.apps, self.state, self.bin):
            folder.mkdir()
        for name, body in STUBS.items():
            stub = self.bin / name
            stub.write_text("#!/bin/bash\n" + body + "\n")
            stub.chmod(0o755)
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin",
                    "STATE": str(self.state), "APPLICATIONS_DIR": str(self.apps),
                    "HOMEBREW_PREFIX": str(self.brew)}
        self.config = self.home / ".docker" / "config.json"

    def install(self, app):
        (self.apps / f"{app}.app").mkdir()

    def running(self, context):
        (self.state / f"running-{context}").touch()

    def is_running(self, context):
        return (self.state / f"running-{context}").exists()

    def volume(self, context, name, files):
        folder = self.state / f"vol-{context}" / name
        folder.mkdir(parents=True)
        for rel, text in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(text)
        return folder

    def container(self, context, name, *mounts):
        folder = self.state / f"run-{context}"
        folder.mkdir(exist_ok=True)
        (folder / name).write_text(" ".join(str(m) for m in mounts) + "\n")

    def docker_config(self, data):
        self.config.parent.mkdir(exist_ok=True)
        self.config.write_text(json.dumps(data))

    def run_tool(self, *args, **env):
        return subprocess.run([TOOL, *args], env=self.env | env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)

    def ok(self, *args, **env):
        result = self.run_tool(*args, **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def saved(self):
        path = self.home / ".sage-is" / "runtime"
        return path.read_text().strip() if path.exists() else None


class Use(Mac):
    def setUp(self):
        super().setUp()
        self.install("Docker")
        self.running("desktop-linux")

    def test_a_new_colima_vm_gets_the_development_profile(self):
        self.ok("use", "colima")
        self.assertIn("colima start --vm-type vz --vz-rosetta --mount-type virtiofs --mount-inotify "
                      "--memory 8 --cpu 5 --disk 100", self.calls())

    def test_a_small_mac_gets_the_floor_and_overrides_win(self):
        self.ok("use", "colima", MAC_RAM_GIB="16", MAC_CORES="4")
        self.assertIn("--memory 5 --cpu 2", " ".join(self.calls()))
        for marker in ("colima-vm", "running-colima"):  # the VM deleted, so the next use creates one
            (self.state / marker).unlink()
        self.ok("use", "colima", SAGE_RUNTIME_MEMORY="6", SAGE_RUNTIME_DISK="60")
        starts = [call for call in self.calls() if call.startswith("colima start")]
        self.assertIn("--memory 6 --cpu 5 --disk 60", starts[-1])

    def test_an_existing_vm_keeps_its_own_settings(self):
        (self.state / "colima-vm").touch()
        self.ok("use", "colima")
        self.assertIn("colima start", self.calls())
        self.assertNotIn("--vm-type", " ".join(self.calls()))

    def test_use_points_docker_at_it_remembers_it_and_stops_the_other(self):
        result = self.ok("use", "colima")
        self.assertEqual((self.state / "current-context").read_text().strip(), "colima")
        self.assertEqual(self.saved(), "colima")
        self.assertFalse(self.is_running("desktop-linux"))
        self.assertIn("ai-ui and trellis-crm follow", result.stdout)

    def test_keep_other_leaves_the_other_running(self):
        self.ok("use", "colima", "--keep-other")
        self.assertTrue(self.is_running("desktop-linux"))
        self.assertTrue(self.is_running("colima"))

    def test_back_to_docker_desktop_stops_colima(self):
        self.ok("use", "colima")
        settings = self.home / "Library/Group Containers/group.com.docker/settings-store.json"
        settings.parent.mkdir(parents=True)
        settings.touch()
        self.ok("use", "docker-desktop")
        self.assertIn("open -g -j -a Docker", self.calls())
        self.assertIn("colima stop", self.calls())
        self.assertEqual(self.saved(), "docker-desktop")
        self.assertEqual((self.state / "current-context").read_text().strip(), "desktop-linux")

    def test_an_unknown_or_missing_runtime_is_refused(self):
        result = self.run_tool("use", "podman")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Use one of: colima docker-desktop orbstack", result.stderr)
        result = self.run_tool("use", "orbstack")
        self.assertEqual(result.returncode, 1)
        self.assertIn("brew install --cask orbstack", result.stderr)
        self.assertIsNone(self.saved())


class SharedData(Mac):
    """Two VMs never share file locks, so ~/SageData has one runtime at a time."""

    def setUp(self):
        super().setUp()
        self.install("Docker")
        self.running("desktop-linux")
        (self.state / "colima-vm").touch()
        self.shared = self.home / "SageData" / "sprig-registry"

    def test_switching_stops_the_other_runtime_before_starting_this_one(self):
        self.ok("use", "colima")
        calls = self.calls()
        quit_desktop = next(i for i, c in enumerate(calls) if c.startswith("osascript"))
        start_colima = next(i for i, c in enumerate(calls) if c.startswith("colima start"))
        self.assertLess(quit_desktop, start_colima)

    def test_keep_other_is_refused_while_both_sides_use_the_shared_folder(self):
        self.running("colima")
        self.container("colima", "local-registry", self.shared)
        self.container("desktop-linux", "old-registry", self.shared)
        result = self.run_tool("use", "colima", "--keep-other")
        self.assertEqual(result.returncode, 1)
        self.assertIn("local-registry", result.stderr)
        self.assertIn("old-registry", result.stderr)
        self.assertIn("lose data", result.stderr)

    def test_keep_other_is_fine_when_only_one_side_uses_it(self):
        self.running("colima")
        self.container("colima", "local-registry", self.shared)
        self.container("desktop-linux", "web", "/var/lib/docker/volumes/x/_data")
        self.ok("use", "colima", "--keep-other")

    def test_a_copy_is_refused_while_both_sides_use_the_shared_folder(self):
        self.running("colima")
        (self.home / ".sage-is").mkdir()
        (self.home / ".sage-is" / "runtime").write_text("colima\n")
        self.container("colima", "a", self.shared)
        self.container("desktop-linux", "b", self.shared / "x")
        self.volume("desktop-linux", "trellis-data", {"f": "1"})
        result = self.run_tool("copy-volume", "trellis-data")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Stop one side first", result.stderr)


class DockerConfig(Mac):
    def test_a_credential_helper_that_left_with_docker_desktop_becomes_the_keychain(self):
        self.docker_config({"auths": {"ghcr.io": {}}, "credsStore": "desktop"})
        self.ok("use", "colima")
        data = json.loads(self.config.read_text())
        self.assertEqual(data["credsStore"], "osxkeychain")
        self.assertEqual(data["auths"], {"ghcr.io": {}})
        backups = list(self.config.parent.glob("config.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertIn('"desktop"', backups[0].read_text())

    def test_a_helper_that_exists_is_left_alone(self):
        self.docker_config({"credsStore": "osxkeychain"})
        self.ok("use", "colima")
        self.assertEqual(json.loads(self.config.read_text())["credsStore"], "osxkeychain")
        self.assertFalse(list(self.config.parent.glob("*.bak")))

    def test_homebrews_plugin_folder_is_added_once(self):
        (self.brew / "lib/docker/cli-plugins").mkdir(parents=True)
        self.docker_config({"cliPluginsExtraDirs": ["/elsewhere"]})
        self.ok("use", "colima")
        self.ok("use", "colima")
        dirs = json.loads(self.config.read_text())["cliPluginsExtraDirs"]
        self.assertEqual(dirs, [str(self.brew / "lib/docker/cli-plugins"), "/elsewhere"])

    def test_no_config_and_no_homebrew_plugins_writes_nothing(self):
        self.ok("use", "colima")
        self.assertFalse(self.config.exists())


class Status(Mac):
    def test_it_lists_runtimes_and_the_problems_that_will_bite(self):
        self.install("Docker")
        self.running("desktop-linux")
        self.volume("desktop-linux", "trellis-data", {"a": "1"})
        (self.state / "current-context").write_text("desktop-linux\n")
        (self.home / ".sage-is").mkdir()
        (self.home / ".sage-is" / "runtime").write_text("colima\n")
        self.docker_config({"credsStore": "desktop"})
        builders = self.home / ".docker" / "buildx" / "instances"
        builders.mkdir(parents=True)
        (builders / "old-builder").write_text(json.dumps({"Name": "old-builder", "Nodes": [{"Endpoint": "gone-context"}]}))
        (builders / "fine").write_text(json.dumps({"Name": "fine", "Nodes": [{"Endpoint": "colima"}]}))
        (self.state / "no-buildx").touch()
        out = self.ok("status").stdout
        self.assertIn("docker-desktop  running, 1 volumes", out)
        self.assertIn("colima          stopped  <- in use", out)
        self.assertIn("orbstack        not installed", out)
        self.assertIn("Not the runtime in use. Run: sage-runtime use colima", out)
        self.assertIn('credsStore "desktop" has no helper', out)
        self.assertIn("brew install docker-buildx", out)
        self.assertIn("docker buildx rm old-builder", out)
        self.assertNotIn("buildx rm fine", out)

    def test_a_clean_mac_says_so(self):
        self.running("colima")
        (self.state / "current-context").write_text("colima\n")
        self.assertIn("No problems found.", self.ok("status").stdout)


class Copy(Mac):
    def setUp(self):
        super().setUp()
        self.install("Docker")
        (self.state / "colima-vm").touch()
        self.ok("use", "colima")

    def test_a_volume_comes_across_whole(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db", "artifacts/ab/cd": "blob"})
        result = self.ok("copy-volume", "trellis-data")
        copied = self.state / "vol-colima" / "trellis-data"
        self.assertEqual((copied / "trellis.sqlite3").read_text(), "db")
        self.assertEqual((copied / "artifacts/ab/cd").read_text(), "blob")
        self.assertIn("from docker-desktop to colima", result.stdout)
        self.assertIn("Copied 5 files and folders", result.stdout)

    def test_a_target_with_data_needs_force(self):
        self.volume("desktop-linux", "trellis-data", {"new": "1"})
        self.volume("colima", "trellis-data", {"old": "1"})
        result = self.run_tool("copy-volume", "trellis-data")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Add --force", result.stderr)
        self.assertFalse((self.state / "vol-colima" / "trellis-data" / "new").exists())
        self.ok("copy-volume", "trellis-data", "--force")
        self.assertTrue((self.state / "vol-colima" / "trellis-data" / "new").exists())

    def test_back_the_other_way_with_from_and_to(self):
        self.volume("colima", "sage-ai-dev-data", {"webui.db": "x"})
        self.ok("copy-volume", "sage-ai-dev-data", "--from", "colima", "--to", "docker-desktop")
        self.assertTrue((self.state / "vol-desktop-linux" / "sage-ai-dev-data" / "webui.db").exists())

    def test_a_missing_volume_is_named(self):
        result = self.run_tool("copy-volume", "nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("docker-desktop has no volume nope", result.stderr)

    def test_an_image_comes_across(self):
        self.ok("copy-image", "youtube-transcribe-server:latest")
        self.assertEqual((self.state / "loaded-colima").read_text(), "IMAGE youtube-transcribe-server:latest")


class Basics(Mac):
    def test_version_and_help(self):
        self.assertRegex(self.ok("version").stdout.strip(), r"^sage-runtime \d+\.\d+\.\d+$")
        self.assertIn("copy-volume NAME", self.ok("--help").stdout)

    def test_no_docker_cli_says_how_to_get_it(self):
        (self.bin / "docker").unlink()
        result = self.run_tool("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("brew install docker", result.stderr)


if __name__ == "__main__":
    unittest.main()
