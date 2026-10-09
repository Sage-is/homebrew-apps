"""lib/sage-runtime.sh: the runtime code sage-runtime and trellis-crm source from lib/, and AI-UI vendors for ai-ui."""

import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import test_sage_runtime as suite

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib" / "sage-runtime.sh"
TOOL = ROOT / "sage-runtime"
FORMULA = ROOT / "Formula" / "sage-runtime.rb"
# What the shared file holds, by the plan (docker-desktop-optional.md, step 1); the commands stay in each tool.
SHARED = ("briefly", "runtime_running", "runtime_up", "start_engine", "restore_context", "fix_credentials",
          "fix_plugin_dirs", "data_disk_step", "append_override", "fix_lima_override", "krunkit_vm", "krunkit_ready",
          "plan_colima", "start_new_colima", "check_data_disk", "check_space", "refuse_in_use", "copy_volume",
          "record_copy", "copy_image", "hint_convert", "convert_colima", "cleanup", "trap_cleanup",
          "keychain_serves", "brew_prefix", "brew_plugins", "dead_plugins", "desktop_leftovers", "desktop_login_item",
          "docker_data_disk", "colima_mounts")


class Lib(unittest.TestCase):
    """A tool sources the file under bash 3.2 with set -euo pipefail, as macOS ships it."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        for folder in (self.home, self.bin):
            folder.mkdir()

    def source(self, script, **env):
        """Source the lib, then run SCRIPT, with nothing but the stand-ins in self.bin ahead of the system."""
        return subprocess.run(["/bin/bash", "-c", f'set -euo pipefail\nsource "{LIB}"\n{script}'],
                              env={"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", **env},
                              capture_output=True, text=True, timeout=30)

    def test_sourcing_defines_the_shared_code_and_does_nothing_else(self):
        result = self.source(":")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(self.source("trap -p").stdout, "")  # a trap here would replace the tool's own
        self.assertEqual(list(self.home.iterdir()), [])
        defined = self.source("declare -F | awk '{print $3}'").stdout.split()
        for name in SHARED:
            self.assertIn(name, defined)
        self.assertEqual([name for name in defined if name.startswith("cmd_") or name == "usage"], [])

    def test_on_a_mac_it_unsets_what_would_turn_docker_or_colima_elsewhere(self):
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "DOCKER_CONTEXT": "colima-build",
               "DOCKER_HOST": "unix:///tmp/x.sock", "COLIMA_PROFILE": "build"}
        show = 'echo "${DOCKER_CONTEXT-unset} ${DOCKER_HOST-unset} ${COLIMA_PROFILE-unset}"'
        # On Linux, ai-ui and trellis-crm use the engine docker's own settings name, such as a rootless one.
        for ostype, seen in (("darwin25.0", "unset unset unset\n"),
                             ("linux-gnu", "colima-build unix:///tmp/x.sock build\n")):
            with self.subTest(ostype):
                result = subprocess.run(["/bin/bash", "-c", f'set -euo pipefail\nOSTYPE={ostype}\nsource "{LIB}"\n{show}'],
                                        env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.stdout, seen)

    def test_its_messages_send_people_to_commands_that_exist(self):
        # trellis-crm and ai-ui have no use or convert: messages name sage-runtime's, or the tool's own.
        for name, body in (("uname", 'echo arm64'), ("krunkit", "exit 0"), ("colima", "exit 0")):
            (self.bin / name).write_text(f"#!/bin/bash\n{body}\n")
            (self.bin / name).chmod(0o755)
        result = self.source('PROG=trellis-crm\nconvert_colima 0')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr,
                         "Error: there is no Colima VM to convert. Make a krunkit one: sage-runtime use colima\n")
        result = self.source('PROG=ai-ui\nUSE_COMMAND="ai-ui start --runtime"\nconvert_colima 0')
        self.assertEqual(result.stderr,
                         "Error: there is no Colima VM to convert. Make a krunkit one: ai-ui start --runtime colima\n")

    def test_a_run_cut_short_after_the_delete_prints_the_way_back(self):
        for script, status in (('ROLLBACK="the way back"\ndie "stopped"', 1),
                               ('ROLLBACK="the way back"\necho "Error: stopped" >&2\nkill -TERM $$', 143)):
            with self.subTest(status=status):
                result = self.source("trap_cleanup\n" + script)
                self.assertEqual(result.returncode, status)
                self.assertEqual(result.stderr, "Error: stopped\nthe way back\n")


class Consumer(suite.Mac):
    """Another tool sources the lib and calls its functions itself, on the fake Mac sage-runtime's suite builds."""

    def setUp(self):
        super().setUp()
        self.stage = self.home / ".sage-is" / "staging"

    def source(self, script, **env):
        script = f'set -euo pipefail\nPROG=trellis-crm\nsource "{LIB}"\ntrap_cleanup\n{script}'
        return subprocess.run(["/bin/bash", "-c", script], env=self.env | env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)

    def call(self, script, **env):
        result = self.source(script, **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def two_runtimes(self):
        self.install("Docker")
        self.running("desktop-linux")
        self.running("colima")
        self.volume("desktop-linux", "sage-ai-data", {"webui.db": "db", "uploads/a.txt": "a"})

    def test_detection_prefers_the_saved_runtime_to_dockers_context(self):
        self.running("colima")
        (self.state / "current-context").write_text("desktop-linux\n")
        out = self.call('current_runtime\nmkdir -p "$SAGE_CONFIG_DIR"\necho orbstack > "$RUNTIME_FILE"\n'
                        'current_runtime\nruntime_installed docker-desktop || echo "docker-desktop is not installed"\n'
                        'runtime_running colima && echo "colima answers"').stdout
        self.assertEqual(out, "docker-desktop\norbstack\ndocker-desktop is not installed\ncolima answers\n")

    def test_colima_says_whether_the_build_vm_is_up(self):
        # The $SAGE_DATA guard lists the build VM's containers, and a refusal stops it, only while it runs.
        show = 'for rt in colima colima-build; do runtime_up "$rt" && echo "$rt up" || echo "$rt down"; done'
        self.running("colima")
        (self.state / "vm-build").touch()
        self.assertEqual(self.call(show).stdout, "colima up\ncolima-build down\n")
        self.running("colima-build")
        self.assertEqual(self.call(show).stdout, "colima up\ncolima-build up\n")

    def test_each_runtime_names_its_context_and_an_unknown_one_is_refused(self):
        out = self.call('for rt in $RUNTIMES; do check_runtime "$rt"; echo "$rt $(runtime_context "$rt")"; done').stdout
        self.assertEqual(out, "colima colima\ndocker-desktop desktop-linux\norbstack orbstack\n")
        result = self.source("check_runtime podman")
        self.assertEqual((result.returncode, result.stderr),
                         (1, "Error: unknown runtime 'podman'. Use one of: colima docker-desktop orbstack\n"))

    def test_briefly_gives_up_on_a_question_that_never_answers(self):
        began = time.monotonic()
        out = self.call('ANSWER_SECONDS=1\nbriefly true\n'
                        'if briefly sleep 30; then echo answered; else echo "gave up: $?"; fi').stdout
        self.assertEqual(out, "gave up: 143\n")
        self.assertLess(time.monotonic() - began, 10)

    def test_briefly_stops_the_children_that_hold_the_answer_open_too(self):
        # colima ssh runs limactl shell, which runs ssh; each holds the output a $(...) waits on.
        self.stub("colima", 'sh -c "sleep 37; :" &\nwait')
        began = time.monotonic()
        self.assertEqual(self.call('ANSWER_SECONDS=1\necho "[$(docker_disk)]"').stdout, "[]\n")
        self.assertLess(time.monotonic() - began, 10)
        deadline = time.monotonic() + 5  # none of them lives on
        while subprocess.run(["pgrep", "-f", "sleep 37"], capture_output=True).returncode == 0:
            self.assertLess(time.monotonic(), deadline, "a child of the command outlived it")
            time.sleep(0.1)

    def test_start_runtime_makes_a_krunkit_vm_on_its_data_disk_and_leaves_docker_where_it_was(self):
        self.stub("krunkit", "exit 0")
        (self.state / "current-context").write_text("desktop-linux\n")
        self.call("start_runtime colima")
        self.assertEqual(self.started(), ["colima start --profile default --vm-type krunkit --mount-type virtiofs "
                                          "--mount-inotify --memory 12 --cpu 5 --disk 100"])
        self.assertEqual(self.override.read_text(), suite.VIRTIOFS + suite.DATA_DISK)
        self.assertEqual(self.context(), "desktop-linux")

    def test_start_engine_starts_a_source_and_leaves_the_data_disk_check_to_start_runtime(self):
        # ai-ui starts the runtime it moves from with start_engine; docker stays on the one in use.
        self.install("Docker")
        settings = self.home / "Library" / "Group Containers" / "group.com.docker" / "settings-store.json"
        settings.parent.mkdir(parents=True)
        settings.touch()
        self.running("colima")
        (self.state / "current-context").write_text("colima\n")
        self.assertEqual(self.call("start_engine docker-desktop").stdout, "Starting docker-desktop...\n")
        self.assertEqual((self.started(), self.context()), (["open -g -j -a Docker"], "colima"))
        # A krunkit VM that comes up without its data disk passes start_engine, never start_runtime.
        (self.state / "running-colima").unlink()
        lima = self.home / ".colima" / "_lima" / "colima"
        lima.mkdir(parents=True)
        (lima / "lima.yaml").write_text("vmType: krunkit\n")
        (self.state / "fail-mount").touch()
        result = self.source("start_engine colima\necho engine up\nstart_runtime colima")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout.startswith("Starting colima...\nengine up\n"), result.stdout)
        self.assertIn("Error: Docker in the colima VM runs on the VM's root disk", result.stderr)

    def test_check_data_disk_stops_while_docker_runs_on_the_root_disk(self):
        self.running("colima")
        (self.state / "root-disk-colima").touch()
        result = self.source("check_data_disk")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: Docker in the colima VM runs on the VM's root disk, not its data disk", result.stderr)

    def test_the_vm_type_comes_from_limas_file_and_krunkit_needs_apple_silicon(self):
        show = 'echo "[$(vm_type)]"\nkrunkit_vm && echo "krunkit VM"\nkrunkit_ready && echo "krunkit ready"\n:'
        self.assertEqual(self.call(show).stdout, "[]\n")
        lima = self.home / ".colima" / "_lima" / "colima"
        lima.mkdir(parents=True)
        (lima / "lima.yaml").write_text("vmType: vz\n")
        self.stub("krunkit", "exit 0")
        self.assertEqual(self.call(show).stdout, "[vz]\nkrunkit ready\n")
        (lima / "lima.yaml").write_text("vmType: krunkit\n")
        self.assertEqual(self.call(show, MAC_ARCH="x86_64").stdout, "[krunkit]\nkrunkit VM\n")

    def test_fix_lima_override_lands_each_mend_once(self):
        # trellis-crm calls it before it starts a krunkit VM that may be Colima's already.
        result = self.call("fix_lima_override\nfix_lima_override\noverride_ready && echo ready")
        self.assertEqual(self.override.read_text(), suite.VIRTIOFS + suite.DATA_DISK)
        self.assertEqual(([line.split(" ")[0] for line in result.stdout.splitlines()], result.stderr),
                         (["Added", "Added", "ready"], ""))

    def test_a_probe_that_fails_stops_rather_than_reading_as_no_volume(self):
        # ai-ui reads each volume with these before it copies; "missing" or "empty" once cost the data.
        self.two_runtimes()
        out = self.call("volume_exists docker-desktop sage-ai-data && echo found\n"
                        "volume_exists colima sage-ai-data || echo missing\n"
                        'measure_volume docker-desktop sage-ai-data\necho "$ENTRIES"').stdout
        self.assertEqual(out, "found\nmissing\n4\n")
        for marker, script, error in (
                ("fail-volume-ls-colima", "volume_exists colima sage-ai-data", "could not list the volumes in colima"),
                ("fail-probe-desktop-linux", "measure_volume docker-desktop sage-ai-data",
                 "could not look inside volume sage-ai-data in docker-desktop")):
            with self.subTest(marker):
                (self.state / marker).touch()
                result = self.source(script + " || echo 'taken for none'")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", f"Error: {error}\n"))

    def test_docker_config_gets_the_keychain_and_homebrews_plugins(self):
        (self.brew / "lib/docker/cli-plugins").mkdir(parents=True)
        self.docker_config({"credsStore": "desktop"})
        self.call("fix_credentials\nfix_plugin_dirs")
        self.assertEqual(json.loads(self.config.read_text()),
                         {"credsStore": "osxkeychain", "cliPluginsExtraDirs": [str(self.brew / "lib/docker/cli-plugins")]})

    def test_fix_credentials_without_a_runtime_takes_the_one_in_use(self):
        # ai-ui and trellis-crm call it bare; Docker Desktop stays installed, with its helper.
        self.install("Docker")
        self.stub("docker-credential-desktop", "exit 0")
        (self.home / ".sage-is").mkdir()
        for runtime, store in (("docker-desktop", "desktop"), ("orbstack", "desktop"), ("colima", "osxkeychain")):
            with self.subTest(runtime):
                (self.home / ".sage-is" / "runtime").write_text(runtime + "\n")
                self.docker_config({"credsStore": "desktop"})
                self.call("fix_credentials")
                self.assertEqual(json.loads(self.config.read_text())["credsStore"], store)

    def test_desktop_leftovers_name_a_command_that_mends_them(self):
        plugins = self.home / ".docker" / "cli-plugins"
        plugins.mkdir(parents=True)
        (plugins / "docker-ai").symlink_to(self.apps / "Docker.app" / "Contents" / "Resources" / "cli-plugins" / "docker-ai")
        line = (f"{plugins} holds links into Docker.app that lead nowhere: docker-ai. docker lists each as a broken plugin. "
                "Remove them: ")
        self.assertEqual(self.call("desktop_leftovers colima").stdout, line + "sage-runtime use colima\n")
        self.assertEqual(self.call('USE_COMMAND="ai-ui start --runtime"\ndesktop_leftovers colima').stdout,
                         line + "ai-ui start --runtime colima\n")

    def test_refuse_in_use_names_each_busy_volume_and_its_runtime(self):
        self.two_runtimes()
        self.container("desktop-linux", "sage-ai", "sage-ai-data")
        result = self.source('refuse_in_use docker-desktop "sage-ai-data sage-try-data" colima sage-ai-data')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "Error: running containers use these volumes, so nothing was copied:\n"
                                        "  sage-ai-data: sage-ai (docker-desktop)\nStop them, then run this again.\n")

    def test_copy_volume_passes_through_a_staged_file_checks_the_count_and_records_it(self):
        self.two_runtimes()
        out = self.call("copy_volume sage-ai-data docker-desktop colima 0\n"
                        "recorded_copy sage-ai-data docker-desktop colima").stdout
        self.assertEqual((self.state / "vol-colima" / "sage-ai-data" / "uploads/a.txt").read_text(), "a")
        self.assertRegex(out, r"Copied 4 files and folders \(\d+ KiB; (\d+) KiB in docker-desktop\)\.\n4 \1\n$")
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_a_copy_cut_short_takes_its_staged_file_and_half_made_volume_with_it(self):
        self.two_runtimes()
        (self.state / "fail-xf").touch()
        result = self.source("copy_volume sage-ai-data docker-desktop colima 0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not write volume sage-ai-data in colima", result.stderr)
        self.assertFalse((self.state / "vol-colima" / "sage-ai-data").exists())
        self.assertEqual(list(self.stage.iterdir()), [])
        self.assertFalse((self.home / ".sage-is" / "migrated").exists())

    def test_copy_volume_never_copies_a_volume_onto_itself(self):
        # With --force and a write that fails, it would empty the only copy.
        self.running("colima")
        self.volume("colima", "sage-ai-data", {"webui.db": "db"})
        (self.state / "fail-xf").touch()
        result = self.source("copy_volume sage-ai-data colima colima 1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "Error: volume sage-ai-data would be copied from colima onto itself\n")
        self.assertEqual((self.state / "vol-colima" / "sage-ai-data" / "webui.db").read_text(), "db")

    def test_copy_image_saves_to_a_staged_file_and_loads_it_inside_the_vm(self):
        self.two_runtimes()
        (self.state / "images-desktop-linux").write_text("ghcr.io/sage-is/ai-ui:3.2.0\t1MB\n")
        self.call("copy_image desktop-linux colima ghcr.io/sage-is/ai-ui:3.2.0")
        self.assertEqual((self.state / "loaded-colima").read_text(), "IMAGE ghcr.io/sage-is/ai-ui:3.2.0")
        staged = self.stage / "image-ghcr.io_sage-is_ai-ui_3.2.0.tar"
        self.assertIn(f"colima ssh --profile default -- docker load -i {staged}", self.calls())
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_the_vz_hint_names_a_command_that_converts(self):
        self.stub("krunkit", "exit 0")
        (self.state / "vm-default").touch()
        lima = self.home / ".colima" / "_lima" / "colima"
        lima.mkdir(parents=True)
        (lima / "lima.yaml").write_text("vmType: vz\n")
        hint = "The Colima VM default is still vz. Move it to krunkit, keeping its images and volumes: "
        self.assertEqual(self.call("hint_convert").stdout, hint + "sage-runtime convert\n")
        self.assertEqual(self.call('CONVERT_COMMAND="ai-ui migrate"\nhint_convert').stdout, hint + "ai-ui migrate\n")


class Tool(unittest.TestCase):
    """sage-runtime sources the lib from lib/ beside itself: in a checkout, and in the formula's libexec."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def run_tool(self, tool):
        return subprocess.run([tool, "version"], env={"HOME": str(self.root), "PATH": "/usr/bin:/bin"},
                              capture_output=True, text=True, timeout=30)

    def test_an_exec_script_in_bin_runs_the_tool_beside_its_lib(self):
        libexec = self.root / "libexec"
        (libexec / "lib").mkdir(parents=True)
        shutil.copy2(TOOL, libexec / "sage-runtime")
        shutil.copy2(LIB, libexec / "lib" / "sage-runtime.sh")
        (self.root / "bin").mkdir()
        script = self.root / "bin" / "sage-runtime"  # what bin.write_exec_script writes
        script.write_text(f'#!/bin/bash\nexec "{libexec}/sage-runtime" "$@"\n')
        script.chmod(0o755)
        result = self.run_tool(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"^sage-runtime \d+\.\d+\.\d+\n$")

    def test_a_link_to_the_tool_finds_its_lib(self):
        # release-tools.sh leaves links in ~/bin to a checkout's tools in place; a link may lead to another link.
        (self.root / "tap" / "lib").mkdir(parents=True)
        shutil.copy2(TOOL, self.root / "tap" / "sage-runtime")
        shutil.copy2(LIB, self.root / "tap" / "lib" / "sage-runtime.sh")
        links = self.root / "links"
        links.mkdir()
        (links / "relative").symlink_to(Path("..") / "tap" / "sage-runtime")
        (self.root / "bin").mkdir()
        (self.root / "bin" / "sage-runtime").symlink_to(links / "relative")
        result = self.run_tool(self.root / "bin" / "sage-runtime")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"^sage-runtime \d+\.\d+\.\d+\n$")

    def test_without_its_lib_it_says_how_to_mend_it(self):
        shutil.copy2(TOOL, self.root / "sage-runtime")
        result = self.run_tool(self.root / "sage-runtime")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr,
                         f"Error: {self.root}/lib/sage-runtime.sh is missing. Reinstall the formula (brew reinstall "
                         "sage-runtime), or run sage-runtime from a checkout of the tap, which has lib/.\n")

    def test_the_tool_keeps_no_copy_of_the_shared_code(self):
        text = TOOL.read_text()
        self.assertEqual([name for name in SHARED if f"\n{name}() {{\n" in text], [])

    def test_the_formula_installs_the_tool_beside_its_lib(self):
        text = FORMULA.read_text()
        for line in ('libexec.install "sage-runtime"', '(libexec/"lib").install "lib/sage-runtime.sh"',
                     'bin.write_exec_script libexec/"sage-runtime"'):
            self.assertIn(f"    {line}\n", text)
        self.assertNotIn('bin.install "sage-runtime"', text)


if __name__ == "__main__":
    unittest.main()
