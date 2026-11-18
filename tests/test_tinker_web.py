"""tinker-web: the settings file, the docker run, invites, the tunnel and the formula."""

import hashlib
import json
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TINKER_WEB = Path(__file__).resolve().parent.parent / "tinker-web"
FORMULA = TINKER_WEB.parent / "Formula" / "tinker-web.rb"
HOST_ENTRY = "--add-host=host.docker.internal:host-gateway"
MODEL_LINE = "web demo: the model comes from http://sage-ai:8080 (tinker-build)"

# Stand-ins log each call to $STATE/calls. Docker keeps a little state as marker
# files: the image, the network, the container, whether it is up and the image
# it runs. A pull writes the engine address and the size of the docker config it
# was given to $STATE/pull-env. `logs` prints $STATE/logs; `exec` prints invite
# codes. With $STATE/page-down, nothing answers on the page.
STUBS = {
    "docker": """ctx="${DOCKER_CONTEXT:-default}"
if [[ "${1:-}" == --context ]]; then ctx="$2"; shift 2; fi
echo "docker $ctx $*" >> "$STATE/calls"
case "$1 ${2:-}" in
  "info "*)          ;;
  "context show")    echo colima ;;
  "context inspect") echo "unix://$STATE/$ctx.sock" ;;
  "image inspect")   [[ -e "$STATE/image" ]] ;;
  "pull "*)          echo "DOCKER_HOST=${DOCKER_HOST:-} config=$(ls -A "${DOCKER_CONFIG:-$HOME/.docker}" 2>/dev/null | wc -l | tr -d ' ')" > "$STATE/pull-env"
                     touch "$STATE/image" ;;
  "network inspect") [[ -e "$STATE/network" ]] ;;
  "network create")  touch "$STATE/network" ;;
  "container inspect")
    name="${@: -1}"
    [[ -e "$STATE/container-$name" ]] || exit 1
    if [[ "$*" == *State.Running* ]]; then
      if [[ -e "$STATE/up-$name" ]]; then echo true; else echo false; fi
    elif [[ "$*" == *State.Status* ]]; then
      echo "Container: running"; echo "Image:     $(cat "$STATE/image-$name")"
    else
      cat "$STATE/image-$name"
    fi ;;
  "run -d")
    prev=""
    for a in "$@"; do [[ "$prev" == --name ]] && name="$a"; prev="$a"; done
    touch "$STATE/container-$name" "$STATE/up-$name"
    echo "${@: -1}" > "$STATE/image-$name" ;;
  "rm -f")           rm -f "$STATE/container-${@: -1}" "$STATE/up-${@: -1}" ;;
  "stop "*)          rm -f "$STATE/up-${@: -1}" ;;
  "logs "*)          cat "$STATE/logs" 2>/dev/null || true ;;
  "exec "*)          echo CODE-ONE; echo CODE-TWO ;;
esac""",
    # Colima's everyday VM runs, and Docker sits on its data disk.
    "colima": """echo "colima $*" >> "$STATE/calls"
case "$1" in
  list) echo '{"name":"default","status":"Running"}' ;;
  ssh)  cat > /dev/null; echo data ;;
esac""",
    "curl": """echo "curl $*" >> "$STATE/calls"
[[ ! -e "$STATE/page-down" ]]""",
    "ssh": 'echo "ssh $*" >> "$STATE/calls"',
    "open": 'echo "open $*" >> "$STATE/calls"',
    "hostname": "echo studio",
}


