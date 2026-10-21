"""Repair the Mermaid mistakes models make most often, so diagrams render on GitHub.

Checked against Mermaid 11: parentheses in an unquoted edge or node label are a parse
error, and a literal \\n in a label is shown as text instead of a line break.
"""

from __future__ import annotations

import re

BLOCK = re.compile(r"(```mermaid\n)(.*?)(```)", re.S)
LITERAL_NEWLINE = re.compile(r"\\n")
# |label| on an edge, unquoted, containing brackets or parentheses
EDGE_LABEL = re.compile(r'\|([^|"\n]*[()\[\]{}][^|"\n]*)\|')
# id[label] with parentheses or braces inside; [( [[ [/ [\ open other node shapes, so they are left alone
NODE_LABEL = re.compile(r'(\b[A-Za-z_][\w-]*)\[(?![(\[/\\])([^\]"\n]*[(){}][^\]"\n]*)\]')

FIXES = [(LITERAL_NEWLINE, "<br/>"), (EDGE_LABEL, r'|"\1"|'), (NODE_LABEL, r'\1["\2"]')]


def fix_mermaid(markdown: str) -> tuple[str, int]:
    """Return the markdown with Mermaid labels repaired, and how many repairs were made."""
    repairs = 0

    def fix_block(match: re.Match) -> str:
        nonlocal repairs
        code = match.group(2)
        for pattern, replacement in FIXES:
            code, count = pattern.subn(replacement, code)
            repairs += count
        return match.group(1) + code + match.group(3)

    return BLOCK.sub(fix_block, markdown), repairs
