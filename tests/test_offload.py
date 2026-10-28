"""offload: a drain never offers a folder that holds a VM disk, and a move never takes one."""

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
        # Headroom 0: the scratch "drives" share the boot disk, which may have less than 10% free.
        # The user temp dir is scratch too, so CodeSignClones never reads the real X folder.
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "OFFLOAD_HOT": str(self.hot),
                    "OFFLOAD_COLD": str(self.cold), "OFFLOAD_CONF": str(root / "absent.conf"),
                    "OFFLOAD_HEADROOM": "0", "OFFLOAD_DARWIN_USER_TEMP_DIR": str(root / "T")}
        self.root = root

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


class Helpers(Base):
    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def tree(self, root, files):
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)


class Contract(Helpers):
    """Black-box behaviour every version must keep: the port to Python is held to these."""

    def test_help_prints_the_design_narrative(self):
        result = self.run_tool("help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("DESIGN — Poka-Yoke Discipline", result.stdout)
        self.assertIn("SUBCOMMANDS", result.stdout)

    def test_version_is_semver_shaped(self):
        result = self.run_tool("version")
        self.assertRegex(result.stdout.strip(), r"^offload v\d+\.\d+\.\d+$")

    def test_unknown_subcommand_fails(self):
        result = self.run_tool("frobnicate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown subcommand: frobnicate", result.stderr)

    def test_status_reports_config_space_and_targets(self):
        out = self.run_tool("status").stdout
        self.assertIn(f"TARGET_HOME={self.home} ===", out)
        self.assertIn("(not found — using defaults)", out)
        self.assertIn("=== Disk space ===", out)
        self.assertRegex(out, r"total: \d+ \| on-disk: \d+ \| symlinked: \d+ \| absent: \d+ \| admin-only: 2")
        self.assertIn("(no log yet)", out)

    def test_list_shows_on_disk_and_absent_rows(self):
        self.tree(self.home, {"Library/Application Support/Signal/db.sqlite": "x" * 2048})
        out = self.run_tool("list").stdout
        signal = next(l for l in out.splitlines() if l.startswith("Signal "))
        self.assertIn("on-disk", signal)
        steam = next(l for l in out.splitlines() if l.startswith("Steam "))
        self.assertTrue(steam.rstrip().endswith("absent"))
        self.assertIn("Potential reclaim from offloading remaining items:", out)

    def test_checklist_marks_done_rows_and_names_commands(self):
        self.tree(self.hot, {"MovedAppData/home/Steam/x": "1"})
        self.link(self.home / "Library/Application Support/Steam", self.hot / "MovedAppData/home/Steam")
        out = self.run_tool("checklist").stdout
        self.assertIn(f"# HOT  drive:  {self.hot}", out)
        self.assertRegex(out, r"- \[x\] Steam +symlink +symlinked")
        self.assertIn('offload move Signal --apply  &&  offload launch "Signal"', out)
        self.assertIn(f"Admin only: offload protected --target-home {self.home} Messages --apply", out)

    def test_dry_run_move_changes_nothing(self):
        self.tree(self.home, {"Library/Application Support/obsidian/a.txt": "a"})
        result = self.run_tool("move", "Obsidian")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mode=DRY-RUN", result.stdout)
        self.assertIn("RUN:", result.stdout)
        self.assertFalse((self.home / "Library/Application Support/obsidian").is_symlink())
        self.assertFalse((self.hot / "MovedAppData").exists())

    def test_symlink_move_copies_verifies_and_links(self):
        self.tree(self.home, {"Library/Application Support/obsidian/a.txt": "alpha",
                              "Library/Application Support/obsidian/sub/b.bin": "beta" * 100})
        result = self.run_tool("move", "Obsidian", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        src = self.home / "Library/Application Support/obsidian"
        dst = self.hot / "MovedAppData/home/Obsidian"
        self.assertTrue(src.is_symlink())
        self.assertEqual(Path(src.resolve()), Path(dst.resolve()))
        self.assertEqual((dst / "sub/b.bin").read_text(), "beta" * 100)
        self.assertIn("DONE:", result.stdout)
        again = self.run_tool("move", "Obsidian", "--apply")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("SKIP:", again.stdout)

    def test_archive_moves_downloads_to_cold_and_leaves_an_empty_folder(self):
        self.tree(self.home, {"Downloads/old.pdf": "pdf", "Downloads/deep/x.iso": "iso"})
        result = self.run_tool("move", "Downloads", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archives = list((self.cold / "Archive").iterdir())
        self.assertEqual(len(archives), 1)
        self.assertTrue(archives[0].name.startswith("Downloads-home-"))
        self.assertEqual((archives[0] / "deep/x.iso").read_text(), "iso")
        self.assertEqual(list((self.home / "Downloads").iterdir()), [])

    def test_merge_needs_an_existing_destination(self):
        self.tree(self.home, {"Documents/Projects/Clones/repo/x": "1"})
        result = self.run_tool("move", "Clones", "--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("must already exist (merge target)", result.stderr)
        (self.hot / "Clones").mkdir()
        result = self.run_tool("move", "Clones", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.hot / "Clones/repo/x").read_text(), "1")
        self.assertFalse((self.home / "Documents/Projects/Clones").exists())

    def test_purge_empties_a_cache(self):
        self.tree(self.home, {".npm/_cacache/blob": "cache"})
        result = self.run_tool("move", "NpmCache", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.home / ".npm").is_dir())
        self.assertEqual(list((self.home / ".npm").iterdir()), [])

    def test_sweep_deletes_only_build_cruft(self):
        self.tree(self.home, {"Documents/Projects/GitHub/app/node_modules/pkg/index.js": "x",
                              "Documents/Projects/GitHub/app/src/main.js": "keep"})
        result = self.run_tool("move", "BuildCruft", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.home / "Documents/Projects/GitHub/app/node_modules").exists())
        self.assertEqual((self.home / "Documents/Projects/GitHub/app/src/main.js").read_text(), "keep")

    def test_move_with_no_match_fails(self):
        result = self.run_tool("move", "NoSuchThing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no targets matched 'NoSuchThing'", result.stderr)

    def test_protected_requires_another_users_home(self):
        result = self.run_tool("protected", "Messages")
        self.assertEqual(result.returncode, 1)
        self.assertIn("requires --target-home", result.stderr)

    def test_admin_rows_are_skipped_in_your_own_home(self):
        result = self.run_tool("move", "protected", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("requires Admin mode", result.stderr)

    def test_config_extends_replaces_and_reads_legacy_rows(self):
        conf = self.home / "offload.conf"
        conf.write_text('OFFLOAD_TARGETS+=(\n  "Extra|app|<TH>/Extra|HOT/Extra|symlink|no|-"\n)\n'
                        'OFFLOAD_APPS=(\n  "Legacy|<TH>/LegacyApp|LegacyBundle"\n)\n')
        out = self.run_tool("list", OFFLOAD_CONF=str(conf)).stdout
        self.assertIn("Extra ", out)
        self.assertIn("Legacy ", out)
        self.assertIn("Signal ", out)
        conf.write_text('OFFLOAD_TARGETS=(\n  "Only|app|<TH>/Only|HOT/Only|symlink|no|-"\n)\n')
        out = self.run_tool("list", OFFLOAD_CONF=str(conf)).stdout
        self.assertIn("Only ", out)
        self.assertNotIn("Signal ", out)

    def test_launch_refuses_when_the_data_drive_is_unmounted(self):
        self.stub("osascript", f'echo "$@" >> "{self.home}/osascript.log"')
        self.stub("open", f'echo "$@" >> "{self.home}/open.log"')
        self.link(self.home / "Library/Application Support/Signal", self.hot / "gone/Signal")
        result = self.run_tool("launch", "Signal")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not mounted", (self.home / "osascript.log").read_text())
        self.assertFalse((self.home / "open.log").exists())

    def test_launch_opens_the_app_when_its_data_is_reachable(self):
        self.stub("open", f'echo "$@" >> "{self.home}/open.log"')
        (self.hot / "Signal").mkdir()
        self.link(self.home / "Library/Application Support/Signal", self.hot / "Signal")
        result = self.run_tool("launch", "Signal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.home / "open.log").read_text().strip(), "-a Signal")


class TrimPure(unittest.TestCase):
    """Signal trim selection is pure: cutoffs, sizes, names, dedupe, containment."""

    def test_old_big_rows_are_picked_and_paths_deduped(self):
        mod = load_offload()
        rows = [("Ann", 20 << 20, 1000, "ab/c1"), ("Ann", 20 << 20, 1000, "ab/c1"),
                ("Bob", 1 << 20, 1000, "ab/c2"), ("Ann", 20 << 20, 9999, "ab/c3")]
        picked = mod.trim_select(rows, cutoff_ms=2000, min_bytes=10 << 20, who="")
        self.assertEqual([p[3] for p in picked], ["ab/c1"])

    def test_who_filters_by_conversation_substring(self):
        mod = load_offload()
        rows = [("Ann Lee", 20 << 20, 1000, "a"), ("Bob", 20 << 20, 1000, "b")]
        self.assertEqual([p[3] for p in mod.trim_select(rows, 2000, 1, "ann")], ["a"])
        self.assertEqual(mod.trim_select(rows, 2000, 1, "zzz"), [])

    def test_escaping_paths_are_rejected(self):
        mod = load_offload()
        with tempfile.TemporaryDirectory() as scratch:
            base = os.path.realpath(os.path.join(scratch, "attachments.noindex"))
            os.makedirs(base)
            self.assertEqual(mod.trim_target_path(base, "ab/c1"), os.path.join(base, "ab/c1"))
            self.assertIsNone(mod.trim_target_path(base, "../../evil"))
            self.assertIsNone(mod.trim_target_path(base, "ab/../../../evil"))


@unittest.skipUnless(shutil.which("sqlcipher"), "trim reads Signal's store through the sqlcipher CLI")
class TrimMove(Helpers):
    """SignalStorage end to end against a fake store: real SQLCipher db, real files."""

    KEY = "ab" * 32
    OLD = 1700000000000  # Nov 2023: older than any sane cutoff under test

    def setUp(self):
        super().setUp()
        # The tool subprocess sees only self.bin:/usr/bin:/bin: link the real
        # sqlcipher in (guarded present by the class skipUnless).
        (self.bin / "sqlcipher").symlink_to(shutil.which("sqlcipher"))

    def signal_profile(self, attaches, convs=(("c1", "Ann"),)):
        prof = self.home / "Library/Application Support/Signal"
        (prof / "config.json").parent.mkdir(parents=True, exist_ok=True)
        (prof / "config.json").write_text(json.dumps({"encryptedKey": "00"}))
        stmts = ["PRAGMA key=\"x'%s'\";" % self.KEY,
                 "CREATE TABLE message_attachments(conversationId TEXT, attachmentType TEXT,"
                 " sentAt INTEGER, size INTEGER, path TEXT);",
                 "CREATE TABLE conversations(id TEXT, name TEXT, profileName TEXT, e164 TEXT);"]
        for cid, name in convs:
            stmts.append(f"INSERT INTO conversations VALUES ('{cid}', '{name}', NULL, NULL);")
        for cid, atype, sent, size, path in attaches:
            stmts.append(f"INSERT INTO message_attachments VALUES ('{cid}', '{atype}',"
                         f" {sent}, {size}, '{path}');")
        db = prof / "sql/db.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["sqlcipher", str(db)], input="\n".join(stmts),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return prof

    def attach_files(self, spec):
        for rel, size in spec.items():
            p = self.home / "Library/Application Support/Signal/attachments.noindex" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * size)

    def trim_env(self, **extra):
        env = {"OFFLOAD_TRIM_RAW_KEY": self.KEY, "OFFLOAD_TRIM_BEFORE_DAYS": "30",
               "OFFLOAD_TRIM_MIN_MB": "1"}
        env.update(extra)
        return env

    def standard_fixtures(self):
        now_ms = int(time.time() * 1000)
        self.signal_profile([("c1", "attachment", self.OLD, 2 << 20, "ab/old.bin"),
                             ("c1", "attachment", now_ms, 2 << 20, "ab/new.bin"),
                             ("c1", "attachment", self.OLD, 100, "ab/small.bin")])
        self.attach_files({"ab/old.bin": 2 << 20, "ab/new.bin": 2 << 20, "ab/small.bin": 100})

    def test_trim_skips_a_non_signal_store(self):
        result = self.run_tool("move", "SignalStorage", **self.trim_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SKIP:", result.stdout)

    def test_trim_dry_run_reports_the_batch_and_changes_nothing(self):
        self.standard_fixtures()
        result = self.run_tool("move", "SignalStorage", **self.trim_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ann", result.stdout)
        self.assertIn("match: 1 files", result.stdout)
        store = self.home / "Library/Application Support/Signal/attachments.noindex/ab"
        self.assertTrue((store / "old.bin").exists())

    def test_trim_apply_deletes_only_the_batch(self):
        self.standard_fixtures()
        self.stub("pgrep", "exit 1")  # Signal not running
        result = self.run_tool("move", "SignalStorage", "--apply", **self.trim_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        store = self.home / "Library/Application Support/Signal/attachments.noindex/ab"
        self.assertFalse((store / "old.bin").exists())
        self.assertTrue((store / "new.bin").exists())
        self.assertTrue((store / "small.bin").exists())
        self.assertIn("freed", result.stdout)

    def test_trim_skips_a_path_that_escapes_the_store(self):
        self.signal_profile([("c1", "attachment", self.OLD, 2 << 20, "../../evil.bin")])
        (self.home / "Library/Application Support/Signal/attachments.noindex").mkdir(parents=True)
        evil = self.home / "Library/Application Support/evil.bin"
        evil.parent.mkdir(parents=True, exist_ok=True)
        evil.write_text("precious")
        self.stub("pgrep", "exit 1")
        result = self.run_tool("move", "SignalStorage", "--apply", **self.trim_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SKIP (outside store)", result.stderr)
        self.assertEqual(evil.read_text(), "precious")

    def test_trim_refuses_apply_while_signal_runs(self):
        self.standard_fixtures()
        self.stub("pgrep", "echo 99999")  # Signal running
        result = self.run_tool("move", "SignalStorage", "--apply", **self.trim_env())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("quit Signal first", result.stderr)
        store = self.home / "Library/Application Support/Signal/attachments.noindex/ab"
        self.assertTrue((store / "old.bin").exists())

    def test_list_shows_the_trim_batch(self):
        self.standard_fixtures()
        out = self.run_tool("list", **self.trim_env()).stdout
        row = next(l for l in out.splitlines() if l.startswith("SignalStorage "))
        self.assertIn("1 files,", row)


class IntendedFixes(Helpers):
    """Bugs in bash v0.7.0 that the port fixes. These fail on bash by design."""

    def test_no_arguments_prints_help(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SUBCOMMANDS", result.stdout)

    def test_an_existing_destination_is_a_conflict_exit_2(self):
        self.tree(self.home, {"Library/Application Support/Signal/a.txt": "a"})
        self.tree(self.hot, {"MovedAppData/home/Signal/other.txt": "b"})
        result = self.run_tool("move", "Signal", "--apply")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("exists — manual review required", result.stderr)
        self.assertFalse((self.home / "Library/Application Support/Signal").is_symlink())

    def test_target_home_token_expands_in_config(self):
        conf = self.home / "offload.conf"
        conf.write_text('OFFLOAD_APPS=(\n  "Luminar|<TARGET_HOME>/Library/Luminar|Luminar"\n)\n')
        self.tree(self.home, {"Library/Luminar/x": "1"})
        out = self.run_tool("list", OFFLOAD_CONF=str(conf)).stdout
        line = next(l for l in out.splitlines() if l.startswith("Luminar "))
        self.assertIn("on-disk", line)

    def test_config_is_parsed_not_run(self):
        conf = self.home / "offload.conf"
        marker = self.home / "ran"
        conf.write_text(f'OFFLOAD_HOT="{self.hot}"\ntouch "{marker}"\n')
        result = self.run_tool("list", OFFLOAD_CONF=str(conf))
        self.assertFalse(marker.exists(), "a config line ran as a command")
        self.assertEqual(result.returncode, 1)
        self.assertIn("offload.conf", result.stderr)


class FakeVolumes(Helpers):
    """Scratch volumes reported through stub mount, diskutil -plist, tmutil and a Backblaze fixture."""

    def setUp(self):
        super().setUp()
        self.vols = self.root / "vols"
        self.plists = self.root / "plists"
        for folder in (self.vols, self.plists):
            folder.mkdir()
        self.mounts, self.tm_dest, self.tm_included, self.bz, self.images = [], [], [], [], []
        self.stub("mount", f'cat "{self.root}/mounts.txt"')
        self.stub("diskutil", f'[ "$1" = info ] && cat "{self.plists}/$(basename "$3").plist" 2>/dev/null')
        self.stub("hdiutil", f'[ "$1" = info ] && cat "{self.root}/hdiutil.plist"')
        self.stub("tmutil", f'case "$1" in destinationinfo) cat "{self.root}/tmdest.txt";;\n'
                            f'isexcluded) grep -qxF "$2" "{self.root}/tminc.txt" && echo "[Included] $2" '
                            '|| echo "[Excluded] $2";; esac')
        self.env |= {"OFFLOAD_VOLUMES": str(self.vols), "OFFLOAD_BZINFO": str(self.root / "bzinfo.xml")}
        self.flush()

    def flush(self):
        (self.root / "mounts.txt").write_text("".join(self.mounts))
        (self.root / "tmdest.txt").write_text("".join(f"Mount Point   : {m}\n" for m in self.tm_dest))
        (self.root / "tminc.txt").write_text("".join(m + "\n" for m in self.tm_included))
        (self.root / "bzinfo.xml").write_text(
            "<bzinfo>" + "".join(f'<bzdirfilter dir="{m.lower()}/" whichfiles="all" />' for m in self.bz) + "</bzinfo>")
        (self.root / "hdiutil.plist").write_bytes(plistlib.dumps({"images": [
            {"image-path": image, "image-encrypted": encrypted,
             "system-entities": [{"dev-entry": "/dev/disk8s1", "mount-point": mount}]}
            for image, mount, encrypted in self.images]}))

    def volume(self, name, fs="apfs", ssd=True, removable=False, owners=False, encrypted=False,
               tm="excluded", bb=False, mounted=True, image=None):
        path = self.vols / name
        path.mkdir()
        # An attached disk image, as diskutil reports one: removable, and never "encrypted" itself.
        info = {"FilesystemType": fs, "BusProtocol": "Disk Image" if image else "USB", "Internal": False,
                "Removable": removable or bool(image), "GlobalPermissionsEnabled": owners}
        if ssd is not None:
            info["SolidState"] = ssd
        if fs == "apfs":
            info["Encryption"] = encrypted and not image
        if image:
            self.images.append((str(image), str(path), encrypted))
        (self.plists / f"{name}.plist").write_bytes(plistlib.dumps(info))
        if mounted:
            kind = "nfs" if fs == "nfs" else fs
            self.mounts.append(f"/dev/disk9s{len(self.mounts) + 1} on {path} ({kind}, local, journaled)\n")
        {"dest": self.tm_dest, "included": self.tm_included}.get(tm, []).append(str(path))
        if bb:
            self.bz.append(str(path))
        self.flush()
        return path

    def row(self, out, name):
        return next(l for l in out.splitlines() if l.startswith(name + " "))


class EveryDrive(FakeVolumes):
    """Phase 1: offload sees every mounted volume, and picks a drive per move."""

    def test_drives_shows_role_backup_owners_and_encryption(self):
        fast = self.volume("Fast", tm="included", bb=True)
        self.volume("Slow", fs="hfs", ssd=None, bb=True)
        self.volume("Stick", fs="exfat", ssd=None, removable=True)
        self.volume("Net", fs="nfs", ssd=None)
        self.volume("Backup", tm="dest")
        self.volume("Spare")
        out = self.run_tool("drives", OFFLOAD_HOT=str(fast)).stdout
        self.assertRegex(self.row(out, "Fast"), r"APFS +yes .* TM\+BB +off +no +HOT ")
        self.assertRegex(self.row(out, "Slow"), r"HFS\+ +- .* BB +off +no +COLD ")
        self.assertIn("removable media", self.row(out, "Stick"))
        self.assertIn("network or FUSE mount", self.row(out, "Net"))
        self.assertRegex(self.row(out, "Backup"), r"TM dest .*EXCLUDED +Time Machine destination")
        self.assertIn(f'OFFLOAD_DRIVES+=("{self.vols}/Slow" "{self.vols}/Spare")', out)

    def test_private_data_needs_an_encrypted_volume_with_ownership(self):
        plain = self.volume("Plain")
        vault = self.volume("Vault", owners=True, encrypted=True)
        self.tree(self.home, {"Library/Application Support/Signal/db": "secret"})
        result = self.run_tool("move", "Signal", "--apply", OFFLOAD_HOT=str(plain))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("private data needs an encrypted volume", result.stderr)
        self.assertFalse((self.home / "Library/Application Support/Signal").is_symlink())
        result = self.run_tool("move", "Signal", "--apply", OFFLOAD_HOT=str(vault))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((vault / "MovedAppData/home/Signal/db").read_text(), "secret")

    def test_an_encrypted_disk_image_on_a_drive_takes_private_data(self):
        slow = self.volume("Slow", fs="hfs", ssd=None, bb=True)
        vault = self.volume("Vault", ssd=None, owners=True, encrypted=True, image=slow / "Vault.sparsebundle")
        self.assertRegex(self.row(self.run_tool("drives").stdout, "Vault"),
                         r"APFS +- .* BB +on +yes +COLD +disk image on Slow")
        self.tree(self.home, {"Library/Application Support/Signal/db": "secret"})
        result = self.run_tool("move", "Signal", "--apply", OFFLOAD_HOT=str(vault))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((vault / "MovedAppData/home/Signal/db").read_text(), "secret")

    def test_a_disk_image_stored_on_the_boot_drive_frees_nothing(self):
        vault = self.volume("Vault", owners=True, encrypted=True, image=self.root / "Vault.sparsebundle")
        self.assertIn("disk image stored on the boot drive", self.row(self.run_tool("drives").stdout, "Vault"))
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a"})
        result = self.run_tool("move", "Obsidian", "--apply", OFFLOAD_HOT=str(vault))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("frees nothing", result.stderr)

    def test_a_folder_under_volumes_that_is_not_mounted_is_refused(self):
        ghost = self.volume("Ghost", mounted=False)
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a"})
        result = self.run_tool("move", "Obsidian", "--apply", OFFLOAD_HOT=str(ghost))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("not mounted", result.stderr)
        self.assertEqual(list(ghost.iterdir()), [])

    def test_an_opted_in_drive_takes_the_move_when_hot_is_missing(self):
        ghost, spare = self.volume("Ghost", mounted=False), self.volume("Spare")
        conf = self.home / "offload.conf"
        conf.write_text(f'OFFLOAD_DRIVES=(\n  "{spare}"\n)\n')
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a"})
        result = self.run_tool("move", "Obsidian", "--apply", OFFLOAD_HOT=str(ghost), OFFLOAD_CONF=str(conf))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((spare / "MovedAppData/home/Obsidian/a").read_text(), "a")

    def test_network_and_exfat_volumes_never_take_a_move(self):
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a"})
        for name, fs, why in (("Net", "nfs", "a network or FUSE mount"),
                              ("Stick", "exfat", "exfat has no Unix permissions")):
            vol = self.volume(name, fs=fs, ssd=None)
            result = self.run_tool("move", "Obsidian", "--apply", OFFLOAD_HOT=str(vol))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(why, result.stderr)

    def test_headroom_refuses_a_move_that_would_fill_the_drive(self):
        fast = self.volume("Fast")
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a"})
        result = self.run_tool("move", "Obsidian", "--apply", OFFLOAD_HOT=str(fast), OFFLOAD_HEADROOM="99")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("would leave less than 99% free", result.stderr)

    def test_a_symlink_inside_the_drive_is_a_conflict(self):
        fast = self.volume("Fast")
        (self.root / "elsewhere").mkdir()
        (fast / "MovedAppData").symlink_to(self.root / "elsewhere")
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a"})
        result = self.run_tool("move", "Obsidian", "--apply", OFFLOAD_HOT=str(fast))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("through a symlink or another device", result.stderr)
        self.assertEqual(list((self.root / "elsewhere").iterdir()), [])

    def test_a_leftover_copy_on_another_drive_is_a_conflict(self):
        fast, spare = self.volume("Fast"), self.volume("Spare")
        self.tree(spare, {"MovedAppData/home/Obsidian/old": "half a copy"})
        conf = self.home / "offload.conf"
        conf.write_text(f'OFFLOAD_DRIVES=(\n  "{spare}"\n)\n')
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a"})
        result = self.run_tool("move", "Obsidian", "--apply", OFFLOAD_HOT=str(fast), OFFLOAD_CONF=str(conf))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("exists — manual review required", result.stderr)


class PlanAndPrices(FakeVolumes):
    """Phase 2: plan is advice only (no moves, no network); prices come from a dated table."""

    def setUp(self):
        super().setUp()
        self.calls = self.root / "calls.log"
        self.stub("curl", f'echo "curl $*" >> "{self.calls}"; exit 1')
        self.stub("rclone", f'echo "rclone $*" >> "{self.calls}"; echo "rclone v1.75.1"')
        self.conf = self.home / "offload.conf"

    def plan(self, conf="", **env):
        self.conf.write_text(conf)
        result = self.run_tool("plan", OFFLOAD_CONF=str(self.conf), **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls.read_text() if self.calls.exists() else ""
        self.assertNotIn("curl", calls)
        self.assertEqual({line.split()[1] for line in calls.splitlines()} - {"version"}, set())
        return result.stdout

    def test_plan_fits_targets_and_keeps_private_data_internal(self):
        fast = self.volume("Fast")
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a" * 4096,
                              "Library/Application Support/Signal/db": "s" * 4096})
        out = self.plan(OFFLOAD_HOT=str(fast))
        self.assertRegex(out, r"Obsidian +Fast \(HOT\)")
        self.assertRegex(out, r"Signal +keep internal: private")
        self.assertIn("rclone v1.75.1", out)
        self.assertFalse((fast / "MovedAppData").exists())
        self.assertTrue((self.home / "Library/Application Support/obsidian").is_dir())

    def test_plan_suggests_a_drain_when_hot_is_below_headroom(self):
        fast = self.volume("Fast")
        out = self.plan(OFFLOAD_HOT=str(fast), OFFLOAD_HEADROOM="99")
        self.assertIn("--- HOT is below 99% free ---", out)
        self.assertRegex(out, r"offload drain --need \d+")

    def test_plan_never_promises_cold_for_a_hot_row_that_move_refuses(self):
        ghost, slow = self.volume("Ghost", mounted=False), self.volume("Slow", fs="hfs", ssd=None)
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a" * 4096})
        out = self.plan(OFFLOAD_HOT=str(ghost), OFFLOAD_COLD=str(slow))
        self.assertRegex(out, r"Obsidian +waits for HOT: .*not mounted")
        self.assertNotIn("COLD: slower", out)
        self.assertIn("placeable now: 0B", out)
        result = self.run_tool("move", "Obsidian", "--apply", OFFLOAD_HOT=str(ghost), OFFLOAD_COLD=str(slow))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("No HOT drive can take Obsidian", result.stderr)

    def test_the_drain_covers_headroom_and_the_hot_rows_waiting(self):
        fast = self.volume("Fast")
        self.tree(self.home, {"Library/Application Support/obsidian/a": "a" * 4096})
        out = self.plan(OFFLOAD_HOT=str(fast), OFFLOAD_HEADROOM="99")
        self.assertRegex(out, r"Obsidian +waits for HOT: .*would leave less than 99% free")
        self.assertRegex(out, r"offload drain --need \d+ +# 99% headroom plus [\d.]+KiB of HOT rows waiting")

    def test_a_shortfall_is_priced_and_stale_prices_are_flagged(self):
        (self.home / "prices.tsv").write_text("b2|cloud|Backblaze B2|6.95|0|month|0|2020-01-01|old row\n")
        out = self.plan("OFFLOAD_BOOT_FREE_PCT=99\n")
        self.assertRegex(out, r"--- Options for the .* shortfall ---")
        self.assertRegex(out, r"Backblaze B2 +\$[\d,.]+/mo")
        self.assertRegex(out, r"22 TB external HDD +\$689\.00 once")
        self.assertIn("Prices were last checked", out)

    def test_prices_lists_rows_and_a_user_row_overrides_by_id(self):
        (self.home / "prices.tsv").write_text(
            "b2|cloud|Backblaze B2 (team rate)|5|0|month|0|2026-10-01|negotiated\n")
        self.conf.write_text("")
        out = self.run_tool("prices", OFFLOAD_CONF=str(self.conf)).stdout
        self.assertIn("Backblaze B2 (team rate)", out)
        self.assertIn("Amazon S3 Glacier Deep Archive", out)
        self.assertEqual(out.count("b2 "), 1)

    def test_a_malformed_price_row_fails(self):
        (self.home / "prices.tsv").write_text("bad|row\n")
        self.conf.write_text("")
        result = self.run_tool("prices", OFFLOAD_CONF=str(self.conf))
        self.assertEqual(result.returncode, 1)
        self.assertIn("a price row needs 9 cells", result.stderr)

    def test_prices_update_fetches_the_team_table_only_when_asked(self):
        team = self.root / "team-offload"
        team.write_text("# script\nPRICES = '''\n# @@PRICES-BEGIN@@\n"
                        "id|kind|name|usd_per_tb|min_usd|unit|min_days|checked|note\n"
                        "b2|cloud|Backblaze B2|7.50|0|month|0|2027-01-01|new rate\n# @@PRICES-END@@\n'''\n")
        (self.home / "prices.tsv").write_text("b2|cloud|Backblaze B2|1|0|month|0|2026-01-01|mine\n")
        self.conf.write_text("")
        result = self.run_tool("prices", "--update", OFFLOAD_CONF=str(self.conf),
                               OFFLOAD_PRICES_URL=team.as_uri())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("2027-01-01|new rate", (self.home / "prices.tsv").read_text())
        self.assertIn("mine", (self.home / "prices.tsv.old").read_text())


class Repos(Helpers):
    """Phase 3: cold clones leave a bundle on COLD and go; unsafe ones move whole; reclone brings them back."""

    def setUp(self):
        super().setUp()
        self.projects, self.remotes = self.home / "Documents/Projects", self.root / "remotes"
        self.projects.mkdir(parents=True)
        self.remotes.mkdir()
        ident = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "T",
                 "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_NOSYSTEM": "1"}
        self.env |= ident
        self.genv = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", **ident}

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                              env=self.genv).stdout.strip()

    def backdate(self, path, days):
        stamp = time.time() - days * 86400
        os.utime(path, (stamp, stamp))

    def make_repo(self, rel, age=200, remote=True, pushed=True, unpushed=False, dirty=None, stash=False, ignored=()):
        repo = self.projects / rel
        repo.mkdir(parents=True)
        self.git(repo, "init", "-q", "-b", "main")
        (repo / "README").write_text("hello\n")
        (repo / ".gitignore").write_text(".env\nnode_modules/\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", "init")
        if remote:
            bare = self.remotes / (repo.name + ".git")
            self.git(self.remotes, "init", "-q", "--bare", str(bare))
            self.git(repo, "remote", "add", "origin", str(bare))
            if pushed:
                self.git(repo, "push", "-q", "-u", "origin", "main")
        if unpushed:
            (repo / "more").write_text("more\n")
            self.git(repo, "add", "more")
            self.git(repo, "commit", "-q", "-m", "more")
        if stash:
            (repo / "README").write_text("changed\n")
            self.git(repo, "stash", "-q")
        for name in ignored:
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text("ignored\n")
        if dirty is not None:
            (repo / "draft.txt").write_text("draft\n")
            self.backdate(repo / "draft.txt", dirty)
        for probe in ("logs/HEAD", "index", "HEAD"):
            self.backdate(repo / ".git" / probe, age)
        return repo

    def verdicts(self, *args):
        result = self.run_tool("repos", *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = {}
        for line in result.stdout.splitlines()[1:]:   # fixed width: VERDICT(8) AGE(6) SIZE(8) REPO(44) REASON
            if line[:8].strip() in ("reclone", "move", "synced", "keep"):
                rows[line[27:71].strip()] = (line[:8].strip(), line)
        return rows

    def test_each_repo_gets_a_verdict_with_its_reason(self):
        self.make_repo("clean")
        self.make_repo("dirty", dirty=200)
        self.make_repo("drafting", dirty=1)
        self.make_repo("unpushed", unpushed=True)
        self.make_repo("stashed", stash=True)
        self.make_repo("secret", ignored=[".env"])
        self.make_repo("cruft", ignored=["node_modules/pkg/index.js"])
        self.make_repo("norem", remote=False)
        self.make_repo("active", age=5)
        (self.projects / "Synced").mkdir()
        (self.projects / "Synced/.stfolder").mkdir()
        self.make_repo("Synced/shared")
        rows = self.verdicts()
        expect = {"clean": "reclone", "cruft": "reclone", "dirty": "move", "unpushed": "move", "stashed": "move",
                  "secret": "move", "norem": "move", "drafting": "keep", "active": "keep", "Synced/shared": "synced"}
        self.assertEqual({k: v[0] for k, v in rows.items()}, expect)
        self.assertIn("uncommitted changes", rows["dirty"][1])
        self.assertIn("unpushed commits", rows["unpushed"][1])
        self.assertIn("a stash", rows["stashed"][1])
        self.assertIn("ignored files the remote lacks: .env", rows["secret"][1])
        self.assertIn("no remote", rows["norem"][1])
        self.assertIn("uncommitted edits 1 d ago", rows["drafting"][1])
        self.assertIn("Syncthing", rows["Synced/shared"][1])
        self.assertEqual(self.verdicts("--synced-ok")["Synced/shared"][0], "reclone")

    def test_a_leftover_stignore_is_not_a_live_sync_folder(self):
        (self.projects / "Old").mkdir()
        (self.projects / "Old/.stignore").write_text("*.tmp\n")
        self.make_repo("Old/archive")
        self.assertEqual(self.verdicts()["Old/archive"][0], "reclone")

    def test_age_comes_from_the_reflog_not_the_index(self):
        repo = self.make_repo("touched")
        os.utime(repo / ".git/index")            # what a plain `git status` by any scanner does
        self.assertEqual(self.verdicts()["touched"][0], "reclone")

    def test_a_dry_run_moves_nothing(self):
        clean = self.make_repo("clean")
        result = self.run_tool("move", "Repos")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((clean / ".git").is_dir())
        self.assertFalse((self.cold / "offload").exists())

    def test_apply_bundles_clean_clones_copies_unsafe_ones_and_reclone_restores(self):
        clean, dirty = self.make_repo("clean"), self.make_repo("dirty", dirty=200)
        active = self.make_repo("active", age=5)
        sha = self.git(clean, "rev-parse", "HEAD")
        result = self.run_tool("move", "Repos", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        store = self.cold / "offload/Repos/home"
        self.assertFalse(clean.exists())
        self.assertTrue((store / "clean.bundle").is_file())
        self.assertFalse(dirty.exists())
        self.assertEqual((store / "dirty/draft.txt").read_text(), "draft\n")
        self.assertTrue((active / ".git").is_dir())
        manifest = (self.root / "reclone.tsv").read_text()
        self.assertIn(f"\treclone\t{clean}\t", manifest)
        self.assertIn(f"\tmoved\t{dirty}\t", manifest)
        result = self.run_tool("reclone", str(clean), "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git(clean, "rev-parse", "HEAD"), sha)
        self.assertIn("restored from the remote", result.stdout)

    def test_reclone_falls_back_to_the_bundle_when_the_remote_is_gone(self):
        import shutil as sh
        clean = self.make_repo("clean")
        sha, url = self.git(clean, "rev-parse", "HEAD"), self.git(clean, "remote", "get-url", "origin")
        self.assertEqual(self.run_tool("move", "Repos", "--apply").returncode, 0)
        sh.rmtree(self.remotes / "clean.git")
        result = self.run_tool("reclone", "all", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("restored from the bundle", result.stdout)
        self.assertEqual(self.git(clean, "rev-parse", "HEAD"), sha)
        self.assertEqual(self.git(clean, "remote", "get-url", "origin"), url)


class Cloud(Helpers):
    """Phase 4: CLOUD/ rows upload only with --apply --upload, only to an approved remote, check before delete."""

    def setUp(self):
        super().setUp()
        self.cloud, self.calls = self.root / "cloud", self.root / "rclone.log"
        self.stub("rclone", f'''echo "$*" >> "{self.calls}"
loc() {{ case "$1" in *:*) echo "{self.cloud}/${{1%%:*}}/${{1#*:}}";; *) echo "$1";; esac; }}
case "$1" in
  listremotes) printf 'test-crypt: crypt\\ntest-plain: s3\\n';;
  copy) mkdir -p "$(loc "$3")" && cp -R "$(loc "$2")/." "$(loc "$3")/";;
  check|cryptcheck) [ -f "{self.root}/failcheck" ] && exit 1; diff -r "$(loc "$2")" "$(loc "$3")" >/dev/null;;
esac''')
        self.conf = self.home / "offload.conf"
        self.tree(self.home, {"Old/a.txt": "alpha", "Old/sub/b.txt": "beta"})

    def configure(self, remote="test-crypt:offload", private=False):
        rows = 'OFFLOAD_TARGETS+=(\n  "Old|cold|<TH>/Old|CLOUD/<U>/Old|cloud|no|-"\n)\n'
        approved = f'OFFLOAD_APPROVED_REMOTES=(\n  "{remote}"\n)\n' if remote else ""
        self.conf.write_text(rows + approved + ('OFFLOAD_PRIVATE_LABELS+=(\n  "Old"\n)\n' if private else ""))

    def move(self, *args):
        return self.run_tool("move", "Old", *args, OFFLOAD_CONF=str(self.conf))

    def calls_of(self, verb):
        return [l for l in (self.calls.read_text().splitlines() if self.calls.exists() else []) if l.startswith(verb)]

    def test_apply_without_upload_sends_nothing(self):
        self.configure()
        result = self.move("--apply")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("needs --upload as well as --apply", result.stderr)
        self.assertEqual(self.calls_of("copy"), [])
        self.assertEqual((self.home / "Old/a.txt").read_text(), "alpha")

    def test_a_dry_run_makes_no_upload(self):
        self.configure()
        result = self.move()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WOULD UPLOAD", result.stdout)
        self.assertEqual(self.calls_of("copy"), [])

    def test_no_approved_remote_no_upload(self):
        self.configure(remote="")
        result = self.move("--apply", "--upload")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("OFFLOAD_APPROVED_REMOTES", result.stderr)

    def test_private_data_only_goes_to_a_crypt_remote(self):
        self.configure(remote="test-plain:bucket", private=True)
        result = self.move("--apply", "--upload")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("may only go to a crypt remote", result.stderr)
        self.assertEqual(self.calls_of("copy"), [])

    def test_upload_copies_checks_records_then_removes_and_restore_brings_it_back(self):
        self.configure()
        result = self.move("--apply", "--upload")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.cloud / "test-crypt/offload/home/Old/sub/b.txt").read_text(), "beta")
        self.assertEqual(list((self.home / "Old").iterdir()), [])
        verbs = [l.split()[0] for l in self.calls.read_text().splitlines() if l.split()[0] in ("copy", "cryptcheck")]
        self.assertEqual(verbs, ["copy", "cryptcheck"])
        self.assertIn("\tcloud\t", (self.home / "reclone.tsv").read_text())   # beside offload.conf
        result = self.run_tool("restore", "Old", "--apply", OFFLOAD_CONF=str(self.conf))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.home / "Old/sub/b.txt").read_text(), "beta")
        self.assertTrue((self.cloud / "test-crypt/offload/home/Old/a.txt").exists())

    def test_a_failed_check_keeps_the_source(self):
        self.configure()
        (self.root / "failcheck").write_text("")
        result = self.move("--apply", "--upload")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.home / "Old/a.txt").read_text(), "alpha")


class CodeSignClones(FakeVolumes):
    """Stale Chromium code-sign clones go; a clone held open or younger than the grace stays; no link is followed."""
    DAY = 24 * 60                                      # clone ages are in minutes

    def setUp(self):
        super().setUp()
        self.x = self.root / "X"                      # beside OFFLOAD_DARWIN_USER_TEMP_DIR, as getconf's T is
        self.lsof_out = self.root / "lsof.out"
        self.stub("lsof", f'echo "$@" >> "{self.root}/lsof.args"; cat "{self.lsof_out}" 2>/dev/null; exit 1')

    def clone(self, bundle, name, age_min):
        path = self.x / f"{bundle}.code_sign_clone" / name
        self.tree(path, {"App.app.bundle/Contents/MacOS/App": "binary"})
        self.age(path, age_min)
        return path

    def age(self, path, age_min, follow_symlinks=True):
        when = time.time() - age_min * 60
        os.utime(path, (when, when), follow_symlinks=follow_symlinks)

    def held(self, path):
        """lsof -Fn output for a file open in the clone, named by its real path as lsof names it."""
        with open(self.lsof_out, "a") as f:
            f.write(f"p4242\nftxt\nn{path.resolve()}/App.app.bundle/Contents/MacOS/App\n")

    def sweep(self, *args, **env):
        result = self.run_tool("move", "CodeSignClones", *args, **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_a_dry_run_names_what_goes_and_what_stays(self):
        old = self.clone("com.google.Chrome", "code_sign_clone.Old", 9 * self.DAY)
        young = self.clone("com.google.Chrome", "code_sign_clone.Young", 2)
        brave = self.clone("com.brave.Browser", "code_sign_clone.Only", 30 * self.DAY)
        out = self.sweep()
        self.assertIn(f"RUN: rm -rf {old}", out)
        self.assertIn(f"RUN: rm -rf {brave}", out)
        self.assertIn(f"KEEP (made under 10 min ago): {young}", out)
        self.assertIn("Total: up to", out)
        self.assertIn("free space can lag", out)
        self.assertTrue(all(p.is_dir() for p in (old, young, brave)))

    def test_an_old_unheld_clone_goes_even_when_it_is_the_only_or_newest_one(self):
        old = self.clone("com.google.Chrome", "code_sign_clone.Old", 9 * self.DAY)
        newest = self.clone("com.google.Chrome", "code_sign_clone.Newest", self.DAY)
        only = self.clone("com.brave.Browser", "code_sign_clone.Only", 30 * self.DAY)
        self.sweep("--apply")
        self.assertFalse(any(p.exists() for p in (old, newest, only)))
        self.assertTrue(newest.parent.is_dir() and only.parent.is_dir())

    def test_a_clone_younger_than_the_grace_stays(self):
        old = self.clone("com.google.Chrome", "code_sign_clone.Old", 9 * self.DAY)
        young = self.clone("com.google.Chrome", "code_sign_clone.Young", 2)
        out = self.sweep("--apply")
        self.assertFalse(old.exists())
        self.assertTrue(young.is_dir())
        self.assertIn(f"KEEP (made under 10 min ago): {young}", out)
        self.assertIn(f"RUN: rm -rf {young}", self.sweep(OFFLOAD_CLONE_GRACE_MIN="1"))

    def test_a_held_open_clone_stays_at_any_age(self):
        old = self.clone("com.google.Chrome", "code_sign_clone.Old", 9 * self.DAY)
        busy = self.clone("com.google.Chrome", "code_sign_clone.Busy", 30 * self.DAY)
        self.held(busy)
        out = self.sweep("--apply")
        self.assertFalse(old.exists())
        self.assertTrue(busy.is_dir())
        self.assertIn(f"KEEP (held open): {busy}", out)
        holder = (self.x / "com.google.Chrome.code_sign_clone").resolve()
        self.assertIn(f"-w -Fn +D {holder}", (self.root / "lsof.args").read_text())

    def test_only_clone_folders_inside_a_code_sign_clone_folder_go(self):
        stale = self.clone("com.google.Chrome", "code_sign_clone.Old", 9 * self.DAY)
        others = ["X/com.google.Chrome.code_sign_clone/other.Old/a", "X/com.google.Chrome.code_sign_clone/code_sign_clone./a",
                  "X/com.google.Chrome.code_sign_clone/code_sign_clone.File", "X/com.example.cache/code_sign_clone.Old/a",
                  "X/code_sign_clone.Old/a"]
        self.tree(self.root, {rel: "keep" for rel in others})
        for rel in others:                            # old, so none is spared by the grace
            self.age(self.root / rel, 30 * self.DAY)
            self.age((self.root / rel).parent, 30 * self.DAY)
        self.sweep("--apply")
        self.assertFalse(stale.exists())
        for rel in others:
            self.assertEqual((self.root / rel).read_text(), "keep", rel)

    def test_links_are_never_followed(self):
        outside = self.root / "precious"
        self.tree(outside, {"doc.txt": "keep", "code_sign_clone.Old/doc.txt": "keep"})
        for folder in (outside, outside / "code_sign_clone.Old"):
            self.age(folder, 30 * self.DAY)
        links = [self.x / "com.google.Chrome.code_sign_clone/code_sign_clone.Link",   # a linked clone
                 self.x / "com.evil.code_sign_clone"]                                  # a linked browser folder
        for link in links:
            self.link(link, outside)
            self.age(link, 30 * self.DAY, follow_symlinks=False)   # old itself, so the grace spares nothing
        out = self.sweep("--apply")
        self.assertNotIn("rm -rf", out)
        self.assertEqual((outside / "doc.txt").read_text(), "keep")
        self.assertEqual((outside / "code_sign_clone.Old/doc.txt").read_text(), "keep")
        self.assertTrue(links[0].is_symlink())

    def test_a_failed_lsof_keeps_every_clone(self):
        old = self.clone("com.google.Chrome", "code_sign_clone.Old", 9 * self.DAY)
        self.clone("com.google.Chrome", "code_sign_clone.Other", self.DAY)
        self.stub("lsof", 'echo "lsof: status error: Permission denied" >&2; exit 1')
        out = self.sweep("--apply")
        self.assertTrue(old.is_dir())
        self.assertIn(f"KEEP (lsof cannot tell what is open): {old}", out)
        self.assertIn("Total: nothing stale", out)

    def test_list_status_checklist_and_plan_show_the_row(self):
        absent = int(re.search(r"absent: (\d+)", self.run_tool("status").stdout).group(1))
        self.clone("com.google.Chrome", "code_sign_clone.Old", 9 * self.DAY)
        self.clone("com.google.Chrome", "code_sign_clone.Young", 2)
        self.clone("com.brave.Browser", "code_sign_clone.Only", 3 * self.DAY)
        self.assertRegex(self.row(self.run_tool("list").stdout, "CodeSignClones"),
                         r"^CodeSignClones +sweep +sweep +no +\S+ +2 stale clones, 1 kept$")
        self.assertIn(f"absent: {absent - 1} |", self.run_tool("status").stdout)
        self.assertIn("offload move CodeSignClones --apply", self.run_tool("checklist").stdout)
        plan = self.run_tool("plan")
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertIn("CodeSignClones frees up to", plan.stdout)
        self.assertIn("free space can lag", plan.stdout)

    def test_another_users_home_has_no_clones_to_sweep(self):
        self.clone("com.google.Chrome", "code_sign_clone.Old", 9 * self.DAY)
        (self.root / "alice").mkdir()
        row = self.row(self.run_tool("--target-home", str(self.root / "alice"), "list").stdout, "CodeSignClones")
        self.assertTrue(row.rstrip().endswith("absent"), row)


def load_offload():
    """The script as a module (it has no .py suffix), registered so dataclasses can resolve it."""
    import importlib.util
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("offload_under_test", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


class PickDest(unittest.TestCase):
    """Placements in one run add up: a drive's free space is promised only once."""

    def test_reservations_add_up_within_a_run(self):
        from unittest import mock
        mod = load_offload()
        with tempfile.TemporaryDirectory() as hot:
            mod.S.hot, mod.S.headroom, mod._INVENTORY = hot, 10, []
            fake = mock.Mock(f_blocks=1000, f_frsize=1, f_bavail=500)
            with mock.patch.object(mod.os, "statvfs", return_value=fake):
                self.assertEqual(mod.pick_dest("HOT", 300, "Obsidian"), hot)
                with self.assertRaises(mod.Fatal) as refused:
                    mod.pick_dest("HOT", 300, "Steam")
            self.assertIn("would leave less than 10% free", str(refused.exception))


class ClonePathOk(unittest.TestCase):
    """The check run on every clone path, again just before its rm: one real folder, two levels below X."""

    def test_only_a_real_clone_folder_passes(self):
        mod = load_offload()
        with tempfile.TemporaryDirectory() as scratch:
            x = Path(scratch) / "X"                    # unresolved: /var links to /private/var
            for rel in ("com.google.Chrome.code_sign_clone/code_sign_clone.Ok", "elsewhere/code_sign_clone.Old",
                        "com.example.cache/code_sign_clone.Old", "com.google.Chrome.code_sign_clone/code_sign_clone.Ok/code_sign_clone.In"):
                (x / rel).mkdir(parents=True)
            (x / "com.google.Chrome.code_sign_clone/code_sign_clone.File").write_text("f")
            (x / "com.google.Chrome.code_sign_clone/code_sign_clone.Link").symlink_to(x / "elsewhere")
            (x / "com.link.code_sign_clone").symlink_to(x / "elsewhere")
            ok = lambda rel: mod.clone_path_ok(str(x / rel), str(x))
            self.assertTrue(ok("com.google.Chrome.code_sign_clone/code_sign_clone.Ok"))
            self.assertFalse(ok("com.link.code_sign_clone/code_sign_clone.Old"))         # through a linked holder
            self.assertFalse(ok("com.google.Chrome.code_sign_clone/code_sign_clone.Link"))
            self.assertFalse(ok("com.google.Chrome.code_sign_clone/code_sign_clone.File"))
            self.assertFalse(ok("com.example.cache/code_sign_clone.Old"))
            self.assertFalse(ok("com.google.Chrome.code_sign_clone/code_sign_clone.Ok/code_sign_clone.In"))
            self.assertFalse(ok("com.google.Chrome.code_sign_clone/code_sign_clone."))
            self.assertFalse(mod.clone_path_ok(str(x / "com.google.Chrome.code_sign_clone/code_sign_clone.Ok"),
                                               str(x / "com.google.Chrome.code_sign_clone")))


if __name__ == "__main__":
    unittest.main()