@unittest.skipUnless(sys.platform == "darwin", "tinker-web targets a Mac")
class Mac(unittest.TestCase):
    """A fake Mac: its own home and stand-in commands."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.home, self.state, self.bin = root / "home", root / "state", root / "bin"
        for folder in (self.home, self.state, self.bin):
            folder.mkdir()
        for name, body in STUBS.items():
            self.stub(name, body)
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "STATE": str(self.state)}
        self.env_file = self.home / ".sage-is" / "tinker-web.env"

    def stub(self, name, body):
        stub = self.bin / name
        stub.write_text("#!/bin/bash\n" + body + "\n")
        stub.chmod(0o755)

    def run_cli(self, *args, **env):
        return subprocess.run([TINKER_WEB, *args], env=self.env | env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def ok(self, *args, **env):
        result = self.run_cli(*args, **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def fails(self, *args, **env):
        result = self.run_cli(*args, **env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def docker_runs(self):
        return [call for call in self.calls() if " run -d " in call]

    def settings(self):
        lines = self.env_file.read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))

    def logs(self, text):
        (self.state / "logs").write_text(text + "\n")


class FirstStart(Mac):
    def test_the_settings_file_is_private_and_names_no_node_until_one_is_set(self):
        self.ok("start")
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        self.assertEqual(self.settings(), {"TINKER_WEB_PORT": "8799"})
        self.assertIn("# TENKTS_WEB_NODE=https://app.tinker.click", self.env_file.read_text())

    def test_the_container_restarts_with_docker_and_listens_on_loopback_only(self):
        self.ok("start")
        (run,) = self.docker_runs()
        for part in ("--name tinker-web", "--restart unless-stopped", "--network sage-network", HOST_ENTRY,
                     "-p 127.0.0.1:8799:8799", "-v tinker-web-data:/data", f"--env-file {self.env_file}",
                     "-e TENKTS_SERVER_BIND=0.0.0.0:8799", "ghcr.io/sage-is/tinker-web:latest"):
            self.assertIn(part, run)

    def test_there_is_no_way_off_loopback(self):
        self.fails("start", "--bind", "0.0.0.0")
        self.assertFalse(self.docker_runs())

    def test_the_first_start_points_at_invites_and_says_no_model_comes_without_a_token(self):
        out = self.ok("start").stdout
        self.assertIn("Web Tinker is running at http://localhost:8799", out)
        self.assertIn("tinker-web invite", out)
        self.assertIn("No node token", out)
        self.assertNotIn("tinker-web invite", self.ok("start").stdout)

    def test_the_image_is_pulled_only_when_missing_and_update_always_pulls(self):
        self.ok("start")
        self.ok("start")
        self.assertEqual(len([c for c in self.calls() if " pull " in c]), 1)
        self.ok("update")
        self.assertEqual(len([c for c in self.calls() if " pull " in c]), 2)

    def test_the_public_image_is_pulled_with_no_credential_helper_on_the_context_s_engine(self):
        # A Keychain helper refuses a session without a screen, such as SSH.
        (self.home / ".docker").mkdir()
        (self.home / ".docker" / "config.json").write_text('{"credsStore": "osxkeychain"}')
        self.ok("start")
        self.assertEqual((self.state / "pull-env").read_text().strip(),
                         f"DOCKER_HOST=unix://{self.state}/colima.sock config=0")
        (pull,) = [c for c in self.calls() if " pull " in c]
        self.assertTrue(pull.startswith("docker default pull "), pull)

    def test_a_tag_picks_the_image(self):
        self.ok("start", "--tag", "0.1.0")
        self.assertTrue(self.docker_runs()[0].endswith("ghcr.io/sage-is/tinker-web:0.1.0"))


class Port(Mac):
    def test_a_port_is_saved_and_published_on_loopback_to_the_server_port(self):
        self.ok("start", "--port", "8798")
        self.assertEqual(self.settings()["TINKER_WEB_PORT"], "8798")
        self.ok("start")
        for run in self.docker_runs():
            self.assertIn("-p 127.0.0.1:8798:8799", run)
            self.assertIn("-e TENKTS_SERVER_BIND=0.0.0.0:8799", run)
        self.assertEqual(self.ok("open", SSH_CONNECTION="1").stdout.strip(), "http://localhost:8798")

    def test_a_port_that_is_not_one_is_refused(self):
        for port in ("0", "65536", "80a", ""):
            with self.subTest(port=port):
                self.fails("start", "--port", port)
        self.assertFalse(self.docker_runs())


class SecondCopy(Mac):
    def test_a_name_gives_its_own_container_volume_and_settings(self):
        self.ok("start", "--port", "8798", TINKER_WEB_NAME="tinker-web-s13")
        (run,) = self.docker_runs()
        self.assertIn("--name tinker-web-s13", run)
        self.assertIn("-v tinker-web-s13-data:/data", run)
        self.assertTrue((self.home / ".sage-is" / "tinker-web-s13.env").exists())
        self.assertFalse(self.env_file.exists())
        self.assertFalse([c for c in self.calls() if c.endswith(" tinker-web") or "tinker-web-data" in c])

    def test_a_name_that_could_leave_the_settings_folder_is_refused(self):
        for name in ("../x", "Tinker", "-x", "a b"):
            with self.subTest(name=name):
                self.fails("start", TINKER_WEB_NAME=name)
        self.assertFalse(self.calls())


class Model(Mac):
    TOKEN = "sk-node-token-0123456789"

    def linked(self):
        self.ok("start")
        with self.env_file.open("a") as f:
            f.write(f"TENKTS_WEB_NODE=http://sage-ai:8080\nTENKTS_WEB_NODE_TOKEN={self.TOKEN}\n")

    def test_with_a_token_start_reports_the_node_s_model(self):
        self.linked()
        self.logs(MODEL_LINE)
        out = self.ok("start").stdout
        self.assertIn("the model comes from http://sage-ai:8080 (tinker-build)", out)
        self.assertNotIn("No node token", out)

    def test_a_refused_token_is_a_warning(self):
        self.linked()
        self.logs("web demo: no model.")
        self.assertIn("Warning: the node gave no model: no model.", self.ok("start").stdout)

    def test_the_token_never_reaches_the_screen_or_a_command_line(self):
        self.linked()
        self.logs(MODEL_LINE)
        shown = self.ok("start").stdout + self.ok("status").stdout
        self.assertNotIn(self.TOKEN, shown)
        self.assertFalse([c for c in self.calls() if self.TOKEN in c])
        self.assertIn("Node:      http://sage-ai:8080 (token: set)", shown)


class Status(Mac):
    def test_before_a_start_status_says_how_to_start(self):
        self.assertIn("not set up here", self.ok("status").stdout)

    def test_status_names_upstream_s_node_while_the_settings_name_none(self):
        self.ok("start")
        out = self.ok("status").stdout
        self.assertIn("Address:   http://localhost:8799 (this machine only)", out)
        self.assertIn("Page:      answers", out)
        self.assertIn("Node:      https://app.tinker.click (token: none)", out)
        self.assertIn("Model:     none yet", out)


class StopAndInvite(Mac):
    def test_stop_stops_and_says_until_when(self):
        self.ok("start")
        self.assertIn("until: tinker-web start", self.ok("stop").stdout)
        self.assertIn("not running", self.ok("stop").stdout)

    def test_invites_need_a_running_server(self):
        self.assertIn("Start it first", self.fails("invite").stderr)
        self.ok("start")
        (self.state / "page-down").touch()
        self.assertIn("does not answer yet", self.fails("invite").stderr)

    def test_invite_codes_go_to_stdout_and_a_file_in_the_volume(self):
        self.ok("start")
        result = self.ok("invite", "--name", "rehearsal", "--count", "2")
        self.assertEqual(result.stdout.split(), ["CODE-ONE", "CODE-TWO"])
        (call,) = [c for c in self.calls() if " exec " in c]
        self.assertRegex(call, r" exec tinker-web tinker-cli invites new --name rehearsal --count 2 "
                               r"--to /data/invites-rehearsal-\d{8}T\d{6}Z\.txt$")
        self.assertRegex(result.stderr, r"/data/invites-rehearsal-\d{8}T\d{6}Z\.txt")

    def test_a_bad_name_or_count_is_refused_before_docker(self):
        self.ok("start")
        for args in (("--count", "0"), ("--count", "x"), ("--name", "a/b"), ("--name", "a b")):
            with self.subTest(args=args):
                self.fails("invite", *args)
        self.assertFalse([c for c in self.calls() if " exec " in c])


class Tunnel(Mac):
    def ssh_calls(self):
        return [c for c in self.calls() if c.startswith("ssh ")]

    def test_the_tunnel_forwards_a_local_port_to_the_host_s_loopback(self):
        out = self.ok("tunnel", "studio").stdout
        self.assertEqual(self.ssh_calls(), ["ssh -N -o ExitOnForwardFailure=yes -L 8799:127.0.0.1:8799 studio"])
        self.assertIn("open http://localhost:8799 here", out)

    def test_both_ports_may_move(self):
        self.ok("tunnel", "--port", "8800", "studio", "--remote-port", "8798")
        self.assertEqual(self.ssh_calls(), ["ssh -N -o ExitOnForwardFailure=yes -L 8800:127.0.0.1:8798 studio"])

    def test_no_host_or_an_option_for_ssh_is_refused(self):
        self.fails("tunnel")
        self.fails("tunnel", "-oProxyCommand=x")
        self.fails("tunnel", "a", "b")
        self.assertFalse(self.ssh_calls())

    def test_the_tunnel_needs_no_docker(self):
        self.ok("tunnel", "studio")
        self.assertFalse([c for c in self.calls() if c.startswith(("docker", "colima"))])


class Open(Mac):
    def test_open_prints_the_address_and_opens_it_on_this_screen(self):
        self.assertEqual(self.ok("open").stdout.strip(), "http://localhost:8799")
        self.assertIn("open http://localhost:8799", self.calls())

    def test_over_ssh_open_prints_the_address_and_the_tunnel(self):
        result = self.ok("open", SSH_CONNECTION="10.0.0.2 5 10.0.0.1 22")
        self.assertEqual(result.stdout.strip(), "http://localhost:8799")
        self.assertIn("tinker-web tunnel studio", result.stderr)
        self.assertFalse([c for c in self.calls() if c.startswith("open ")])


class FakeAiUi(ThreadingHTTPServer):
    """The slice of AI-UI's API link-ai-ui calls, with one admin. Every request is logged."""

    ADMIN = {"email": "admin@example.invalid", "password": "admin-pass-31337"}

    def __init__(self):
        super().__init__(("127.0.0.1", 0), AiUiHandler)
        self.route_on = True
        self.config = {"ENABLE_API_KEY": True, "ENABLE_API_KEY_ENDPOINT_RESTRICTIONS": False,
                       "API_KEY_ALLOWED_ENDPOINTS": ""}
        self.users = []
        self.served = ["base-a", "base-b"]
        self.presets = {}
        self.base_url = "http://sage-ai:8080/api"
        self.requests = []

    def key_owner(self, auth):
        return next((u for u in self.users if u["api_key"] and auth == "Bearer " + u["api_key"]), None)


class AiUiHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.route(None)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.route(json.loads(self.rfile.read(length)) if length else None)

    def route(self, body):
        ai, auth = self.server, self.headers.get("Authorization", "")
        path, _, query = self.path.partition("?")
        args = dict(urllib.parse.parse_qsl(query))
        ai.requests.append((self.command, path, args, auth, body))
        admin = auth == "Bearer jwt-admin"
        match (self.command, path):
            case ("GET", "/v1/model-login"):
                if not ai.route_on:
                    return self.answer(404, {"detail": "Not Found"})
                owner = ai.key_owner(auth)
                if not owner:
                    return self.answer(401, {"detail": "Not authenticated"})
                return self.answer(200, {"name": "Sage.is AI-UI", "provider": "custom", "base_url": ai.base_url,
                                         "api_key": owner["api_key"], "model": "tinker-build"})
            case ("POST", "/api/v1/auths/signin"):
                if body != FakeAiUi.ADMIN:
                    return self.answer(400, {"detail": "The email or password provided is incorrect."})
                return self.answer(200, {"token": "jwt-admin", "id": "admin-1", "role": "admin",
                                         "email": body["email"]})
            case ("GET", "/api/v1/auths/admin/config") if admin:
                return self.answer(200, ai.config)
            case ("GET", "/api/v1/users/") if admin:
                return self.answer(200, {"users": [u for u in ai.users if args["query"] in u["email"]],
                                         "total": len(ai.users)})
            case ("POST", "/api/v1/auths/add") if admin:
                if any(u["email"] == body["email"] for u in ai.users):
                    return self.answer(400, {"detail": "EMAIL_TAKEN"})
                user = {"id": "node-1", "email": body["email"], "role": body.get("role", "pending"),
                        "api_key": None, "password": body["password"]}
                ai.users.append(user)
                return self.answer(200, {"token": "jwt-node", "id": user["id"], "email": user["email"],
                                         "role": user["role"]})
            case ("POST", "/api/v1/auths/api_key") if auth == "Bearer jwt-node":
                ai.users[-1]["api_key"] = "sk-made-0123456789abcdef"
                return self.answer(200, {"api_key": ai.users[-1]["api_key"]})
            case ("GET", "/api/v1/models/model") if admin:
                if args["id"] not in ai.presets:
                    return self.answer(401, {"detail": "NOT_FOUND"})
                return self.answer(200, ai.presets[args["id"]])
            case ("GET", "/api/models") if admin:
                return self.answer(200, {"data": [{"id": m} for m in ai.served]})
            case ("POST", "/api/v1/models/create") if admin:
                ai.presets[body["id"]] = body
                return self.answer(200, body)
        self.answer(401, {"detail": "Not authenticated"})


