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
            streamed = isinstance(payload, str)  # a str is sent as-is, as a server-sent event stream
            data = payload.encode() if streamed else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream" if streamed else "application/json")
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
        headers = {"Content-Type": "text/html"}

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


def test_request_past_the_deadline_is_abandoned_and_retryable(monkeypatch):
    import time

    import pytest

    from marg.llm import LLMError, http_transport

    monkeypatch.setattr("marg.llm._post_json", lambda *a: time.sleep(5))
    started = time.monotonic()
    with pytest.raises(LLMError, match="no complete response") as info:
        http_transport("http://example.invalid/v1/responses", {}, {}, deadline=0.2)
    assert time.monotonic() - started < 2
    assert info.value.retryable is True


def test_deadline_wrapper_passes_results_and_errors_through(monkeypatch):
    import pytest

    from marg.llm import LLMError, http_transport

    monkeypatch.setattr("marg.llm._post_json", lambda *a: {"ok": True})
    assert http_transport("http://x/v1/responses", {}, {}) == {"ok": True}

    def unauthorised(*a):
        err = LLMError("HTTP 401")
        err.status = 401
        raise err

    monkeypatch.setattr("marg.llm._post_json", unauthorised)
    with pytest.raises(LLMError) as info:
        http_transport("http://x/v1/responses", {}, {})
    assert info.value.status == 401


def test_guide_in_tamil_end_to_end(sample_repo, monkeypatch, tmp_path, capsys):
    script = [(200, {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": "# ஆன்போர்டிங் வழிகாட்டி (Onboarding guide): demo\n"}]}]})]
    seen = []
    server = make_server(script, seen)
    monkeypatch.setenv("BHARATCODE_API_KEY", "bc_live_testkey123")
    out = tmp_path / "GUIDE.ta.md"
    try:
        code = main(["guide", str(sample_repo), "-o", str(out), "--lang", "Tamil",
                     "--base-url", f"http://127.0.0.1:{server.server_port}/api/model/v1"])
    finally:
        server.shutdown()
    assert code == 0
    assert "Tamil (தமிழ்)" in seen[0][2]["input"][0]["content"]
    assert "writing in Tamil" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8").startswith("# ஆன்போர்டிங் வழிகாட்டி")


def test_guide_is_written_as_utf8_whatever_the_locale(sample_repo, monkeypatch, tmp_path):
    # Windows defaults to a legacy code page, which can't encode Devanagari or Tamil.
    import pathlib

    real_write_text = pathlib.Path.write_text
    encodings = []

    def recording_write_text(self, data, encoding=None, *args, **kwargs):
        encodings.append(encoding)
        return real_write_text(self, data, encoding, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", recording_write_text)
    script = [(200, {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": "# गाइड (Onboarding guide): demo\n"}]}]})]
    server = make_server(script, [])
    monkeypatch.setenv("BHARATCODE_API_KEY", "bc_live_testkey123")
    out = tmp_path / "GUIDE.hi.md"
    try:
        code = main(["guide", str(sample_repo), "-o", str(out), "--lang", "hi", "-q",
                     "--base-url", f"http://127.0.0.1:{server.server_port}/api/model/v1"])
    finally:
        server.shutdown()
    assert code == 0
    assert encodings == ["utf-8"]
    assert out.read_bytes().decode("utf-8").startswith("# गाइड")



def sse(*events, queued=3):
    """A BharatCode-style event stream: queue keepalives, then the given events."""
    text = ": bharatcode queued\n\n" * queued
    for event in events:
        text += f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
    return text


def completed(output):
    return {"type": "response.completed", "response": {"status": "completed", "output": output,
                                                       "usage": {"input_tokens": 10, "output_tokens": 5}}}


def test_streamed_guide_end_to_end(sample_repo, monkeypatch, tmp_path):
    script = [
        (200, sse({"type": "response.created"}, completed([
            {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{\"path\": \"app.py\"}"}]))),
        (200, sse({"type": "response.failed", "response": {"error": {"message": "model busy"}}})),
        (200, sse({"type": "response.output_text.delta", "delta": "# Onboarding"}, queued=0)),  # ends early
        (200, sse({"type": "response.output_text.delta", "delta": "# Onboarding"}, completed([
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "# Onboarding guide: demo\nEntry point is `app.py:7-8`.\n"}]}]))),
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
    assert all(body["stream"] is True for _, _, body in seen)
    assert len(seen) == 4  # the failed and the cut-short streams were retried
    assert "Entry point is `app.py:7-8`." in out.read_text()
    assert "1 verified" in out.read_text()
