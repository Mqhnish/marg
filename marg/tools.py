"""Read-only, sandboxed tools the agent can call.

Every path is resolved against the repo root and rejected if it escapes it.
Secret-looking files are never read, and common token formats are redacted
from file contents before anything leaves the machine.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from marg.repomap import BINARY_EXTS, is_secret_file, iter_files

MAX_READ_LINES = 400
MAX_READ_CHARS = 24_000
MAX_SEARCH_HITS = 60
MAX_LIST_ENTRIES = 200

REDACTIONS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"bc_live_[A-Za-z0-9_\-]{8,}"), "[REDACTED_BHARATCODE_KEY]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "[REDACTED_PRIVATE_KEY]"),
    (re.compile(
        r"""(?i)((?:api[_-]?key|secret|token|password|passwd)\s*[:=]\s*)(['"])[^'"\n]{8,}\2"""),
     r"\1\2[REDACTED]\2"),
]


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class ToolError(Exception):
    pass


class RepoTools:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.files_read: dict[str, int] = {}  # path -> line count, for citation checks
        self.ranges_read: dict[str, list[tuple[int, int]]] = {}  # path -> line ranges actually shown

    # ---------- helpers ----------
    def _resolve(self, rel: str) -> Path:
        rel = (rel or ".").strip().lstrip("/") or "."
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            raise ToolError(f"path '{rel}' is outside the repository")
        return target

    def _rel(self, path: Path) -> str:
        rel = path.relative_to(self.root).as_posix()
        return rel or "."

    # ---------- tools ----------
    def list_dir(self, path: str = ".") -> str:
        target = self._resolve(path)
        if not target.is_dir():
            raise ToolError(f"'{path}' is not a directory")
        prefix = self._rel(target)
        prefix = "" if prefix == "." else prefix + "/"
        entries = []
        for rel, abspath in iter_files(target):
            entries.append(prefix + rel)
            if len(entries) >= MAX_LIST_ENTRIES:
                entries.append(f"... truncated at {MAX_LIST_ENTRIES} entries")
                break
        return "\n".join(entries) or "(empty)"

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"'{path}' is not a file")
        if is_secret_file(target.name):
            raise ToolError(f"'{path}' looks like a secrets file and will not be read")
        if target.suffix.lower() in BINARY_EXTS:
            raise ToolError(f"'{path}' is a binary file")
        text = target.read_text(errors="replace")
        lines = text.splitlines()
        rel = self._rel(target)
        self.files_read[rel] = len(lines)
        start = max(1, int(start_line or 1))
        if lines and start > len(lines):
            raise ToolError(f"'{path}' has only {len(lines)} lines")
        end = int(end_line) if end_line else start + MAX_READ_LINES - 1
        end = min(end, len(lines), start + MAX_READ_LINES - 1)
        # Cut at a whole line so the footer and ranges_read say exactly which lines were shown.
        chunk, size = [], 0
        for n in range(start, end + 1):
            line = f"{n:>5}  {lines[n - 1]}"[:MAX_READ_CHARS]
            if chunk and size + len(line) > MAX_READ_CHARS:
                end = n - 1
                break
            chunk.append(line)
            size += len(line) + 1
        if end >= start:
            self.ranges_read.setdefault(rel, []).append((start, end))
        body = redact("\n".join(chunk))
        footer = f"\n[{rel}: lines {start}-{end} of {len(lines)}]"
        if end < len(lines):
            footer += " (call read_file again with start_line to see more)"
        return body + footer

    def search(self, pattern: str, path_glob: str | None = None) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regex: {exc}") from exc
        hits = []
        for rel, abspath in iter_files(self.root):
            if is_secret_file(abspath.name) or abspath.suffix.lower() in BINARY_EXTS:
                continue
            if path_glob and not Path(rel).match(path_glob):
                continue
            try:
                for n, line in enumerate(abspath.read_text(errors="ignore").splitlines(), 1):
                    if regex.search(line):
                        hits.append(f"{rel}:{n}: {redact(line.strip())[:200]}")
                        if len(hits) >= MAX_SEARCH_HITS:
                            return "\n".join(hits) + f"\n... stopped at {MAX_SEARCH_HITS} hits"
            except OSError:
                continue
        return "\n".join(hits) or "no matches"

    # ---------- dispatch ----------
    def call(self, name: str, arguments: str | dict) -> str:
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        except json.JSONDecodeError:
            return f"error: arguments were not valid JSON: {arguments!r}"
        fn = {"list_dir": self.list_dir, "read_file": self.read_file, "search": self.search}.get(name)
        if fn is None:
            return f"error: unknown tool '{name}'"
        try:
            return fn(**args)
        except TypeError as exc:
            return f"error: bad arguments for {name}: {exc}"
        except ToolError as exc:
            return f"error: {exc}"
        except (ValueError, AttributeError, OSError) as exc:
            # Bad argument values and unreadable files go back to the model instead of ending the run.
            return f"error: {name} failed: {exc}"


TOOL_SPECS = [
    {
        "name": "list_dir",
        "description": "List files under a directory in the repository (recursive, ignores build output and secrets).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory relative to the repo root. Use '.' for the root."}},
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": f"Read a text file with line numbers. Returns at most {MAX_READ_LINES} lines per call.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the repo root."},
                "start_line": {"type": "integer", "description": "First line to read (1-based). Default 1."},
                "end_line": {"type": "integer", "description": "Last line to read (inclusive). Optional."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search",
        "description": "Search all text files with a Python regular expression. Returns path:line: text for each hit.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression to search for."},
                "path_glob": {"type": "string", "description": "Optional glob to limit files, e.g. '*.py'."},
            },
            "required": ["pattern"],
        },
    },
]