class LinkAiUi(Mac):
    KEY = "sk-made-0123456789abcdef"

    def setUp(self):
        super().setUp()
        self.ai = FakeAiUi()
        threading.Thread(target=self.ai.serve_forever, daemon=True).start()
        self.addCleanup(self.ai.server_close)
        self.addCleanup(self.ai.shutdown)
        self.url = f"http://localhost:{self.ai.server_port}"
        self.admin = {"AI_UI_ADMIN_EMAIL": FakeAiUi.ADMIN["email"], "AI_UI_ADMIN_PASSWORD": FakeAiUi.ADMIN["password"]}

    def link(self, *args, **env):
        return self.run_cli("link-ai-ui", self.url, *args, **(self.admin | env))

    def linked(self, *args, **env):
        result = self.link(*args, **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def sent(self, method, path):
        return [r for r in self.ai.requests if r[:2] == (method, path)]

    def sage_secret(self):
        self.stub("sage-secret", """printf '%s\\n' "sage-secret $*" >> "$STATE/calls"
for var in TINKER_NODE_KEY TINKER_NODE_PASSWORD; do
  [[ -z "${!var:-}" ]] || printf '%s' "${!var}" | shasum -a 256 | cut -d' ' -f1 > "$STATE/$var"
done""")

    def test_a_fresh_ai_ui_gets_the_user_its_key_and_the_preset(self):
        self.linked("--base-model", "base-b")
        (add,) = self.sent("POST", "/api/v1/auths/add")
        self.assertEqual((add[4]["email"], add[4]["role"]), ("tinker-node@sage.is", "user"))
        self.assertGreaterEqual(len(add[4]["password"]), 24)
        preset = self.ai.presets["tinker-build"]
        self.assertEqual(preset["base_model_id"], "base-b")
        self.assertEqual(preset["params"], {})
        self.assertEqual(preset["access_control"]["read"]["user_ids"], ["node-1"])
        self.assertEqual(preset["access_control"]["write"]["user_ids"], [])
        self.assertEqual(self.settings()["TENKTS_WEB_NODE_TOKEN"], self.KEY)
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)

    def test_the_key_and_the_logins_never_reach_the_screen_or_a_command_line(self):
        result = self.linked("--base-model", "base-b")
        password = self.ai.users[0]["password"]
        for secret in (self.KEY, password, FakeAiUi.ADMIN["password"]):
            self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertFalse([c for c in self.calls() if secret in c])

    def test_localhost_becomes_the_host_as_a_container_sees_it(self):
        self.linked("--base-model", "base-b")
        self.assertEqual(self.settings()["TENKTS_WEB_NODE"], f"http://host.docker.internal:{self.ai.server_port}")

    def test_node_names_the_address_the_container_uses(self):
        self.linked("--base-model", "base-b", "--node", "http://sage-ai:8080/")
        self.assertEqual(self.settings()["TENKTS_WEB_NODE"], "http://sage-ai:8080")

    def test_a_second_run_keeps_the_key_and_the_preset(self):
        self.linked("--base-model", "base-b")
        out = self.linked().stderr
        self.assertEqual(len(self.sent("POST", "/api/v1/auths/add")), 1)
        self.assertEqual(len(self.sent("POST", "/api/v1/auths/api_key")), 1)
        self.assertEqual(len(self.sent("POST", "/api/v1/models/create")), 1)
        self.assertIn("Its key stays as it is", out)
        self.assertEqual(self.settings()["TENKTS_WEB_NODE_TOKEN"], self.KEY)

    def test_the_route_switched_off_says_which_setting_turns_it_on(self):
        self.ai.route_on = False
        result = self.link("--base-model", "base-b")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ENABLE_TINKER_NODE=true", result.stderr)
        self.assertFalse(self.sent("POST", "/api/v1/auths/signin"))
        self.assertFalse(self.env_file.exists())

    def test_without_a_base_model_a_missing_preset_lists_what_the_ai_ui_serves(self):
        result = self.link()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--base-model ID", result.stderr)
        self.assertIn("base-a, base-b", result.stderr)
        self.assertFalse(self.env_file.exists())
        self.assertIn("serves no model base-c", self.link("--base-model", "base-c").stderr)
        self.assertFalse(self.ai.presets)

    def test_a_preset_the_node_may_not_read_is_refused(self):
        self.ai.presets["tinker-build"] = {"id": "tinker-build", "access_control": {"read": {"user_ids": []}}}
        result = self.link()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("may not use it", result.stderr)

    def test_a_public_preset_is_used_as_it_is(self):
        self.ai.presets["tinker-build"] = {"id": "tinker-build", "access_control": None}
        self.linked()
        self.assertFalse(self.sent("POST", "/api/v1/models/create"))

    def test_api_keys_off_or_fenced_off_are_named(self):
        self.ai.config["ENABLE_API_KEY"] = False
        self.assertIn("API keys are off", self.link("--base-model", "base-b").stderr)
        self.ai.config |= {"ENABLE_API_KEY": True, "ENABLE_API_KEY_ENDPOINT_RESTRICTIONS": True,
                           "API_KEY_ALLOWED_ENDPOINTS": "/api/chat/completions"}
        result = self.link("--base-model", "base-b")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Add /v1/model-login to the allowed endpoints", result.stderr)
        self.assertFalse(self.sent("POST", "/api/v1/auths/add"))

    def test_a_wrong_admin_password_is_refused(self):
        result = self.link(AI_UI_ADMIN_PASSWORD="wrong")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("admin sign-in was refused", result.stderr)

    def test_without_a_terminal_the_login_must_come_from_the_environment(self):
        result = self.run_cli("link-ai-ui", self.url)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AI_UI_ADMIN_EMAIL and AI_UI_ADMIN_PASSWORD", result.stderr)
        self.assertFalse(self.ai.requests)

    def test_vault_stores_the_key_and_the_new_account_s_password_through_the_environment(self):
        self.sage_secret()
        self.linked("--base-model", "base-b", "--vault", "air-aiui-tinker-node")
        puts = [c for c in self.calls() if c.startswith("sage-secret ")]
        self.assertEqual(puts, [
            "sage-secret put air-aiui-tinker-node --from-env TINKER_NODE_KEY --username tinker-node@sage.is --replace",
            "sage-secret put air-aiui-tinker-node --field account_password --from-env TINKER_NODE_PASSWORD --replace"])
        digest = lambda s: hashlib.sha256(s.encode()).hexdigest()
        self.assertEqual((self.state / "TINKER_NODE_KEY").read_text().strip(), digest(self.KEY))
        self.assertEqual((self.state / "TINKER_NODE_PASSWORD").read_text().strip(), digest(self.ai.users[0]["password"]))

    def test_vault_without_sage_secret_is_refused_before_any_call(self):
        result = self.link("--base-model", "base-b", "--vault", "air-aiui-tinker-node")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("brew install sage-is/apps/sage-secret", result.stderr)
        self.assertFalse(self.ai.requests)

    def test_a_running_web_tinker_starts_again_to_read_its_node(self):
        self.assertIn("Start web Tinker to use it", self.linked("--base-model", "base-b").stdout)
        self.logs(MODEL_LINE)
        self.ok("start")
        before = len(self.docker_runs())
        self.assertIn("starting it again", self.linked().stdout)
        self.assertEqual(len(self.docker_runs()), before + 1)

    def test_the_start_after_a_link_keeps_the_image_the_container_runs(self):
        self.logs(MODEL_LINE)
        self.ok("start", "--tag", "0.0.9")
        self.linked("--base-model", "base-b")
        runs = self.docker_runs()
        self.assertEqual(len(runs), 2)
        self.assertTrue(runs[1].endswith("ghcr.io/sage-is/tinker-web:0.0.9"), runs[1])
        self.assertEqual(len([c for c in self.calls() if " pull " in c]), 1)

    def test_a_loopback_base_url_is_a_warning(self):
        self.ai.base_url = "http://localhost:8080/api"
        self.assertIn("Set TINKER_NODE_BASE_URL", self.linked("--base-model", "base-b").stdout)


class Formula(unittest.TestCase):
    def script(self):
        return TINKER_WEB.read_text()

    def test_version_names_the_image_of_the_same_tag(self):
        run = subprocess.run([TINKER_WEB, "version"], capture_output=True, text=True, check=True)
        self.assertEqual(run.stdout.strip(), "tinker-web 0.1.0 (image ghcr.io/sage-is/tinker-web:latest)")

    def test_the_formula_pins_the_image_of_its_own_version(self):
        formula = FORMULA.read_text()
        version = re.search(r'^  version "(.*)"$', formula, re.M).group(1)
        self.assertEqual(re.search(r'^VERSION="(.*)"$', self.script(), re.M).group(1), version)
        # The formula's inreplace: one line to replace, or the install keeps latest.
        self.assertEqual(self.script().count('DEFAULT_TAG="latest"'), 1)
        self.assertIn("""inreplace "tinker-web", 'DEFAULT_TAG="latest"'""", formula)

    def test_the_formula_installs_the_shared_lib_and_logs_in_nowhere(self):
        formula = FORMULA.read_text()
        self.assertIn('(libexec/"lib").install "lib/sage-runtime.sh"', formula)
        self.assertNotIn("docker login", formula + self.script())


if __name__ == "__main__":
    unittest.main()
