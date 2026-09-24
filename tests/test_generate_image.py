import base64
import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = load_module("generate_image_setup", SKILL_ROOT / "scripts/setup.py")
generate = load_module("generate_image", SKILL_ROOT / "scripts/generate.py")


class UpstreamHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.requests.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/redirect/models":
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/models")
            self.end_headers()
            return
        if self.path == "/unauthorized/models":
            self.send_response(401)
            response = {"error": "secret-value rejected"}
        elif self.path == "/empty/models":
            self.send_response(200)
            response = {"data": [{"id": "gpt-4o"}]}
        else:
            self.send_response(200)
            response = {"data": [
                {"id": "gpt-image-2"}, {"id": "gpt-4o"},
                {"id": "nano-banana-pro"}, {"id": "grok-imagine-1"},
                {"id": "seedream-4"}, {"id": "xgpt-image-evil"},
                {"id": "gpt-image-2"}, {"name": "seedream-no-id"},
            ]}
        self.send_response_only_headers(response)

    def send_response_only_headers(self, response):
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(("POST", self.path, self.headers.get("Authorization"), data))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        image_data = base64.b64encode(b"fake-image").decode()
        self.wfile.write(json.dumps({"data": [{"b64_json": image_data}]}).encode())


class GenerateImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), UpstreamHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        UpstreamHandler.requests.clear()

    def test_setup_discovers_filtered_models_and_stores_no_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary) / "settings"
            config_path = config_dir / "config.json"
            values = {"GENERATE_IMAGE_KEY": "secret-value"}
            with patch.dict(os.environ, values, clear=True), patch.object(setup, "CONFIG_DIR", config_dir), patch.object(setup, "CONFIG_PATH", config_path), patch.object(sys, "argv", ["setup.py", "--base-url", self.base + "/v1", "--model-id", "seedream-4"]), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup.main(), 0)
            stored = json.loads(config_path.read_text())
            self.assertEqual(stored["GENERATE_IMAGE_BASE_URL"], self.base + "/v1")
            self.assertEqual(stored["GENERATE_IMAGE_MODEL_ID"], "seedream-4")
            self.assertEqual(stored["GENERATE_IMAGE_MODE_LIST"], [
                "gpt-image-2", "grok-imagine-1", "nano-banana-pro", "seedream-4"
            ])
            self.assertNotIn("secret-value", config_path.read_text() + output.getvalue())
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertEqual(UpstreamHandler.requests[0], ("GET", "/v1/models", "Bearer secret-value"))

    def test_nonmatching_and_redirect_catalogs_fail_closed(self):
        for suffix, expected in (("empty", "no supported"), ("redirect", "HTTP 302"), ("unauthorized", "HTTP 401")):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(ValueError, expected) as failure:
                    setup.fetch_models(self.base + "/" + suffix, "secret-value")
                self.assertNotIn("secret-value", str(failure.exception))

    def test_environment_only_status_and_invalid_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            values = {
                "GENERATE_IMAGE_BASE_URL": self.base + "/v1",
                "GENERATE_IMAGE_KEY": "secret-value",
                "GENERATE_IMAGE_MODEL_ID": "gpt-image-2",
                "GENERATE_IMAGE_MODE_LIST": '["gpt-image-2", "gpt-4o"]',
            }
            with patch.dict(os.environ, values, clear=True), patch.object(setup, "CONFIG_PATH", Path(temporary) / "missing.json"), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup.status(), 0)
                self.assertNotIn("secret-value", output.getvalue())
                self.assertNotIn("gpt-4o", output.getvalue())
                os.environ["GENERATE_IMAGE_MODEL_ID"] = "gpt-4o"
                self.assertEqual(setup.status(), 2)

    def test_setup_rejects_stale_environment_model_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary) / "settings"
            config_path = config_dir / "config.json"
            values = {
                "GENERATE_IMAGE_KEY": "secret-value",
                "GENERATE_IMAGE_MODE_LIST": '["gpt-image-2"]',
            }
            with patch.dict(os.environ, values, clear=True), patch.object(setup, "CONFIG_DIR", config_dir), patch.object(setup, "CONFIG_PATH", config_path), patch.object(sys, "argv", ["setup.py", "--base-url", self.base + "/v1"]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(setup.main(), 1)
            self.assertIn("conflicts with /models", errors.getvalue())
            self.assertFalse(config_path.exists())

    def test_environment_defaults_and_request_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            values = {
                "GENERATE_IMAGE_BASE_URL": self.base + "/v1",
                "GENERATE_IMAGE_KEY": "secret-value",
                "GENERATE_IMAGE_MODEL_ID": "seedream-4",
                "GENERATE_IMAGE_MODE_LIST": json.dumps(["gpt-4o", "gpt-image-2", "nano-banana-pro", "seedream-4"]),
            }
            with patch.dict(os.environ, values, clear=True), patch.object(generate, "CONFIG_PATH", Path(temporary) / "missing.json"):
                for extra, expected in (([], "seedream-4"), (["--model", "nano-banana-pro"], "nano-banana-pro")):
                    with patch.object(sys, "argv", ["generate.py", "--prompt", "hello", "--output-dir", temporary, *extra]), contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(generate.main(), 0)
                    self.assertEqual(Path(json.loads(output.getvalue())["files"][0]).read_bytes(), b"fake-image")
                    self.assertEqual(UpstreamHandler.requests[-1][3]["model"], expected)
                submitted = len(UpstreamHandler.requests)
                with patch.object(sys, "argv", ["generate.py", "--prompt", "hello", "--model", "gpt-4o"]):
                    with self.assertRaisesRegex(SystemExit, "not in GENERATE_IMAGE_MODE_LIST"):
                        generate.main()
                self.assertEqual(len(UpstreamHandler.requests), submitted)


if __name__ == "__main__":
    unittest.main()
