"""sage-tunnel against a stand-in Cloudflare API: tunnels, routes, DNS, and a token that never reaches the screen."""

import base64
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


def connector_token(tid, secret="Y29ubmVjdG9yLXNlY3JldC12YWx1ZQ=="):
    """A connector token as cloudflared reads it: base64 JSON of the account, tunnel id and secret."""
    return base64.b64encode(json.dumps({"a": "acct1", "t": tid, "s": secret}).encode()).decode()


CONNECTOR_TOKEN = connector_token("tun1")  # the first tunnel's, before any rotation
API_TOKEN = "api-token-secret-value"
CONNECTORS = [  # two connectors on the leaked token: ours, and one nobody here runs
    {"id": "c1", "run_at": "2026-10-07T15:26:01Z", "conns": [{"origin_ip": "203.0.113.5"}] * 4},
    {"id": "c2", "run_at": "2026-10-08T03:12:44Z", "conns": [{"origin_ip": "198.51.100.7"}] * 2},
]


class ApiError(Exception):
    """An error Cloudflare answers with its own code."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


class FakeCloudflare:
    """Just enough of Cloudflare's v4 API, in memory."""

    def __init__(self):
        self.accounts = [{"id": "acct1", "name": "Sage.is"}]
        self.zones = [{"id": "zone1", "name": "startr.cloud", "account": {"id": "acct1", "name": "Sage.is"}}]
        self.tunnels = {}
        self.tokens = {}
        self.connectors = {}
        self.configs = {}
        self.records = {}
        self.requests = []
        self.secrets = []  # every tunnel secret and connector token handed out
        self.fail = ""  # a path ending that answers with an error; "GET /ending" fails that method alone
        self.lost = ""  # a path ending whose change lands, but whose answer a proxy swaps for an HTML 502
        self.next_id = 1

    def handle(self, method, path, query, body):
        self.requests.append((method, path, body))
        how, _, ending = self.fail.rpartition(" ")
        if ending and path.endswith(ending) and how in ("", method):
            raise ValueError(f"{method} {path} failed")
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
                # No connections field: Cloudflare dropped it from tunnel objects on 2026-10-05.
                self.tunnels[tid] = {"id": tid, "name": body["name"], "status": "inactive",
                                     "config_src": body["config_src"]}
                self.configs[tid] = {"config": None}  # its first configuration GET succeeded live on 2026-10-07
                self.tokens[tid] = connector_token(tid)
                self.secrets.append(self.tokens[tid])
                return self.tunnels[tid]
            tid = parts[3]
            if len(parts) == 4 and method == "DELETE":
                return self.tunnels.pop(tid)
            if len(parts) == 4 and method == "PATCH":
                if len(base64.b64decode(body["tunnel_secret"], validate=True)) < 32:
                    raise ValueError("tunnel_secret must be at least 32 bytes")
                self.tunnels[tid]["name"] = body.get("name", self.tunnels[tid]["name"])
                self.tokens[tid] = connector_token(tid, body["tunnel_secret"])
                self.secrets += [body["tunnel_secret"], self.tokens[tid]]
                return self.tunnels[tid]
            if parts[4] == "configurations":
                if method == "PUT":
                    self.configs[tid] = body
                elif tid not in self.configs:  # seen 2026-10-09 for a tunnel made by cloudflared
                    raise ApiError(404, 1055, "Configuration for tunnel not found")
                return self.configs[tid]
            if parts[4] == "connections":
                if method == "DELETE":
                    self.connectors[tid] = []
                return self.connectors.get(tid, [])
            if parts[4] == "token":
                return self.tokens[tid]
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
                    except ValueError as e:
                        status, payload = 400, {"success": False, "errors": [{"code": 1001, "message": str(e)}]}
                    except ApiError as e:
                        status, payload = e.status, {"success": False, "errors": [{"code": e.code, "message": str(e)}]}
                data = json.dumps(payload).encode()
                if cf.lost and url.path.endswith(cf.lost):
                    status, data = 502, b"<html><body>502 Bad Gateway</body></html>"
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve

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
        for secret in (CONNECTOR_TOKEN, API_TOKEN, *self.cf.secrets):
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

    def test_a_symlink_into_a_missing_folder_stops_it_before_any_change(self):
        self.env_file.symlink_to(self.dir / "missing" / "yt.env")
        result = self.run_tool("create", "yt-transcribe", "yt.startr.cloud", "--service", "http://localhost:8000",
                               "--env-file", str(self.env_file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("/missing for the env file", result.stderr)
        self.assertEqual(self.cf.requests, [])

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

    def test_list_counts_live_connections_from_their_own_endpoint(self):
        self.create()
        (tid,) = self.cf.tunnels
        self.cf.connectors[tid] = list(CONNECTORS)
        self.assertIn(" 6 connections", self.ok("list").stdout)

    def test_a_remote_tunnel_with_no_configuration_yet_takes_its_first_route(self):
        # Error 1055 from a tunnel whose config_src is cloudflare means none was saved yet, not a local tunnel.
        self.cf.tunnels["rem1"] = {"id": "rem1", "name": "fresh", "status": "inactive", "config_src": "cloudflare"}
        self.assertTrue(self.ok("list").stdout.rstrip().endswith(" 0 connections  no hostnames"))
        self.ok("route", "fresh", "new.startr.cloud", "--service", "http://localhost:8000")
        self.assertEqual(self.cf.configs["rem1"]["config"]["ingress"],
                         [{"hostname": "new.startr.cloud", "service": "http://localhost:8000"},
                          {"service": "http_status:404"}])
        self.assertEqual([r["content"] for r in self.cf.records.values()], ["rem1.cfargotunnel.com"])


class Rotate(Base):
    def setUp(self):
        super().setUp()
        self.env_file.write_text("YT_COOKIE_SID=keep-me\n")
        self.create()
        (self.tid,) = self.cf.tunnels
        self.cf.connectors[self.tid] = list(CONNECTORS)

    def rotate(self, *args):
        return self.ok("rotate", "yt-transcribe", "--env-file", str(self.env_file), *args)

    def test_a_new_token_in_the_env_file_and_the_old_connectors_cut_off(self):
        records, config = dict(self.cf.records), self.cf.configs[self.tid]
        self.env_file.chmod(0o644)
        out = self.rotate().stdout
        (patch,) = [b for m, p, b in self.cf.requests if m == "PATCH"]
        self.assertGreaterEqual(len(base64.b64decode(patch["tunnel_secret"], validate=True)), 32)
        self.assertEqual(list(self.cf.tunnels), [self.tid])
        self.assertEqual(self.cf.tunnels[self.tid]["name"], "yt-transcribe")
        self.assertEqual((self.cf.records, self.cf.configs[self.tid]), (records, config))
        token = self.cf.tokens[self.tid]
        self.assertNotEqual(token, CONNECTOR_TOKEN)
        lines = self.env_file.read_text().splitlines()
        self.assertEqual([line for line in lines if line.startswith("CLOUDFLARED_TUNNEL_TOKEN=")],
                         [f"CLOUDFLARED_TUNNEL_TOKEN={token}"])
        self.assertIn("YT_COOKIE_SID=keep-me", lines)
        self.assertIn("CLOUDFLARED_HOSTNAME=yt.startr.cloud", lines)
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        self.assertEqual(self.cf.connectors[self.tid], [])
        self.assertIn("(hidden)", out)
        self.assertIn("connectors on the old token: 2, disconnected", out)
        self.assertIn("203.0.113.5 since 2026-10-07T15:26:01Z", out)
        self.assertIn("198.51.100.7", out)
        self.assertIn("Recreate the service", out)

    def test_it_prints_how_to_replace_a_vault_copy_unseen(self):
        # A vault copy of the old token is dead too. The hint is a command to paste, so it quotes the path.
        spaced = self.dir / "Somma 02" / "yt.env"
        spaced.parent.mkdir()
        self.env_file.rename(spaced)
        out = self.ok("rotate", "yt-transcribe", "--env-file", str(spaced)).stdout
        self.assertIn(f"\n  sage-secret run --env '{spaced}' -- "
                      "sage-secret put ITEM --replace --from-env CLOUDFLARED_TUNNEL_TOKEN\n", out)

    def test_a_failed_connector_list_still_cuts_off_the_old_token(self):
        # The list only shows who ran the old token, so the disconnect goes ahead without it.
        self.cf.fail = "GET /connections"
        result = self.rotate()
        self.assertIn("/connections: 1001", result.stderr)
        self.assertIn("connectors on the old token: unknown, disconnected", result.stdout)
        self.assertEqual(self.cf.connectors[self.tid], [])
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", self.env_file.read_text().splitlines())

    def test_each_rotation_draws_a_new_secret(self):
        self.rotate()
        self.rotate()
        first, second = [b["tunnel_secret"] for m, p, b in self.cf.requests if m == "PATCH"]
        self.assertNotEqual(first, second)
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", self.env_file.read_text().splitlines())

    def test_keep_connectors_leaves_them_serving_until_they_restart(self):
        out = self.rotate("--keep-connectors").stdout
        self.assertEqual(self.cf.connectors[self.tid], CONNECTORS)
        self.assertIn("connectors on the old token: 2, serving until they restart", out)
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", self.env_file.read_text().splitlines())
        # Their list only informs a planned rotation, so one that fails leaves the rotation standing.
        self.cf.fail = "/connections"
        result = self.rotate("--keep-connectors")
        self.assertIn("connectors on the old token: unknown, serving until they restart", result.stdout)
        self.assertIn("/connections: 1001", result.stderr)
        self.assertNotIn("rotate again", result.stderr)
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", self.env_file.read_text().splitlines())
        self.assertEqual(self.cf.connectors[self.tid], CONNECTORS)
        self.assertFalse([p for m, p, b in self.cf.requests if m == "DELETE"])

    def test_a_file_it_cannot_read_or_rewrite_stops_it_before_the_secret_changes(self):
        if os.geteuid() == 0:
            self.skipTest("root reads and writes past file modes")
        for locked, mode in ((self.env_file, 0), (self.dir, 0o500)):  # an unreadable file, a read-only folder
            was = stat.S_IMODE(locked.stat().st_mode)
            locked.chmod(mode)
            try:
                result = self.run_tool("rotate", "yt-transcribe", "--env-file", str(self.env_file))
            finally:
                locked.chmod(was)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Permission denied", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        self.assertFalse([p for m, p, b in self.cf.requests if m in ("PATCH", "DELETE")])
        self.assertEqual(self.cf.tokens[self.tid], CONNECTOR_TOKEN)
        self.assertEqual(self.cf.connectors[self.tid], CONNECTORS)
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={CONNECTOR_TOKEN}", self.env_file.read_text().splitlines())

    def test_a_failure_after_the_new_secret_says_what_it_left_and_a_rerun_finishes(self):
        self.cf.lost = f"/cfd_tunnel/{self.tid}"  # Cloudflare takes the PATCH, but its answer never arrives
        result = self.run_tool("rotate", "yt-transcribe", "--env-file", str(self.env_file))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"/cfd_tunnel/{self.tid}: HTTP 502, not JSON", result.stderr)
        self.assertIn("Cloudflare may have changed the secret anyway, and", result.stderr)
        self.assertIn("still holds the old token. Run rotate again before the service restarts", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotEqual(self.cf.tokens[self.tid], CONNECTOR_TOKEN)
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={CONNECTOR_TOKEN}", self.env_file.read_text().splitlines())
        self.cf.lost = ""
        self.cf.fail = "/token"
        result = self.run_tool("rotate", "yt-transcribe", "--env-file", str(self.env_file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("still holds the old token, which opens no new connections. Run rotate again", result.stderr)
        self.assertNotEqual(self.cf.tokens[self.tid], CONNECTOR_TOKEN)
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={CONNECTOR_TOKEN}", self.env_file.read_text().splitlines())
        self.cf.fail = "/connections"
        result = self.run_tool("rotate", "yt-transcribe", "--env-file", str(self.env_file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("connectors on the old one are still up. To cut them off, run rotate again", result.stderr)
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", self.env_file.read_text().splitlines())
        self.assertEqual(self.cf.connectors[self.tid], CONNECTORS)
        self.cf.fail = ""
        self.rotate()
        self.assertEqual(self.cf.connectors[self.tid], [])
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", self.env_file.read_text().splitlines())

    def test_a_hard_link_to_the_env_file_gets_the_new_token_too(self):
        shared = self.dir / "shared.env"  # *.env files are often hard-linked across repos
        os.link(self.env_file, shared)
        shared.chmod(0o644)
        self.rotate()
        self.assertTrue(shared.samefile(self.env_file))
        self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o600)
        lines = shared.read_text().splitlines()
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", lines)
        self.assertIn("YT_COOKIE_SID=keep-me", lines)

    def test_a_symlinked_env_file_stays_a_link_to_the_new_token(self):
        real = self.dir / "real" / "yt.env"
        real.parent.mkdir()
        self.env_file.rename(real)
        self.env_file.symlink_to(real)
        self.rotate()
        self.assertTrue(self.env_file.is_symlink())
        lines = real.read_text().splitlines()
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", lines)
        self.assertIn("YT_COOKIE_SID=keep-me", lines)

    def test_a_file_without_this_tunnels_token_changes_nothing(self):
        crm_env = self.dir / "crm.env"
        self.ok("create", "crm-app", "crm.startr.cloud", "--service", "http://localhost:8030",
                "--env-file", str(crm_env))
        (crm,) = set(self.cf.tunnels) - {self.tid}
        cases = [("crm-app", self.env_file, f"holds a token for tunnel {self.tid}, not crm-app ({crm})"),
                 ("yt-transcribe", crm_env, f"holds a token for tunnel {crm}, not yt-transcribe ({self.tid})")]
        for file, text in ((".env.example", "YT_COOKIE_SID=\nCLOUDFLARED_TUNNEL_TOKEN=\n"),  # a tracked template
                           ("notes.env", "YT_COOKIE_SID=keep-me\n"),
                           ("draft.env", "CLOUDFLARED_TUNNEL_TOKEN=paste-it-here\n"),
                           ("no-id.env", "CLOUDFLARED_TUNNEL_TOKEN=e30=\n"),  # {}
                           ("list.env", "CLOUDFLARED_TUNNEL_TOKEN=W10=\n")):  # []
            (self.dir / file).write_text(text)
            cases.append(("yt-transcribe", self.dir / file, "has no tunnel token in CLOUDFLARED_TUNNEL_TOKEN"))
        files = {f: f.read_bytes() for _, f, _ in cases}
        tokens = dict(self.cf.tokens)
        for name, env_file, message in cases:
            result = self.run_tool("rotate", name, "--env-file", str(env_file))
            self.assertEqual(result.returncode, 1, env_file)
            self.assertIn(message, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        self.assertFalse([p for m, p, b in self.cf.requests if m in ("PATCH", "DELETE")])
        self.assertEqual(self.cf.tokens, tokens)
        self.assertEqual({f: f.read_bytes() for f in files}, files)
        self.assertEqual(self.cf.connectors[self.tid], CONNECTORS)

    def test_the_last_line_for_a_key_counts_and_another_tunnels_token_stops_it(self):
        mine, other = self.env_file.read_text(), connector_token("tun9")
        for text, key in ((f"{mine}CLOUDFLARED_TUNNEL_TOKEN={other}\n", "CLOUDFLARED_TUNNEL_TOKEN"),
                          (f"{mine}TUNNEL_TOKEN={other}\n", "TUNNEL_TOKEN")):  # the one cloudflared reads itself
            self.env_file.write_text(text)
            result = self.run_tool("rotate", "yt-transcribe", "--env-file", str(self.env_file))
            self.assertEqual(result.returncode, 1, key)
            self.assertIn(f"{key} in {self.env_file} holds a token for tunnel tun9, not yt-transcribe ({self.tid})",
                          result.stderr)
            self.assertEqual(self.env_file.read_text(), text)
        self.assertFalse([p for m, p, b in self.cf.requests if m in ("PATCH", "DELETE")])
        self.env_file.write_text(f"CLOUDFLARED_TUNNEL_TOKEN={other}\n{mine}")  # docker and compose read the last
        self.rotate()
        # The new token takes the first line's place, so a reference below any line for the key still resolves.
        self.assertEqual(self.env_file.read_text().splitlines(),
                         [f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", "YT_COOKIE_SID=keep-me",
                          "CLOUDFLARED_HOSTNAME=yt.startr.cloud"])

    def test_a_vault_reference_stops_it_and_says_so(self):
        # sage-secret run fills in a bw: reference from the vault, which this tool cannot write.
        mine = self.env_file.read_text()
        for text, key in ((mine.replace(f"={CONNECTOR_TOKEN}", "=bw:yt-tunnel"), "CLOUDFLARED_TUNNEL_TOKEN"),
                          (mine.replace(f"={CONNECTOR_TOKEN}", '="bw:yt-tunnel"'), "CLOUDFLARED_TUNNEL_TOKEN"),
                          (f"{mine}TUNNEL_TOKEN=bw:yt-tunnel\n", "TUNNEL_TOKEN")):
            self.env_file.write_text(text)
            result = self.run_tool("rotate", "yt-transcribe", "--env-file", str(self.env_file))
            self.assertEqual(result.returncode, 1, text)
            self.assertIn(f"{key} in {self.env_file} is a vault reference, and rotate cannot write the vault",
                          result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(self.env_file.read_text(), text)
        self.assertFalse([p for m, p, b in self.cf.requests if m in ("PATCH", "DELETE")])
        self.assertEqual(self.cf.tokens[self.tid], CONNECTOR_TOKEN)
        self.assertEqual(self.cf.connectors[self.tid], CONNECTORS)

    def test_a_tunnel_token_line_for_this_tunnel_gets_the_new_token_too(self):
        # Say a cloudflared container runs with --env-file on this file: it reads TUNNEL_TOKEN.
        self.env_file.write_text(self.env_file.read_text() + f"TUNNEL_TOKEN={CONNECTOR_TOKEN}\n")
        self.rotate()
        token = self.cf.tokens[self.tid]
        lines = self.env_file.read_text().splitlines()
        self.assertIn(f"TUNNEL_TOKEN={token}", lines)
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={token}", lines)
        self.assertIn("YT_COOKIE_SID=keep-me", lines)
        # A line that holds no token, such as a reference compose fills in, follows the other key as it is.
        # Compose fills one in only from the lines above it, so the key keeps its place.
        ref = "TUNNEL_TOKEN=${CLOUDFLARED_TUNNEL_TOKEN}"
        self.env_file.write_text(self.env_file.read_text().replace(f"\nTUNNEL_TOKEN={token}", "\n" + ref))
        self.rotate()
        self.assertEqual(self.env_file.read_text().splitlines(),
                         ["YT_COOKIE_SID=keep-me", f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}",
                          "CLOUDFLARED_HOSTNAME=yt.startr.cloud", ref])

    def test_a_quoted_token_still_names_its_tunnel(self):
        self.env_file.write_text(self.env_file.read_text().replace(f"={CONNECTOR_TOKEN}", f'="{CONNECTOR_TOKEN}"'))
        self.assertIn(f'"{CONNECTOR_TOKEN}"', self.env_file.read_text())
        self.rotate()
        self.assertIn(f"CLOUDFLARED_TUNNEL_TOKEN={self.cf.tokens[self.tid]}", self.env_file.read_text().splitlines())

    def test_an_unknown_tunnel_a_mistyped_file_or_a_folder_changes_nothing(self):
        for name, env_file, message in (("nope", self.env_file, "no tunnel named nope"),
                                        ("yt-transcribe", self.dir / ".evn", "is not a file"),
                                        ("yt-transcribe", self.dir, "is not a file")):
            result = self.run_tool("rotate", name, "--env-file", str(env_file))
            self.assertEqual(result.returncode, 1)
            self.assertIn(message, result.stderr)
        self.assertFalse((self.dir / ".evn").exists())
        self.assertFalse([p for m, p, b in self.cf.requests if m == "PATCH"])
        self.assertEqual(self.cf.tokens[self.tid], CONNECTOR_TOKEN)
        self.assertEqual(self.cf.connectors[self.tid], CONNECTORS)


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


class LocalTunnel(Base):
    # A configuration stored for a local tunnel anyway, say by an earlier PUT, names a hostname it never serves.
    STALE = {"config": {"ingress": [{"hostname": "stale.startr.cloud", "service": "http://localhost:9"},
                                    {"service": "http_status:404"}]}}

    def setUp(self):
        super().setUp()
        # Made with cloudflared: its routes live in its config file, and a CNAME points at it.
        self.cf.tunnels["loc1"] = {"id": "loc1", "name": "old-mac", "status": "healthy", "config_src": "local"}
        self.cf.connectors["loc1"] = list(CONNECTORS)
        self.cf.records["old"] = {"id": "old", "type": "CNAME", "name": "old.startr.cloud",
                                  "content": "loc1.cfargotunnel.com", "proxied": True}

    def looks(self):
        """Each way Cloudflare shows such a tunnel, in turn: by its config_src, by its configuration's
        source, or only by error 1055 (seen 2026-10-09)."""
        for src, config in (("local", None), ("local", self.STALE),
                            (None, dict(self.STALE, source="local")), (None, None)):
            self.cf.tunnels["loc1"]["config_src"] = src
            self.cf.configs.pop("loc1", None)
            if config:
                self.cf.configs["loc1"] = config
            yield src, config

    def test_list_notes_it_in_place_of_hostnames_and_goes_on(self):
        self.create()
        note = " 6 connections  configured locally (cloudflared config, not the dashboard)"
        for look in self.looks():
            local, remote = self.ok("list").stdout.splitlines()
            self.assertTrue(local.startswith("old-mac "), look)
            self.assertTrue(local.endswith(note), look)
            self.assertTrue(remote.startswith("yt-transcribe "))
            self.assertTrue(remote.endswith("  yt.startr.cloud"))

    def test_list_still_fails_on_any_other_error(self):
        self.cf.fail = "/configurations"
        for src in (None, "cloudflare"):  # list reads the configuration of either
            self.cf.tunnels["loc1"]["config_src"] = src
            result = self.run_tool("list")
            self.assertEqual(result.returncode, 1, src)
            self.assertIn("/configurations: 1001", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_create_route_unroute_and_delete_refuse_it_and_change_nothing(self):
        service = ("--service", "http://localhost:8000")
        for look in self.looks():
            tunnels, records, configs = dict(self.cf.tunnels), dict(self.cf.records), dict(self.cf.configs)
            for args in (("create", "old-mac", "new.startr.cloud", *service, "--env-file", str(self.env_file)),
                         ("route", "old-mac", "new.startr.cloud", *service),
                         ("unroute", "old-mac", "old.startr.cloud"),
                         ("delete", "old-mac", "--yes")):
                result = self.run_tool(*args)
                self.assertEqual(result.returncode, 1, (args, look))
                self.assertIn("tunnel old-mac is configured locally (cloudflared config, not the dashboard); "
                              "manage it with cloudflared", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
            self.assertEqual((self.cf.tunnels, self.cf.records, self.cf.configs), (tunnels, records, configs), look)
        self.assertFalse([p for m, p, b in self.cf.requests if m != "GET"])
        self.assertFalse(self.env_file.exists())


if __name__ == "__main__":
    unittest.main()
