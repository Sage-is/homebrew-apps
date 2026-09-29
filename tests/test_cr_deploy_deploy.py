"""Deploys: tags refused, a build must be seen starting, a dead app fails the check."""

import contextlib
import io
import unittest
from unittest import mock

from cr_deploy_loader import FakeCaptain, cr_deploy

APPS = "/user/apps/appDefinitions"
DATA = "/user/apps/appData/a"


def app_at(version: int) -> dict:
    return {"appDefinitions": [{"appName": "a", "deployedVersion": version}]}


class Deploy(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(cr_deploy, "POLL_S", 0), mock.patch.object(cr_deploy.time, "sleep")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        quiet = contextlib.redirect_stdout(io.StringIO())  # the tool reports progress on stdout
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def test_a_tag_is_refused_before_anything_is_sent(self):
        cap = FakeCaptain({})
        with self.assertRaises(SystemExit):
            cr_deploy.deploy_image(cap, "a", "ghcr.io/sage-is/ai-ui:3.2.0")
        self.assertEqual(cap.posts, [])

    def test_idle_before_the_build_starts_is_not_success(self):
        cap = FakeCaptain({
            APPS: [app_at(7)],
            DATA: [{"isAppBuilding": False, "isBuildFailed": True},  # the last build's state, stale
                   {"isAppBuilding": True},
                   {"isAppBuilding": False, "isBuildFailed": False}],
        })
        self.assertTrue(cr_deploy.wait_build(cap, "a", before=7))

    def test_an_app_that_never_answers_fails_the_deploy(self):
        with mock.patch.object(cr_deploy, "answers", return_value=False), mock.patch.object(cr_deploy, "LIVE_TIMEOUT_S", 0):
            ok = cr_deploy.check_live("a", "https://x/api/config", ("version", "3.2.0"), None)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
