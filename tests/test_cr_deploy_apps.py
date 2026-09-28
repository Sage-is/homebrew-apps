"""Storage, rollback target, instance-file defaults."""

import unittest

from cr_deploy_loader import cr_deploy

OVERRIDE = "TaskTemplate:\n  ContainerSpec:\n    Mounts:\n      - Type: bind\n        Source: /root/Sync/d/\n        Target: /app/data/\n"
FLOATING = {"appName": "a", "nodeId": "", "volumes": [], "serviceUpdateOverride": OVERRIDE, "containerHttpPort": 8080,
            "websocketSupport": True, "deployedVersion": 3, "envVars": [],
            "versions": [{"version": v, "deployedImageName": f"img@sha256:{v}"} for v in (1, 2, 3, 4)]}


class Storage(unittest.TestCase):
    def test_a_bind_mount_counts_as_keeping_data(self):
        self.assertEqual(cr_deploy.bind_mounts(FLOATING), [{"source": "/root/Sync/d/", "target": "/app/data/"}])
        self.assertTrue(cr_deploy.keeps_data(FLOATING))

    def test_no_volume_and_no_bind_mount_keeps_nothing(self):
        self.assertFalse(cr_deploy.keeps_data({"appName": "b", "volumes": []}))


class Rollback(unittest.TestCase):
    def test_goes_to_the_version_before_the_running_one_not_the_newest(self):
        self.assertEqual(cr_deploy.previous_image(FLOATING), "img@sha256:2")

    def test_only_digests_and_captain_builds_are_pinned(self):
        self.assertTrue(cr_deploy.is_pinned("img-captain--trellis:41"))
        self.assertFalse(cr_deploy.is_pinned("ghcr.io/sage-is/ai-ui:3.2.0"))


class Ensure(unittest.TestCase):
    def test_keys_the_file_leaves_out_keep_their_live_values(self):
        want = cr_deploy.desired({"app": "a", "node": "none"}, FLOATING)
        self.assertEqual((want["containerHttpPort"], want["websocketSupport"], want["nodeId"]), (8080, True, ""))
        self.assertEqual(want["serviceUpdateOverride"], OVERRIDE)

    def test_keys_the_file_names_win(self):
        want = cr_deploy.desired({"app": "a", "node": "n1", "port": 8000, "volumes": []}, FLOATING)
        self.assertEqual((want["containerHttpPort"], want["nodeId"], want["volumes"]), (8000, "n1", []))


if __name__ == "__main__":
    unittest.main()
