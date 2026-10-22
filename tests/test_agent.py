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


def test_api_failure_mid_run_still_writes_from_what_was_read(sample_repo):
    down = LLMError("HTTP 400: bad request")
    down.status = 400
    transport = ScriptedTransport([
        responses_call("c1", "read_file", {"path": "app.py"}),
        down,
        responses_text(GUIDE),
    ])
    client = Client(api_key="k", transport=transport)
    result = write_guide(client, sample_repo, max_steps=10)
    _, last = transport.requests[-1]
    assert "tools" not in last
    assert "tool budget" in last["input"][-1]["content"]
    assert result.markdown.startswith("# Onboarding guide: demo")


def test_api_failure_before_any_reading_is_raised(sample_repo):
    down = LLMError("HTTP 400")
    down.status = 400
    client = Client(api_key="k", transport=ScriptedTransport([down]))
    with pytest.raises(LLMError):
        write_guide(client, sample_repo)


def test_empty_assistant_message_is_not_replayed(sample_repo):
    turn = responses_call("c1", "read_file", {"path": "app.py"})
    turn["output"].insert(0, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": ""}]})
    transport = ScriptedTransport([turn, responses_text("done")])
    ask(Client(api_key="k", transport=transport), sample_repo, "q")
    _, last = transport.requests[-1]
    assert not [i for i in last["input"] if i.get("role") == "assistant"]


def test_compact_history_stubs_oldest_outputs_first():
    from marg.agent import STUB, compact_history

    big = "x" * 1000 + "\n[app.py: lines 1-400 of 900]"
    history = [{"role": "user", "content": "map"}]
    for n in range(5):
        history.append({"type": "function_call", "call_id": f"c{n}", "name": "read_file", "arguments": "{}"})
        history.append({"type": "function_call_output", "call_id": f"c{n}", "output": big})

    assert compact_history(history, budget=10**6) == 0
    stubbed = compact_history(history, budget=3000, keep_recent=2)
    outputs = [i["output"] for i in history if i.get("type") == "function_call_output"]
    assert stubbed == 3
    assert all(o == STUB + "[app.py: lines 1-400 of 900]" for o in outputs[:3])
    assert outputs[3:] == [big, big]
    assert [i["call_id"] for i in history if i.get("type") == "function_call_output"] == ["c0", "c1", "c2", "c3", "c4"]


def test_long_runs_resend_shortened_history(sample_repo, monkeypatch):
    monkeypatch.setattr("marg.agent.HISTORY_CHAR_BUDGET", 2000)
    monkeypatch.setattr("marg.agent.KEEP_RECENT_OUTPUTS", 1)
    transport = ScriptedTransport(
        [responses_call(f"c{n}", "read_file", {"path": "app.py"}) for n in range(4)] + [responses_text(GUIDE)]
    )
    write_guide(Client(api_key="k", transport=transport), sample_repo, max_steps=10)
    _, last = transport.requests[-1]
    outputs = [i["output"] for i in last["input"] if i.get("type") == "function_call_output"]
    assert outputs[0].startswith("[output omitted") and "[app.py: lines 1-8 of 8]" in outputs[0]
    assert "from flask import Flask" in outputs[-1]


def test_citations_into_lines_never_shown_are_not_verified(sample_repo):
    md = "See `app.py:2`, `app.py:5`, `app.py:2-5` and `app.py`."
    report = check_citations(md, sample_repo, {"app.py": 8}, {"app.py": [(1, 3)]})
    assert report.valid == ["app.py:2", "app.py"]
    assert report.unread == ["app.py:5", "app.py:2-5"]


def test_guide_drops_narration_before_the_title(sample_repo):
    transport = ScriptedTransport([responses_text("I have enough to write the guide.\n\n" + GUIDE)])
    result = write_guide(Client(api_key="k", transport=transport), sample_repo)
    assert result.markdown.startswith("# Onboarding guide: demo")


def test_system_paths_are_not_counted_as_citations(sample_repo):
    report = check_citations("It falls back to `/etc/ssl/cert.pem`. Entry: `/app.py:4`.", sample_repo, {"app.py": 8})
    assert report.problems == []
    assert report.valid == ["/app.py:4"]


def test_find_language_accepts_codes_english_and_native_names():
    from marg.prompts import find_language

    assert find_language("hi") == find_language("Hindi") == find_language(" HINDI ") == find_language("हिन्दी") == "hi"
    assert find_language("Tamil") == find_language("தமிழ்") == "ta"
    with pytest.raises(ValueError, match="Choose from"):
        find_language("klingon")


def test_guide_in_hindi_keeps_code_and_citations_untranslated(sample_repo):
    transport = ScriptedTransport([responses_text("# ऑनबोर्डिंग गाइड (Onboarding guide): demo\nएंट्री पॉइंट `app.py:4-5` में है।\n")])
    result = write_guide(Client(api_key="k", transport=transport), sample_repo, lang="hi")
    _, body = transport.requests[0]
    system = body["input"][0]["content"]
    assert "Hindi (हिन्दी)" in system
    assert "Never translate or transliterate code" in system
    assert "(Run it locally)" in system
    assert result.markdown.startswith("# ऑनबोर्डिंग गाइड")
    assert result.citations.unread == ["app.py:4-5"]  # parsed out of Devanagari prose; never read, so not verified


def test_english_and_ask_prompts(sample_repo):
    from marg import prompts

    assert prompts.language_rules("en") == ""
    rules = prompts.language_rules("ta", guide=False)
    assert "your answer in Tamil (தமிழ்)" in rules and "heading" not in rules


def test_citations_inside_indian_language_prose_are_checked(sample_repo):
    report = check_citations("राउट `routes.py:3` में रजिस्टर होते हैं, और ऐप `app.py:4-5` में बनता है।", sample_repo,
                             {"app.py": 8, "routes.py": 6}, {"app.py": [(1, 8)], "routes.py": [(1, 6)]})
    assert report.valid == ["routes.py:3", "app.py:4-5"]


def test_retries_and_long_waits_are_logged(sample_repo, monkeypatch):
    import time

    monkeypatch.setattr("marg.llm.WAIT_LOG_EVERY", 0.05)
    busy = LLMError("HTTP 503: model busy")
    busy.status = 503

    def slow_then_ok(url, headers, body, _script=[busy]):
        if _script:
            raise _script.pop()
        time.sleep(0.3)
        return responses_text("ok")

    logs = []
    client = Client(api_key="k", transport=slow_then_ok, sleep=lambda s: None, log=logs.append)
    assert ask(client, sample_repo, "q").markdown.strip() == "ok"
    assert "  HTTP 503: model busy; retrying in 2s (1/6)" in logs
    assert any(line.startswith("  still waiting for the model") for line in logs)


def test_guides_are_saved_with_repaired_diagrams(sample_repo):
    transport = ScriptedTransport([responses_text("# Onboarding guide: demo\n```mermaid\nflowchart LR\n  A -->|next()| B\n```\n")])
    result = write_guide(Client(api_key="k", transport=transport), sample_repo)
    assert 'A -->|"next()"| B' in result.markdown


TAMIL_DRAFT = "# வழிகாட்டி (Onboarding guide): demo\nஎன்ட்ரி `app.py:4-5` இல் உள்ளது, budget用完 ஆனால் repo-வ을 பார்.\n"


def test_wrong_script_words_are_repaired_and_reported(sample_repo):
    fixes = json.dumps({"1": "budget முடிந்தால்", "2": "repo-வை"}, ensure_ascii=False)
    transport = ScriptedTransport([responses_call("c1", "read_file", {"path": "app.py"}),
                                   responses_text(TAMIL_DRAFT), responses_text("```json\n" + fixes + "\n```")])
    result = write_guide(Client(api_key="k", transport=transport), sample_repo, lang="ta")
    _, repair = transport.requests[-1]
    assert "wrong script" in repair["input"][0]["content"] and "tools" not in repair
    assert '1. "budget用完" in:' in repair["input"][1]["content"] and '2. "repo-வ을" in:' in repair["input"][1]["content"]
    assert repair["max_output_tokens"] == 2000 and repair["reasoning"] == {"effort": "none"}
    _, guide_turn = transport.requests[0]
    assert "reasoning" not in guide_turn  # the guide itself keeps the model's reasoning
    assert "budget முடிந்தால் ஆனால் repo-வை பார்" in result.markdown
    assert "Script check: 2 word(s) in the wrong script, 2 repaired." in result.markdown
    assert result.citations.valid == ["app.py:4-5"]


def test_unusable_repairs_are_ignored(sample_repo):
    # one fix still has a wrong-script letter, one is for a number that wasn't in the list
    fixes = json.dumps({"1": "budget用", "2": "repo-வை", "9": "செயலி", "x": "செயலி"}, ensure_ascii=False)
    transport = ScriptedTransport([responses_text(TAMIL_DRAFT), responses_text(fixes)])
    result = write_guide(Client(api_key="k", transport=transport), sample_repo, lang="ta")
    assert "budget用完" in result.markdown and "repo-வை" in result.markdown
    assert "`app.py:4-5`" in result.markdown  # code never touched
    assert "1 repaired; not repaired: `budget用完`." in result.markdown


def test_repair_reply_that_is_not_json_keeps_the_draft(sample_repo):
    transport = ScriptedTransport([responses_text(TAMIL_DRAFT), responses_text("Sorry, I can't help with that.")])
    result = write_guide(Client(api_key="k", transport=transport), sample_repo, lang="ta")
    assert "0 repaired; not repaired: `budget用完`, `repo-வ을`." in result.markdown



def test_clean_drafts_and_english_need_no_repair_request(sample_repo):
    clean = "# வழிகாட்டி (Onboarding guide): demo\nஎன்ட்ரி `app.py:4-5` இல் உள்ளது.\n"
    transport = ScriptedTransport([responses_text(clean)])
    result = write_guide(Client(api_key="k", transport=transport), sample_repo, lang="ta")
    assert len(transport.requests) == 1
    assert "Script check: no words in the wrong script." in result.markdown
    english = ScriptedTransport([responses_text(GUIDE)])
    assert "Script check" not in write_guide(Client(api_key="k", transport=english), sample_repo).markdown
    assert len(english.requests) == 1


def test_chat_style_sends_reasoning_effort(sample_repo):
    transport = ScriptedTransport([{"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]}])
    Client(api_key="k", api_style="chat", transport=transport).step("s", [{"role": "user", "content": "q"}], None, 100,
                                                                     reasoning="none")
    _, body = transport.requests[0]
    assert body["reasoning_effort"] == "none" and "stream" not in body


def test_bare_and_multi_range_citations_are_checked(sample_repo):
    md = ("Entry in (app.py:4-5) and `app.py:1,4-5`; see routes.py:3, 99 and lib/ghost.py:2. "
          "Not citations: 127.0.0.1:8080, version 1.2:3, `/etc/hosts:1` and http://example.com/a.js:12.")
    report = check_citations(md, sample_repo, {"app.py": 8, "routes.py": 6}, {"app.py": [(1, 8)], "routes.py": [(1, 6)]})
    assert report.valid == ["app.py:4-5", "app.py:1,4-5"]
    assert report.bad_lines == ["routes.py:3, 99"]  # line 99 doesn't exist
    assert report.missing == ["lib/ghost.py:2"]
    assert report.unread == []
