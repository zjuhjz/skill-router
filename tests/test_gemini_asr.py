import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENTRY = REPO / "skills" / "gemini-asr" / "entry.py"


def test_gemini_skill_parses_mocked_http_and_hides_the_key(tmp_path):
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            seen["path"] = self.path
            seen["key"] = self.headers.get("x-goog-api-key")
            seen["body"] = json.loads(body.decode("utf-8"))
            payload = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "language": "zh",
                                            "text": "英雄联盟",
                                            "segments": [{"start_ms": 0, "end_ms": 900, "text": "英雄联盟"}],
                                        },
                                        ensure_ascii=False,
                                    )
                                }
                            ]
                        }
                    }
                ]
            }
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    secret = "gemini-test-key-should-not-leak"
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = secret
    env["GEMINI_API_BASE"] = f"http://127.0.0.1:{server.server_port}/v1beta"
    env["PYTHONIOENCODING"] = "utf-8"
    request = {
        "skillcall": "0.1",
        "request_id": "r_test",
        "capability": "video.transcript@1",
        "input": {"file": str(media), "language": "zh"},
        "context": {"output_dir": str(tmp_path / "out"), "timeout_s": 30, "client_cwd": str(tmp_path)},
    }
    try:
        proc = subprocess.run(
            [sys.executable, str(ENTRY)],
            input=json.dumps(request).encode("utf-8"),
            capture_output=True,
            env=env,
            cwd=str(ENTRY.parent),
            check=False,
        )
    finally:
        server.shutdown()
    stdout = proc.stdout.decode("utf-8")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0, stderr + stdout
    assert secret not in stdout
    assert secret not in stderr
    payload = json.loads(stdout)
    assert payload["output"]["source"] == "asr"
    assert payload["output"]["text"] == "英雄联盟"
    assert seen["path"].endswith("/models/gemini-4-argon:generateContent")
    assert seen["key"] == secret
    assert "x-goog-api-key" not in json.dumps(seen["body"])
    assert seen["body"]["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "video/mp4"


def test_gemini_http_401_is_need_auth(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            raw = b'{"error":{"message":"bad key gemini-denied-key"}}'
            self.send_response(401)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x" * 64)
    secret = "gemini-denied-key"
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = secret
    env["GEMINI_API_BASE"] = f"http://127.0.0.1:{server.server_port}/v1beta"
    request = {
        "input": {"file": str(media)},
        "context": {"output_dir": str(tmp_path), "client_cwd": str(tmp_path), "timeout_s": 20},
    }
    try:
        proc = subprocess.run(
            [sys.executable, str(ENTRY)],
            input=json.dumps(request).encode("utf-8"),
            capture_output=True,
            env=env,
            cwd=str(ENTRY.parent),
            check=False,
        )
    finally:
        server.shutdown()
    payload = json.loads(proc.stdout.decode("utf-8"))
    assert payload["error"]["code"] == "NEED_AUTH"
    assert secret not in proc.stdout.decode("utf-8")
