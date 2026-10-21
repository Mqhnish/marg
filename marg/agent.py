"""The explore-then-write agent loop."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from marg import prompts
from marg.llm import MAX_OUTPUT_TOKENS, Client, LLMError
from marg.mermaid import fix_mermaid
from marg.script import check_and_repair
from marg.repomap import build_repo_map, render_summary
from marg.tools import TOOL_SPECS, RepoTools
from marg.verify import CitationReport, check_citations, render_footer

Logger = Callable[[str], None]

START_OUTPUT_TOKENS = 16_000
MAX_CUTOFFS = 3
# The whole history is resent every step, so its size drives both context use and token cost.
# About 50k tokens; qwen-3.8-27b's window is 262k.
HISTORY_CHAR_BUDGET = 200_000
KEEP_RECENT_OUTPUTS = 6
STUB = "[output omitted to save context; call the tool again if you need it] "


@dataclass
class AgentResult:
    markdown: str
    steps: int
    tool_calls: int
    files_read: dict[str, int]
    citations: CitationReport
    usage: dict = field(default_factory=dict)


def _merge_usage(total: dict, usage: dict) -> None:
    for key, value in (usage or {}).items():
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


def compact_history(history: list[dict], budget: int = HISTORY_CHAR_BUDGET,
                    keep_recent: int = KEEP_RECENT_OUTPUTS) -> int:
    """Stub out the oldest tool outputs until the history fits the budget. Returns how many."""
    size = sum(len(json.dumps(item)) for item in history)
    outputs = [i for i in history if i.get("type") == "function_call_output" or i.get("role") == "tool"]
    stubbed = 0
    for item in outputs[:max(0, len(outputs) - keep_recent)]:
        if size <= budget:
            break
        key = "output" if "output" in item else "content"
        text = item[key]
        if text.startswith(STUB):
            continue
        last_line = text.rstrip().rsplit("\n", 1)[-1]
        stub = STUB + (last_line if last_line.startswith("[") else text[:80])
        size -= len(text) - len(stub)
        item[key] = stub
        stubbed += 1
    return stubbed


def run_agent(
    client: Client,
    root: str | Path,
    system: str,
    user_prompt: str,
    max_steps: int = 30,
    log: Logger = lambda _msg: None,
    footer: bool = True,
    max_output_tokens: int = START_OUTPUT_TOKENS,
    title_first: bool = False,
    lang: str = "en",
) -> AgentResult:
    tools = RepoTools(root)
    history: list[dict] = [{"role": "user", "content": user_prompt}]
    usage: dict = {}
    tool_calls = 0
    steps = 0
    answer_parts: list[str] = []
    budget = max_output_tokens
    cutoffs = 0
    nudged = False

    while True:
        steps += 1
        out_of_budget = steps > max_steps
        if out_of_budget and not nudged:
            history.append({"role": "user", "content": prompts.FINAL_NUDGE})
            nudged = True
        if compact_history(history, HISTORY_CHAR_BUDGET, KEEP_RECENT_OUTPUTS):
            log("  history is large, older tool output was shortened")
        try:
            turn = client.step(system, history, None if out_of_budget else TOOL_SPECS, budget)
        except LLMError as exc:
            if out_of_budget or tool_calls == 0:
                raise
            # Don't throw away what was already read: go straight to the write step.
            log(f"  API error, writing from what was read so far: {exc}")
            max_steps = steps
            continue
        _merge_usage(usage, turn.usage)

        if turn.truncated and cutoffs < MAX_CUTOFFS:
            cutoffs += 1
            # The output limit includes reasoning, and the same budget tends to stop in the
            # same place, so every cut-off turn doubles it (up to the API maximum).
            budget = min(budget * 2, MAX_OUTPUT_TOKENS)
            if turn.tool_calls or not turn.text:
                # Tool calls in a cut-off turn may be incomplete, so drop the turn and ask again.
                log("  output hit the length limit, retrying with a larger budget")
                continue
            log("  output hit the length limit, asking the model to continue")
            history.extend(turn.raw_items)
            answer_parts.append(turn.text)
            history.append({"role": "user", "content": prompts.CONTINUE_NUDGE})
            continue

        history.extend(turn.raw_items)
        if turn.tool_calls and not out_of_budget and not turn.truncated:
            results = []
            for call in turn.tool_calls:
                tool_calls += 1
                log(f"  [{steps:>2}] {call.name}({call.arguments})")
                results.append((call.id, tools.call(call.name, call.arguments)))
            history.extend(client.tool_results(results))
            continue

        answer_parts.append(turn.text)
        break

    markdown = "".join(answer_parts).strip() + "\n"
    if title_first:
        # Models sometimes narrate ("I have enough to write the guide.") before the title.
        title = re.search(r"^# ", markdown, re.M)
        if title:
            markdown = markdown[title.start():]
    markdown, repairs = fix_mermaid(markdown)
    if repairs:
        log(f"  repaired {repairs} Mermaid label(s) so the diagram renders")
    script_note = ""
    if lang != "en":
        markdown, script = check_and_repair(client, markdown, lang, log)
        _merge_usage(usage, script.usage)
        script_note = script.note()
        log("  " + script_note)
    report = check_citations(markdown, root, tools.files_read, tools.ranges_read)
    if footer:
        markdown += render_footer(report, steps, client.model, script_note)
    return AgentResult(markdown, steps, tool_calls, tools.files_read, report, usage)


def write_guide(client: Client, root: str | Path, max_steps: int = 30, log: Logger = lambda _m: None,
                lang: str = "en") -> AgentResult:
    summary = render_summary(build_repo_map(root))
    system = prompts.GUIDE_SYSTEM + prompts.language_rules(lang)
    return run_agent(client, root, system, prompts.guide_user_prompt(summary), max_steps, log, title_first=True,
                     lang=lang)


def ask(client: Client, root: str | Path, question: str, max_steps: int = 15, log: Logger = lambda _m: None,
        lang: str = "en") -> AgentResult:
    summary = render_summary(build_repo_map(root))
    system = prompts.ASK_SYSTEM + prompts.language_rules(lang, guide=False)
    return run_agent(client, root, system, prompts.ask_user_prompt(summary, question), max_steps, log, footer=False,
                     lang=lang)
