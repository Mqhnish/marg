"""The explore-then-write agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from marg import prompts
from marg.llm import Client
from marg.repomap import build_repo_map, render_summary
from marg.tools import TOOL_SPECS, RepoTools
from marg.verify import CitationReport, check_citations, render_footer

Logger = Callable[[str], None]


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


def run_agent(
    client: Client,
    root: str | Path,
    system: str,
    user_prompt: str,
    max_steps: int = 30,
    log: Logger = lambda _msg: None,
    footer: bool = True,
) -> AgentResult:
    tools = RepoTools(root)
    history: list[dict] = [{"role": "user", "content": user_prompt}]
    usage: dict = {}
    tool_calls = 0
    steps = 0
    answer_parts: list[str] = []

    while True:
        steps += 1
        out_of_budget = steps > max_steps
        if out_of_budget:
            history.append({"role": "user", "content": prompts.FINAL_NUDGE})
        turn = client.step(system, history, None if out_of_budget else TOOL_SPECS)
        _merge_usage(usage, turn.usage)
        history.extend(turn.raw_items)

        if turn.tool_calls and not out_of_budget:
            results = []
            for call in turn.tool_calls:
                tool_calls += 1
                log(f"  [{steps:>2}] {call.name}({call.arguments})")
                results.append((call.id, tools.call(call.name, call.arguments)))
            history.extend(client.tool_results(results))
            continue

        answer_parts.append(turn.text)
        if turn.truncated and len(answer_parts) < 4:
            log("  output hit the length limit, asking the model to continue")
            history.append({"role": "user", "content": prompts.CONTINUE_NUDGE})
            continue
        break

    markdown = "".join(answer_parts).strip() + "\n"
    report = check_citations(markdown, root, tools.files_read)
    if footer:
        markdown += render_footer(report, steps, client.model)
    return AgentResult(markdown, steps, tool_calls, tools.files_read, report, usage)


def write_guide(client: Client, root: str | Path, max_steps: int = 30, log: Logger = lambda _m: None) -> AgentResult:
    summary = render_summary(build_repo_map(root))
    return run_agent(client, root, prompts.GUIDE_SYSTEM, prompts.guide_user_prompt(summary), max_steps, log)


def ask(client: Client, root: str | Path, question: str, max_steps: int = 15, log: Logger = lambda _m: None) -> AgentResult:
    summary = render_summary(build_repo_map(root))
    return run_agent(client, root, prompts.ASK_SYSTEM, prompts.ask_user_prompt(summary, question),
                     max_steps, log, footer=False)
