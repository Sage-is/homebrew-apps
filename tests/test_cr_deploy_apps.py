"""Storage, rollback target, instance-file defaults."""

import contextlib
import io
import os
import unittest
from unittest import mock

from cr_deploy_loader import FakeCaptain, cr_deploy

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



class ForceSsl(unittest.TestCase):
    """Adding a domain to an app that already forces https must never turn it off."""

    def ensure_posts(self, live):
        cap = FakeCaptain({"/user/apps/appDefinitions": [{"appDefinitions": [live]}]})
        spec = {"app": "warden", "node": "n1", "force_ssl": True, "env": {"SMTP_PORT": "465"}}
        os.environ.pop("CLOUDFLARE_API_TOKEN", None)
        with contextlib.redirect_stdout(io.StringIO()):
            cr_deploy.ensure(cap, spec, dry_run=False)
        return [d for path, d in cap.posts if path.endswith("/update")]

    def live(self, default_ssl):
        return {"appName": "warden", "nodeId": "n1", "forceSsl": True, "hasDefaultSubDomainSsl": default_ssl,
                "customDomain": [], "envVars": [{"key": "ADMIN_TOKEN", "value": "x"}], "volumes": []}

    def test_a_default_subdomain_certificate_keeps_force_ssl_on(self):
        (update,) = self.ensure_posts(self.live(default_ssl=True))
        self.assertTrue(update["forceSsl"])
        self.assertIn({"key": "ADMIN_TOKEN", "value": "x"}, update["envVars"])

    def test_with_no_certificate_anywhere_it_waits_for_one(self):
        updates = self.ensure_posts(self.live(default_ssl=False))
        self.assertFalse(updates[0]["forceSsl"])


class Captain1107(FakeCaptain):
    """Refuses the first two domain connects with 1107: a proxied name in an https-only zone, then a stale resolver."""

    def post(self, path, data):
        super().post(path, data)
        if path.endswith("/customdomain") and sum(p.endswith("/customdomain") for p, _ in self.posts) <= 2:
            raise cr_deploy.ApiError(1107, "Verification Failed.")
        return {}


class FakeRecord:
    def __init__(self):
        self.record, self.switches = {"proxied": True}, []

    def set_proxied(self, proxied):
        self.switches.append(proxied)
        self.record["proxied"] = proxied


class DomainThroughTheProxy(unittest.TestCase):
    def test_a_1107_on_connect_goes_dns_only_once_for_connect_and_certificate(self):
        domain = "warden.example"
        app = {"appName": "warden", "nodeId": "n1", "envVars": [], "volumes": [], "customDomain": []}
        connected = app | {"customDomain": [{"publicDomain": domain, "hasSsl": False}]}
        cap = Captain1107({"/user/apps/appDefinitions": [{"appDefinitions": [app]}, {"appDefinitions": [connected]}]})
        record = FakeRecord()
        with mock.patch.object(cr_deploy, "dns_ensure", return_value=record), \
                mock.patch.object(cr_deploy.DomainChecks, "POLL_S", 0), \
                contextlib.redirect_stdout(io.StringIO()):
            cr_deploy.ensure(cap, {"app": "warden", "node": "n1", "domain": domain}, dry_run=False)
        paths = [p.rsplit("/", 1)[1] for p, _ in cap.posts]
        self.assertEqual(paths, ["customdomain"] * 3 + ["enablecustomdomainssl"])
        self.assertEqual(record.switches, [False, True])


if __name__ == "__main__":
    unittest.main()
