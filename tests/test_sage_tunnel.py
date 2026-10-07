"""sage-tunnel against a stand-in Cloudflare API: tunnels, routes, DNS, and a token that never reaches the screen."""

import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "sage-tunnel"
CONNECTOR_TOKEN = "eyJhIjoiY29ubmVjdG9yLXNlY3JldC12YWx1ZSJ9"
API_TOKEN = "api-token-secret-value"


class FakeCloudflare:
    """Just enough of Cloudflare's v4 API, in memory."""

    def __init__(self):
        self.accounts = [{"id": "acct1", "name": "Sage.is"}]
        self.zones = [{"id": "zone1", "name": "startr.cloud", "account": {"id": "acct1", "name": "Sage.is"}}]
        self.tunnels = {}
        self.configs = {}
        self.records = {}
        self.requests = []
        self.next_id = 1

    def handle(self, method, path, query, body):
        self.requests.append((method, path, body))
        parts = path.strip("/").split("/")
        if parts == ["accounts"]:
            return self.accounts
        if parts == ["zones"]:
            return [z for z in self.zones if query.get("name") in (None, z["name"])]
        if parts[:3] == ["accounts", "acct1", "cfd_tunnel"]:
            if len(parts) == 3 and method == "GET":
                return [t for t in self.tunnels.values() if query.get("name") in (None, t["name"])]
            if len(parts) == 3 and method == "POST":
                tid = f"tun{self.next_id}"
                self.next_id += 1
                self.tunnels[tid] = {"id": tid, "name": body["name"], "status": "inactive", "connections": []}
                return self.tunnels[tid]
            tid = parts[3]
            if len(parts) == 4 and method == "DELETE":
                return self.tunnels.pop(tid)
            if parts[4] == "configurations":
                if method == "PUT":
                    self.configs[tid] = body
                return self.configs.get(tid, {"config": None})
            if parts[4] == "token":
                return CONNECTOR_TOKEN
        if parts[:3] == ["zones", "zone1", "dns_records"]:
            if len(parts) == 3 and method == "GET":
                return [r for r in self.records.values() if r["name"] == query.get("name")]
            if len(parts) == 3 and method == "POST":
                rid = f"rec{self.next_id}"
                self.next_id += 1
                self.records[rid] = dict(body, id=rid)
                return self.records[rid]
            if method == "PUT":
                self.records[parts[3]] = dict(body, id=parts[3])
                return self.records[parts[3]]
            if method == "DELETE":
                return self.records.pop(parts[3])
        raise KeyError(f"{method} {path}")


