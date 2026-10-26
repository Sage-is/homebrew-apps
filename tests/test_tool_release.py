"""scripts/tool-release.sh: which version a formula's tarball url names."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "tool-release.sh"
TAP = "https://github.com/Sage-is/homebrew-apps/archive/refs/tags"


def version_of_url(url, tool):
    # The script, sourced: it defines version_of_url and stops before doing any release work.
    run = subprocess.run(["bash", "-c", f'source "$1"; version_of_url "$2" "$3"', "_", str(SCRIPT), url, tool],
                         capture_output=True, text=True, check=True)
    return run.stdout.strip()


class VersionOfUrl(unittest.TestCase):
    def test_a_tool_kept_in_the_tap_is_tagged_tool_dash_v(self):
        self.assertEqual(version_of_url(f"{TAP}/offload-v0.8.1.tar.gz", "offload"), "0.8.1")

    def test_a_hyphenated_tool_name_is_stripped_whole(self):
        self.assertEqual(version_of_url(f"{TAP}/cr-deploy-v0.1.0.tar.gz", "cr-deploy"), "0.1.0")
        self.assertEqual(version_of_url(f"{TAP}/git-release-v1.0.0.tar.gz", "git-release"), "1.0.0")

    def test_another_repos_plain_v_tag_still_parses(self):
        self.assertEqual(version_of_url("https://github.com/Sage-is/work-delegation/archive/refs/tags/v0.1.0.tar.gz",
                                        "work-delegation"), "0.1.0")

    def test_another_tools_tag_is_not_this_tools_version(self):
        self.assertEqual(version_of_url(f"{TAP}/offload-v0.8.1.tar.gz", "cr-deploy"), "")

    def test_a_url_that_names_no_version_gives_nothing(self):
        self.assertEqual(version_of_url(f"{TAP}/main.tar.gz", "offload"), "")
        self.assertEqual(version_of_url(f"{TAP}/offload-v.tar.gz", "offload"), "")
        self.assertEqual(version_of_url(f"{TAP}/offload-v1.0-rc1.tar.gz", "offload"), "")


class TapFormulae(unittest.TestCase):
    def test_a_formula_without_a_version_line_names_its_version_in_the_url(self):
        for formula in sorted((ROOT / "Formula").glob("*.rb")):
            text = formula.read_text()
            if re.search(r'^  version "', text, re.M):
                continue
            url = re.search(r'^  url "(.*)"$', text, re.M).group(1)
            with self.subTest(formula=formula.name):
                self.assertTrue(version_of_url(url, formula.stem), f"{formula.name}: {url}")


if __name__ == "__main__":
    unittest.main()
