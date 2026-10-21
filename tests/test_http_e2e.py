"""End to end: real CLI and real urllib transport against a local fake BharatCode server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from marg.cli import main


def make_server(script, seen):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers.get("Authorization"), body))
            status, payload = script.pop(0)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("x-request-id", "req_test")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_guide_end_to_end_over_http(sample_repo, monkeypatch, tmp_path):
    script = [
        (503, {"error": "busy"}),
        (200, {"status": "completed", "output": [{"type": "function_call", "call_id": "c1", "name": "read_file",
                                                   "arguments": "{\"path\": \"app.py\"}"}]}),
        (200, {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "# Onboarding guide: demo\nEntry point is `app.py:7-8`.\n"}]}]}),
    ]
    seen = []
    server = make_server(script, seen)
    monkeypatch.setenv("BHARATCODE_API_KEY", "bc_live_testkey123")
    monkeypatch.setattr("marg.llm.time.sleep", lambda s: None)
    out = tmp_path / "GUIDE.md"
    try:
        code = main(["guide", str(sample_repo), "-o", str(out), "-q",
                     "--base-url", f"http://127.0.0.1:{server.server_port}/api/model/v1"])
    finally:
        server.shutdown()
    assert code == 0
    assert [p for p, _, _ in seen] == ["/api/model/v1/responses"] * 3
    assert seen[0][1] == "Bearer bc_live_testkey123"
    text = out.read_text()
    assert "Entry point is `app.py:7-8`." in text
    assert "1 verified" in text


def test_http_error_reports_request_id(sample_repo, monkeypatch, capsys):
    seen = []
    server = make_server([(401, {"error": "bad key"})], seen)
    monkeypatch.setenv("BHARATCODE_API_KEY", "bc_live_wrong")
    try:
        code = main(["ask", "q", str(sample_repo), "-q",
                     "--base-url", f"http://127.0.0.1:{server.server_port}/api/model/v1"])
    finally:
        server.shutdown()
    assert code == 1
    err = capsys.readouterr().err
    assert "HTTP 401" in err and "req_test" in err


def test_read_timeout_becomes_retryable_llm_error(monkeypatch):
    import socket

    import pytest

    from marg.llm import LLMError, http_transport

    def boom(*args, **kwargs):
        raise socket.timeout("timed out")

    monkeypatch.setattr("marg.llm.urllib.request.urlopen", boom)
    with pytest.raises(LLMError) as info:
        http_transport("http://example.invalid/v1/responses", {}, {})
    assert info.value.retryable is True


def test_non_json_body_becomes_retryable_llm_error(monkeypatch):
    import pytest

    from marg.llm import LLMError, http_transport

    class HtmlPage:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"<html>Down for maintenance</html>"

    monkeypatch.setattr("marg.llm.urllib.request.urlopen", lambda *a, **k: HtmlPage())
    with pytest.raises(LLMError, match="not JSON") as info:
        http_transport("http://example.invalid/v1/responses", {}, {})
    assert info.value.retryable is True