class Base(unittest.TestCase):
    def setUp(self):
        self.cf = FakeCloudflare()
        cf = self.cf

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _serve(self):
                url = urllib.parse.urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                if self.headers.get("Authorization") != f"Bearer {API_TOKEN}":
                    status, payload = 403, {"success": False, "errors": [{"code": 9109, "message": "bad token"}]}
                else:
                    try:
                        status, payload = 200, {"success": True, "result": cf.handle(
                            self.command, url.path, dict(urllib.parse.parse_qsl(url.query)), body)}
                    except KeyError as e:
                        status, payload = 404, {"success": False, "errors": [{"code": 7003, "message": str(e)}]}
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)
        self.env_file = self.dir / ".env"
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.dir),
                    "SAGE_TUNNEL_API": f"http://127.0.0.1:{self.server.server_port}",
                    "CLOUDFLARE_API_TOKEN": API_TOKEN}

    def run_tool(self, *args, **env):
        result = subprocess.run([TOOL, *args], env=self.env | env, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=30)
        for secret in (CONNECTOR_TOKEN, API_TOKEN):
            self.assertNotIn(secret, result.stdout + result.stderr, "a secret reached the screen")
        return result

    def ok(self, *args, **env):
        result = self.run_tool(*args, **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def create(self, host="yt.startr.cloud"):
        return self.ok("create", "yt-transcribe", host, "--service", "http://localhost:8000",
                       "--env-file", str(self.env_file))


class Create(Base):
    def test_a_tunnel_with_its_route_dns_and_token_in_the_env_file(self):
        self.env_file.write_text("YT_COOKIE_SID=keep-me\nCLOUDFLARED_TUNNEL_TOKEN=old\n")
        out = self.create().stdout
        (tunnel,) = self.cf.tunnels.values()
        self.assertEqual(tunnel["name"], "yt-transcribe")
        post = next(b for m, p, b in self.cf.requests if m == "POST" and p.endswith("/cfd_tunnel"))
        self.assertEqual(post["config_src"], "cloudflare")
        self.assertEqual(self.cf.configs[tunnel["id"]]["config"]["ingress"],
                         [{"hostname": "yt.startr.cloud", "service": "http://localhost:8000"},
                          {"service": "http_status:404"}])
        (record,) = self.cf.records.values()
        self.assertEqual((record["type"], record["name"], record["content"], record["proxied"]),
                         ("CNAME", "yt.startr.cloud", f"{tunnel['id']}.cfargotunnel.com", True))
        lines = self.env_file.read_text().splitlines()
        self.assertIn("YT_COOKIE_SID=keep-me", lines)
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={CONNECTOR_TOKEN}", lines)
        self.assertIn("CLOUDFLARED_HOSTNAME=yt.startr.cloud", lines)
        self.assertEqual(sum(line.startswith("CLOUDFLARED_TUNNEL_TOKEN=") for line in lines), 1)
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        self.assertIn("(hidden)", out)

    def test_running_it_again_reuses_the_tunnel_and_the_record(self):
        self.create()
        out = self.create().stdout
        self.assertEqual(len(self.cf.tunnels), 1)
        self.assertEqual(len(self.cf.records), 1)
        self.assertIn("reusing", out)
        self.assertIn("already pointed", out)

    def test_another_kind_of_record_at_the_hostname_is_left_alone(self):
        self.cf.records["a1"] = {"id": "a1", "type": "A", "name": "yt.startr.cloud", "content": "1.2.3.4"}
        result = self.run_tool("create", "yt-transcribe", "yt.startr.cloud", "--service", "http://localhost:8000",
                               "--env-file", str(self.env_file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("already has A records", result.stderr)
        self.assertEqual(self.cf.records["a1"]["content"], "1.2.3.4")
        self.assertFalse(self.env_file.exists())

    def test_a_missing_token_names_the_scopes(self):
        result = self.run_tool("list", CLOUDFLARE_API_TOKEN="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Cloudflare Tunnel: Edit", result.stderr)
        self.assertIn("sage-secret run", result.stderr)

    def test_a_token_that_sees_two_accounts_needs_one_named(self):
        self.cf.accounts.append({"id": "acct2", "name": "Other"})
        result = self.run_tool("list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("CLOUDFLARE_ACCOUNT_ID", result.stderr)
        self.ok("list", CLOUDFLARE_ACCOUNT_ID="acct1")

    def test_a_token_scoped_to_one_zone_finds_its_account_through_the_zone(self):
        self.cf.accounts = []
        self.create()
        (tunnel,) = self.cf.tunnels.values()
        self.assertEqual(tunnel["name"], "yt-transcribe")


class Routes(Base):
    def test_a_second_hostname_joins_the_same_tunnel_before_the_catch_all(self):
        self.create()
        self.ok("route", "yt-transcribe", "crm.startr.cloud", "--service", "http://localhost:8030")
        (tid,) = self.cf.tunnels
        hosts = [r.get("hostname") for r in self.cf.configs[tid]["config"]["ingress"]]
        self.assertEqual(hosts, ["yt.startr.cloud", "crm.startr.cloud", None])
        self.assertEqual(len(self.cf.records), 2)

    def test_unroute_drops_the_hostname_and_its_record(self):
        self.create()
        self.ok("route", "yt-transcribe", "crm.startr.cloud", "--service", "http://localhost:8030")
        self.ok("unroute", "yt-transcribe", "crm.startr.cloud")
        (tid,) = self.cf.tunnels
        self.assertEqual([r.get("hostname") for r in self.cf.configs[tid]["config"]["ingress"]],
                         ["yt.startr.cloud", None])
        self.assertEqual([r["name"] for r in self.cf.records.values()], ["yt.startr.cloud"])

    def test_list_shows_each_tunnel_and_its_hostnames(self):
        self.create()
        out = self.ok("list").stdout
        self.assertIn("yt-transcribe", out)
        self.assertIn("yt.startr.cloud", out)


class Delete(Base):
    def test_it_asks_and_without_a_terminal_wants_yes(self):
        self.create()
        result = self.run_tool("delete", "yt-transcribe")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--yes", result.stderr)
        self.assertEqual(len(self.cf.tunnels), 1)

    def test_yes_removes_the_dns_and_the_tunnel(self):
        self.create()
        self.ok("delete", "yt-transcribe", "--yes")
        self.assertEqual(self.cf.tunnels, {})
        self.assertEqual(self.cf.records, {})

    def test_an_unknown_tunnel_is_named(self):
        result = self.run_tool("delete", "nope", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no tunnel named nope", result.stderr)


if __name__ == "__main__":
    unittest.main()
