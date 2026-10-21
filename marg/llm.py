"""Minimal client for the BharatCode model API (OpenAI-compatible).

Uses only the standard library so `pip install` stays dependency-free.
Supports both the Responses API (default, documented to support function
tools) and Chat Completions. Both are normalised into a `Turn`.

BharatCode's API is stateless: there is no previous_response_id, so the
caller replays the full history every request with store=false.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

DEFAULT_BASE_URL = "https://bharatcode.ai/api/model/v1"
DEFAULT_MODEL = "qwen-3.8-27b"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    truncated: bool = False
    raw_items: list[dict] = field(default_factory=list)  # responses mode: items to replay
    usage: dict = field(default_factory=dict)


Transport = Callable[[str, dict, dict], dict]  # (url, headers, body) -> json


def http_transport(url: str, headers: dict, body: dict, timeout: float = 180) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        request_id = exc.headers.get("x-request-id", "n/a") if exc.headers else "n/a"
        err = LLMError(f"HTTP {exc.code} from {url} (x-request-id: {request_id}): {detail}")
        err.status = exc.code  # type: ignore[attr-defined]
        raise err from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"could not reach {url}: {exc.reason}") from exc


class Client:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_style: str = "responses",
        transport: Transport | None = None,
        max_retries: int = 4,
        sleep: Callable[[float], None] | None = None,
    ):
        self.api_key = api_key or os.environ.get("BHARATCODE_API_KEY")
        self.base_url = (base_url or os.environ.get("BHARATCODE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("MARG_MODEL") or DEFAULT_MODEL
        if api_style not in {"responses", "chat"}:
            raise ValueError("api_style must be 'responses' or 'chat'")
        self.api_style = api_style
        self.transport = transport or http_transport
        self.max_retries = max_retries
        self.sleep = sleep or (lambda seconds: time.sleep(seconds))
        if not self.api_key and transport is None:
            raise LLMError(
                "BHARATCODE_API_KEY is not set. Get a key at https://bharatcode.ai/account "
                "and run: export BHARATCODE_API_KEY=bc_live_..."
            )

    def _post(self, path: str, body: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "marg/0.1",
        }
        url = f"{self.base_url}{path}"
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            try:
                return self.transport(url, headers, body)
            except LLMError as exc:
                status = getattr(exc, "status", None)
                if status in RETRY_STATUSES and attempt < self.max_retries:
                    self.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                raise
        raise LLMError("unreachable")

    # ---------- public ----------
    def step(self, system: str, history: list[dict], tools: list[dict] | None, max_output_tokens: int = 8000) -> Turn:
        if self.api_style == "responses":
            return self._step_responses(system, history, tools, max_output_tokens)
        return self._step_chat(system, history, tools, max_output_tokens)

    # ---------- Responses API ----------
    def _step_responses(self, system, history, tools, max_output_tokens) -> Turn:
        body = {
            "model": self.model,
            "input": [{"role": "system", "content": system}, *history],
            "store": False,
            "max_output_tokens": max_output_tokens,
        }
        if tools:
            body["tools"] = [{"type": "function", **t} for t in tools]
        data = self._post("/responses", body)
        texts, calls, replay = [], [], []
        for item in data.get("output", []):
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content", []):
                    if part.get("type") in {"output_text", "text"}:
                        texts.append(part.get("text", ""))
                replay.append({"role": "assistant", "content": "".join(
                    p.get("text", "") for p in item.get("content", []) if p.get("type") in {"output_text", "text"})})
            elif kind == "function_call":
                call = ToolCall(item.get("call_id") or item.get("id", ""), item["name"], item.get("arguments", "{}"))
                calls.append(call)
                replay.append({"type": "function_call", "call_id": call.id, "name": call.name, "arguments": call.arguments})
        if not texts and isinstance(data.get("output_text"), str):
            texts.append(data["output_text"])
        truncated = data.get("status") == "incomplete"
        return Turn("".join(texts), calls, truncated, replay, data.get("usage") or {})

    @staticmethod
    def responses_tool_results(results: list[tuple[str, str]]) -> list[dict]:
        return [{"type": "function_call_output", "call_id": cid, "output": out} for cid, out in results]

    # ---------- Chat Completions ----------
    def _step_chat(self, system, history, tools, max_output_tokens) -> Turn:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *history],
            "max_tokens": max_output_tokens,
        }
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        data = self._post("/chat/completions", body)
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:300]}") from exc
        message = choice.get("message", {})
        calls = [
            ToolCall(tc["id"], tc["function"]["name"], tc["function"].get("arguments", "{}"))
            for tc in message.get("tool_calls") or []
        ]
        replay_msg = {"role": "assistant", "content": message.get("content") or ""}
        if message.get("tool_calls"):
            replay_msg["tool_calls"] = message["tool_calls"]
        return Turn(message.get("content") or "", calls, choice.get("finish_reason") == "length",
                    [replay_msg], data.get("usage") or {})

    @staticmethod
    def chat_tool_results(results: list[tuple[str, str]]) -> list[dict]:
        return [{"role": "tool", "tool_call_id": cid, "content": out} for cid, out in results]

    def tool_results(self, results: list[tuple[str, str]]) -> list[dict]:
        if self.api_style == "responses":
            return self.responses_tool_results(results)
        return self.chat_tool_results(results)
