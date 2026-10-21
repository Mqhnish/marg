import json

import pytest

from marg.agent import write_guide, ask
from marg.llm import Client, LLMError
from marg.verify import check_citations


class ScriptedTransport:
    """Plays back canned API responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, headers, body):
        self.requests.append((url, json.loads(json.dumps(body))))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def responses_call(call_id, name, args):
    return {"status": "completed", "output": [
        {"type": "function_call", "id": "fc_" + call_id, "call_id": call_id, "name": name, "arguments": json.dumps(args)}
    ], "usage": {"input_tokens": 100, "output_tokens": 10}}


def responses_text(text, status="completed"):
    return {"status": status, "output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
    ], "usage": {"input_tokens": 200, "output_tokens": 50}}


GUIDE = "# Onboarding guide: demo\n## In one paragraph\nFlask app booted in `app.py:4-5`, routes in `routes.py:3`.\n"


def test_responses_loop_replays_history_statelessly(sample_repo):
    transport = ScriptedTransport([
        responses_call("c1", "read_file", {"path": "app.py"}),
        responses_call("c2", "read_file", {"path": "routes.py"}),
        responses_text(GUIDE),
    ])
    client = Client(api_key="test", transport=transport, sleep=lambda s: None)
    result = write_guide(client, sample_repo, max_steps=10)

    assert result.steps == 3 and result.tool_calls == 2
    assert set(result.files_read) == {"app.py", "routes.py"}
    assert result.citations.valid == ["app.py:4-5", "routes.py:3"]
    assert "2 verified" in result.markdown

    url, last = transport.requests[-1]
    assert url.endswith("/responses")
    assert last["store"] is False and last["model"] == "qwen-3.8-27b"
    assert last["input"][0]["role"] == "system"
    kinds = [i.get("type") or i.get("role") for i in last["input"]]
    assert kinds == ["system", "user", "function_call", "function_call_output", "function_call", "function_call_output"]
    outputs = [i for i in last["input"] if i.get("type") == "function_call_output"]
    assert outputs[0]["call_id"] == "c1" and "from flask import Flask" in outputs[0]["output"]
    assert "sk-abcdef" not in outputs[1]["output"]
    assert result.usage["input_tokens"] == 100 + 100 + 200


def test_chat_completions_mode(sample_repo):
    transport = ScriptedTransport([
        {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "search", "arguments": json.dumps({"pattern": "register"})}}
        ]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "It is in `routes.py:3`."}}]},
    ])
    client = Client(api_key="k", api_style="chat", transport=transport)
    result = ask(client, sample_repo, "where are routes registered?")
    url, body = transport.requests[-1]
    assert url.endswith("/chat/completions")
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1] == {"role": "tool", "tool_call_id": "t1", "content": body["messages"][-1]["content"]}
    assert "routes.py:3:" in body["messages"][-1]["content"]
    assert "routes.py:3" in result.markdown
    assert result.citations.unread == ["routes.py:3"]  # found via search, never read_file


def test_budget_exhaustion_forces_final_answer_without_tools(sample_repo):
    transport = ScriptedTransport([
        responses_call("a", "list_dir", {"path": "."}),
        responses_call("b", "list_dir", {"path": "."}),
        responses_text("final"),
    ])
    client = Client(api_key="k", transport=transport)
    result = write_guide(client, sample_repo, max_steps=2)
    _, last = transport.requests[-1]
    assert "tools" not in last
    assert "tool budget" in last["input"][-1]["content"]
    assert result.markdown.startswith("final")


def test_truncated_output_is_continued(sample_repo):
    transport = ScriptedTransport([
        responses_text("part one, ", status="incomplete"),
        responses_text("part two."),
    ])
    client = Client(api_key="k", transport=transport)
    result = ask(client, sample_repo, "q")
    assert result.markdown.strip() == "part one, part two."


def test_retries_on_503_then_succeeds(sample_repo):
    busy = LLMError("busy")
    busy.status = 503
    transport = ScriptedTransport([busy, busy, responses_text("ok")])
    sleeps = []
    client = Client(api_key="k", transport=transport, sleep=sleeps.append)
    assert ask(client, sample_repo, "q").markdown.strip() == "ok"
    assert sleeps == [2.0, 4.0]


def test_non_retryable_error_propagates(sample_repo):
    bad = LLMError("unauthorised")
    bad.status = 401
    client = Client(api_key="k", transport=ScriptedTransport([bad]))
    with pytest.raises(LLMError):
        ask(client, sample_repo, "q")


def test_missing_key_gives_helpful_error(monkeypatch):
    monkeypatch.delenv("BHARATCODE_API_KEY", raising=False)
    with pytest.raises(LLMError, match="bharatcode.ai/account"):
        Client()


def test_citation_checker_flags_fake_and_out_of_range(sample_repo):
    md = "See `app.py:2`, `app.py:99`, `nope/ghost.py:1` and `routes.py`."
    report = check_citations(md, sample_repo, {"app.py": 8})
    assert report.valid == ["app.py:2"]
    assert report.bad_lines == ["app.py:99"]
    assert report.missing == ["nope/ghost.py:1"]
    assert report.unread == ["routes.py"]


def test_cut_off_tool_call_is_never_run_and_budget_grows(sample_repo):
    cut_off = responses_call("half", "read_file", {"path": "app.py"})
    cut_off["status"] = "incomplete"
    transport = ScriptedTransport([
        cut_off,
        responses_call("c1", "read_file", {"path": "routes.py"}),
        responses_text(GUIDE),
    ])
    client = Client(api_key="k", transport=transport)
    result = write_guide(client, sample_repo, max_steps=10)

    assert result.tool_calls == 1
    assert set(result.files_read) == {"routes.py"}
    assert [body["max_output_tokens"] for _, body in transport.requests] == [16000, 32000, 32000]
    _, last = transport.requests[-1]
    assert "half" not in json.dumps(last["input"])


def test_chat_cut_off_tool_call_is_never_run(sample_repo):
    transport = ScriptedTransport([
        {"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t0", "type": "function", "function": {"name": "search", "arguments": "{\"pattern\": \"reg"}}
        ]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "No idea."}}]},
    ])
    client = Client(api_key="k", api_style="chat", transport=transport)
    result = ask(client, sample_repo, "q")
    assert result.tool_calls == 0
    assert [body["max_tokens"] for _, body in transport.requests] == [16000, 32000]
    assert result.markdown.strip() == "No idea."


def test_repeated_cut_offs_stop(sample_repo):
    transport = ScriptedTransport([responses_text("", status="incomplete")] * 4)
    client = Client(api_key="k", transport=transport)
    ask(client, sample_repo, "q")
    assert len(transport.requests) == 4


def test_timeouts_are_retried(sample_repo):
    slow = LLMError("no response")
    slow.retryable = True
    transport = ScriptedTransport([slow, responses_text("ok")])
    client = Client(api_key="k", transport=transport, sleep=lambda s: None)
    assert ask(client, sample_repo, "q").markdown.strip() == "ok"
