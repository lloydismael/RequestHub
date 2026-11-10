import importlib
import json
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
import unittest
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


class ContainerCleanupTests(unittest.TestCase):
    def test_failed_start_removes_every_owned_container(self):
        bash = os.environ.get("TEST_BASH") or shutil.which("bash")
        with tempfile.TemporaryDirectory() as temporary:
            live = Path(temporary) / "live"
            live.write_text("")
            stub = r'''
            docker() {
              case "$1" in
                run|create)
                  local name= previous= arg
                  for arg in "$@"; do
                    if [ "$previous" = --name ]; then name=$arg; fi
                    previous=$arg
                  done
                  if [ -n "$name" ]; then echo "$name" >> "$LIVE_CONTAINERS"; fi
                  if [ "$1" = run ] && [[ "$name" == *-app ]]; then return 42; fi
                  ;;
                start) [[ "$2" != *-app ]] || return 42 ;;
                rm) grep -Fxv "$3" "$LIVE_CONTAINERS" > "$LIVE_CONTAINERS.tmp" || true
                    mv "$LIVE_CONTAINERS.tmp" "$LIVE_CONTAINERS" ;;
              esac
              return 0
            }
            export -f docker
            bash scripts/smoke-container.sh image:test aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
            '''
            result = subprocess.run([bash, "-c", stub], env={**os.environ, "LIVE_CONTAINERS": str(live)},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
            self.assertEqual(live.read_text(), "", "A failed start leaked an owned container")


class ContainerSmokeProbeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.probe = importlib.import_module("check_container_smoke")
        except ModuleNotFoundError:
            self.fail("Container smoke probe is not implemented")
        self.state = {"revision": "a" * 40, "ready": True, "static": True, "external": False}
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.headers.get("X-Forwarded-Proto") != "https":
                    self.send_response(301)
                    self.end_headers()
                    return
                routes = {
                    "/healthz/": b"ok",
                    "/release/": json.dumps({"version": "smoke", "revision": state["revision"]}).encode(),
                    "/readyz/": json.dumps({"ready": state["ready"]}).encode(),
                    "/accounts/login/": (b'<html><link rel="stylesheet" href="https://external.invalid/a.css"></html>'
                                         if state["external"] else b'<html><link rel="stylesheet" href="/static/style.css"></html>'),
                    "/static/style.css": b"body { color: black; }",
                }
                status = 200 if self.path in routes and (self.path != "/static/style.css" or state["static"]) else 404
                self.send_response(status)
                self.end_headers()
                self.wfile.write(routes.get(self.path, b""))

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def test_image_probes_verify_revision_database_login_and_static(self):
        self.probe.check(self.url, "a" * 40)

    def test_wrong_revision_cannot_pass(self):
        self.state["revision"] = "b" * 40
        with self.assertRaises(ValueError):
            self.probe.check(self.url, "a" * 40)

    def test_database_not_ready_cannot_pass(self):
        self.state["ready"] = False
        with self.assertRaises(ValueError):
            self.probe.check(self.url, "a" * 40)

    def test_missing_collected_static_cannot_pass(self):
        self.state["static"] = False
        with self.assertRaises(ValueError):
            self.probe.check(self.url, "a" * 40)

    def test_external_asset_does_not_satisfy_packaged_static_check(self):
        self.state["external"] = True
        with self.assertRaises(ValueError):
            self.probe.check(self.url, "a" * 40)


if __name__ == "__main__":
    unittest.main()
