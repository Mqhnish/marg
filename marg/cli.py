"""Command line interface: `marg guide`, `marg ask`, `marg map`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from marg import __version__
from marg.llm import DEFAULT_MODEL, Client, LLMError
from marg.repomap import build_repo_map, render_summary


def _client(args) -> Client:
    return Client(model=args.model, api_style=args.api, base_url=args.base_url)


def _log(quiet: bool):
    return (lambda _m: None) if quiet else (lambda m: print(m, file=sys.stderr))


def cmd_map(args) -> int:
    print(render_summary(build_repo_map(args.path)))
    return 0


def cmd_guide(args) -> int:
    from marg.agent import write_guide

    log = _log(args.quiet)
    root = Path(args.path).resolve()
    log(f"Marg is exploring {root.name} with {args.model or DEFAULT_MODEL} ...")
    result = write_guide(_client(args), root, max_steps=args.max_steps, log=log)
    out = Path(args.output) if args.output else root / "ONBOARDING.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.markdown)
    r = result.citations
    log(
        f"Wrote {out} after {result.steps} steps and {result.tool_calls} tool calls. "
        f"Read {len(result.files_read)} files. Citations: {len(r.valid)} verified, "
        f"{len(r.unread)} unopened, {len(r.problems)} unverified."
    )
    if result.usage:
        log("Token usage: " + ", ".join(f"{k}={v}" for k, v in sorted(result.usage.items())))
    return 0


def cmd_ask(args) -> int:
    from marg.agent import ask

    result = ask(_client(args), Path(args.path).resolve(), args.question, max_steps=args.max_steps, log=_log(args.quiet))
    print(result.markdown)
    if result.citations.problems:
        print("Unverified citations: " + ", ".join(result.citations.problems), file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marg",
        description="Marg: point it at a repo and get a grounded onboarding guide, powered by the BharatCode model API.",
    )
    parser.add_argument("--version", action="version", version=f"marg {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_llm_flags(p):
        p.add_argument("--model", default=None, help=f"BharatCode model id (default {DEFAULT_MODEL}, or $MARG_MODEL)")
        p.add_argument("--api", choices=["responses", "chat"], default="responses",
                       help="which BharatCode endpoint to use (default: responses)")
        p.add_argument("--base-url", default=None, help="override the API base URL")
        p.add_argument("-q", "--quiet", action="store_true", help="do not print progress")

    p_map = sub.add_parser("map", help="print the local repo map (no API key needed)")
    p_map.add_argument("path", nargs="?", default=".")
    p_map.set_defaults(func=cmd_map)

    p_guide = sub.add_parser("guide", help="write ONBOARDING.md for a repository")
    p_guide.add_argument("path", nargs="?", default=".")
    p_guide.add_argument("-o", "--output", help="where to write the guide (default: <repo>/ONBOARDING.md)")
    p_guide.add_argument("--max-steps", type=int, default=30, help="tool-call budget (default 30)")
    add_llm_flags(p_guide)
    p_guide.set_defaults(func=cmd_guide)

    p_ask = sub.add_parser("ask", help="ask a question about a repository")
    p_ask.add_argument("question")
    p_ask.add_argument("path", nargs="?", default=".")
    p_ask.add_argument("--max-steps", type=int, default=15)
    add_llm_flags(p_ask)
    p_ask.set_defaults(func=cmd_ask)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (LLMError, NotADirectoryError) as exc:
        print(f"marg: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
