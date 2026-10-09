"""offload: a drain never offers a folder that holds a VM disk, and a move never takes one."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "offload"
ORBSTACK_DATA = "Library/Group Containers/HUAQ24HBR6.dev.orbstack/data"


@unittest.skipUnless(sys.platform == "darwin", "offload reads macOS paths with BSD stat")
class Base(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        # Not resolved: under /var, which links to /private/var, as a HOT path can reach a drive through a link.
        root = Path(scratch.name)
        self.home, self.hot, self.cold, self.bin = root / "home", root / "hot", root / "cold", root / "bin"
        for folder in (self.home, self.hot, self.cold, self.bin):
            folder.mkdir()
        (self.bin / "lsof").write_text("#!/bin/bash\nexit 0\n")  # nothing open, and no scan of the whole system
        (self.bin / "lsof").chmod(0o755)
        (self.hot / "Old").mkdir()
        (self.hot / "Old" / "notes.txt").write_text("old notes")
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "OFFLOAD_HOT": str(self.hot),
                    "OFFLOAD_COLD": str(self.cold), "OFFLOAD_CONF": str(root / "absent.conf")}

    def run_tool(self, *args, **env):
        return subprocess.run([TOOL, *args], env=self.env | env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60, cwd=self.home)

    def disks(self, folder):
        """A Colima disks folder, as Lima lays one out."""
        (folder / "colima").mkdir(parents=True)
        (folder / "colima" / "datadisk").write_bytes(b"disk")
        return folder

    def link(self, at, to):
        at.parent.mkdir(parents=True, exist_ok=True)
        at.symlink_to(to)

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def assert_kept(self, name, **env):
        result = self.run_tool("drain", name, **env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"'{name}' is not movable: owned by offload or a VM disk", result.stderr)
        self.assertTrue((self.hot / name).is_dir() and not (self.hot / name).is_symlink())


class DrainKeepsVmDisks(Base):
    def test_an_ordinary_folder_drains(self):
        result = self.run_tool("drain", "Old")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("=== drain Old ===", result.stdout)
        self.assertIn("Dry run. Add --apply to move.", result.stdout)

    def test_colimas_disks_folder_linked_into_hot_is_kept(self):
        self.link(self.home / ".colima/_lima/_disks", self.disks(self.hot / "VMs/_disks"))
        self.assert_kept("VMs")

    def test_a_linked_colima_home_keeps_the_disks_inside_it(self):
        self.disks(self.hot / "Stuff/_lima/_disks")
        self.link(self.home / ".colima", self.hot / "Stuff")
        self.assert_kept("Stuff")

    def test_colima_home_from_the_environment_is_kept(self):
        self.disks(self.hot / "ColimaHome/_lima/_disks")
        self.assert_kept("ColimaHome", COLIMA_HOME=str(self.hot / "ColimaHome"))

    def test_the_default_colima_home_is_kept_beside_one_from_the_environment(self):
        self.link(self.home / ".colima/_lima/_disks", self.disks(self.hot / "VMs/_disks"))
        self.assert_kept("VMs", COLIMA_HOME=str(self.home / "elsewhere"))

    def test_colima_home_under_xdg_config_is_kept_while_the_default_is_missing(self):
        self.link(self.home / ".config/colima/_lima/_disks", self.disks(self.hot / "VMs/_disks"))
        self.assert_kept("VMs", XDG_CONFIG_HOME=str(self.home / ".config"))

    def test_a_vm_folder_at_the_hot_root_keeps_every_entry(self):
        (self.hot / "data.img.raw").write_bytes(b"disk")
        self.write_json(self.home / ".orbstack/vmconfig.json", {"data_dir": str(self.hot)})
        self.assertIn("skip  data.img.raw  (owned by offload or a VM disk)", self.run_tool("drain").stdout)
        result = self.run_tool("drain", "@loose-images", "--apply")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.hot / "data.img.raw").is_symlink())
        self.assert_kept("Old")

    def test_forced_colour_does_not_hide_the_vms_in_a_disks_folder_at_the_hot_root(self):
        self.link(self.home / ".colima/_lima/_disks", self.disks(self.hot))
        self.assert_kept("colima", CLICOLOR="1", CLICOLOR_FORCE="1", TERM="xterm-256color")

    def test_orbstacks_data_dir_setting_is_kept(self):
        (self.hot / "Orb").mkdir()
        (self.hot / "Orb/data.img.raw").write_bytes(b"disk")
        self.write_json(self.home / ".orbstack/vmconfig.json", {"memory_mib": 8192, "data_dir": str(self.hot / "Orb")})
        self.assert_kept("Orb")

    def test_orbstacks_default_folder_linked_into_hot_is_kept(self):
        (self.hot / "OrbData").mkdir()
        self.link(self.home / ORBSTACK_DATA, self.hot / "OrbData")
        self.assert_kept("OrbData")

    def test_docker_desktops_data_folder_is_kept(self):
        (self.hot / "DD/DockerDesktop").mkdir(parents=True)
        self.write_json(self.home / "Library/Group Containers/group.com.docker/settings-store.json",
                        {"DataFolder": str(self.hot / "DD/DockerDesktop")})
        self.assert_kept("DD")

    def test_settings_without_a_folder_keep_nothing_extra(self):
        self.write_json(self.home / ".orbstack/vmconfig.json", {"data_dir": ""})
        self.write_json(self.home / "Library/Group Containers/group.com.docker/settings-store.json", {})
        self.disks(self.home / ".colima/_lima/_disks")
        self.assertEqual(self.run_tool("drain", "Old").returncode, 0)

    def test_the_plan_lists_a_vm_disk_as_skipped(self):
        self.link(self.home / ".colima/_lima/_disks", self.disks(self.hot / "VMs/_disks"))
        result = self.run_tool("drain")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = next(l for l in result.stdout.splitlines() if " VMs " in l)
        self.assertIn("skip  VMs  (owned by offload or a VM disk)", line)


class MoveLeavesVmDisks(Base):
    def test_move_vm_apply_moves_no_disk(self):
        disks = self.disks(self.home / ".colima/_lima/_disks")
        (self.home / ORBSTACK_DATA).mkdir(parents=True)
        (self.home / ORBSTACK_DATA / "data.img").write_bytes(b"disk")
        result = self.run_tool("move", "vm", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for label in ("Docker", "Colima", "OrbStack"):
            self.assertIn(f"=== {label} (strategy=native-ui) ===", result.stdout)
        self.assertFalse(disks.is_symlink())
        self.assertEqual((disks / "colima/datadisk").read_bytes(), b"disk")
        self.assertFalse((self.home / ORBSTACK_DATA).is_symlink())
        self.assertEqual(sorted(p.name for p in self.hot.iterdir()), ["Old"])

    def test_list_shows_linked_colima_disks_as_done(self):
        self.link(self.home / ".colima/_lima/_disks", self.disks(self.hot / "Colima/_disks"))
        result = self.run_tool("list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = next(l for l in result.stdout.splitlines() if l.startswith("Colima "))
        self.assertEqual(line.split()[1:5], ["vm", "native-ui", "no", "-"])
        self.assertIn("symlinked (via native UI)", line)


class Relocations(Base):
    def test_the_steps_name_the_folders_the_targets_use(self):
        out = self.run_tool("relocations").stdout
        self.assertIn(f'ln -s "{self.hot}/Colima/_disks" "{self.home}/.colima/_lima/_disks"', out)
        self.assertIn(f"Change to {self.hot}/OrbStack/", out)
        self.assertIn("colima stop --profile NAME", out)

    def test_the_copy_follows_a_disks_folder_that_is_already_a_link(self):
        out = self.run_tool("relocations").stdout
        self.assertIn(f'cp -aH "{self.home}/.colima/_lima/_disks" "{self.hot}/Colima/"', out)

    def test_the_steps_name_colimas_xdg_home_while_the_default_is_missing(self):
        out = self.run_tool("relocations", XDG_CONFIG_HOME=str(self.home / ".config")).stdout
        self.assertIn(f'mv "{self.home}/.config/colima/_lima/_disks" "{self.home}/.config/colima/_lima/_disks.old"', out)

    def test_disks_linked_elsewhere_get_steps_that_stop_on_a_failed_copy(self):
        self.link(self.home / ".colima/_lima/_disks", self.disks(self.hot / "VMs/_disks"))
        out = self.run_tool("relocations").stdout
        self.assertNotIn("Already done", out)
        self.assertIn("If cp fails, stop here.", out)
        self.assertIn(f'If it was a link to a folder other than "{self.hot}/Colima/_disks",', out)

    def test_disks_already_on_hot_get_no_steps(self):
        self.disks(self.hot / "Colima/_disks")
        self.link(self.home / "Drive", self.hot)  # another spelling of HOT: the physical path decides
        self.link(self.home / ".colima/_lima/_disks", self.home / "Drive/Colima/_disks")
        out = self.run_tool("relocations").stdout
        self.assertIn(f'Already done: "{self.home}/.colima/_lima/_disks" resolves to "{self.hot}/Colima/_disks".', out)
        self.assertNotIn("_disks.old", out)


if __name__ == "__main__":
    unittest.main()
