"""Find and repair words written in the wrong script in non-English output.

In a real run, qwen-3.8-27b's Tamil slipped into Korean, Bengali and Chinese
mid-sentence. Prose is checked letter by letter against the target language's
script (plus Latin, for the English technical terms developers use). Code,
paths and citations are never checked or changed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from marg.llm import MAX_OUTPUT_TOKENS, Client, LLMError
from marg.prompts import LANGUAGES

# Unicode script of each language's writing system, as it appears in character names.
SCRIPTS = {
    "hi": "DEVANAGARI", "mr": "DEVANAGARI", "bn": "BENGALI", "as": "BENGALI", "ta": "TAMIL",
    "te": "TELUGU", "gu": "GUJARATI", "kn": "KANNADA", "ml": "MALAYALAM", "pa": "GURMUKHI",
    "or": "ORIYA", "ur": "ARABIC",
}
ALWAYS_ALLOWED = {"LATIN", "COMBINING"}
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
EDGE_PUNCTUATION = ".,;:!?()[]{}\"'*_|<>"

REPAIR_SYSTEM = """You fix language slips in a technical document written in {english} ({native}).
Some words are in the wrong script or language. Replace each listed word with the right {english} word,
or with the English technical term developers use. Change nothing else.
Keep every code block, every inline `code` span, every file path and every `path:line` citation exactly
as it is, character for character. Reply with the full corrected document only."""


@dataclass
class ScriptCheck:
    found: list[str] = field(default_factory=list)      # wrong-script words in the first draft
    remaining: list[str] = field(default_factory=list)  # still there after the repair attempt
    usage: dict = field(default_factory=dict)

    def note(self) -> str:
        if not self.found:
            return "Script check: no words in the wrong script."
        fixed = len(self.found) - len(self.remaining)
        text = f"Script check: {len(self.found)} word(s) in the wrong script, {fixed} repaired"
        if self.remaining:
            text += "; not repaired: " + ", ".join(f"`{w}`" for w in self.remaining[:10])
        return text + "."


def _script(ch: str) -> str | None:
    if not unicodedata.category(ch).startswith(("L", "M")):
        return None
    name = unicodedata.name(ch, "")
    return name.split(" ")[0] if name else None


def foreign_words(text: str, lang: str) -> list[str]:
    """Words in the prose that contain letters from a script the language doesn't use."""
    allowed = ALWAYS_ALLOWED | {SCRIPTS[lang]}
    words: list[str] = []
    for word in CODE.sub(" ", text).split():
        if any(_script(ch) not in allowed for ch in word if _script(ch)):
            word = word.strip(EDGE_PUNCTUATION)
            if word not in words:
                words.append(word)
    return words


def code_spans(text: str) -> list[str]:
    return sorted(CODE.findall(text))


def check_and_repair(client: Client, markdown: str, lang: str, log=lambda _m: None) -> tuple[str, ScriptCheck]:
    """Repair wrong-script words with one extra request; keep the original if the repair touches code."""
    check = ScriptCheck(found=foreign_words(markdown, lang))
    check.remaining = list(check.found)
    if not check.found:
        return markdown, check
    log(f"  {len(check.found)} word(s) in the wrong script, asking the model to fix them")
    english, native = LANGUAGES[lang]
    request = [{"role": "user", "content": "Words in the wrong script: " + ", ".join(check.found)
                + "\n\nDocument:\n\n" + markdown}]
    try:
        turn = client.step(REPAIR_SYSTEM.format(english=english, native=native), request, None, MAX_OUTPUT_TOKENS)
    except LLMError as exc:
        log(f"  script repair failed, keeping the draft: {exc}")
        return markdown, check
    check.usage = turn.usage
    repaired = turn.text.strip() + "\n"
    remaining = foreign_words(repaired, lang)
    safe = (
        not turn.truncated
        and code_spans(repaired) == code_spans(markdown)
        and 0.75 <= len(repaired) / len(markdown) <= 1.33
        and repaired.lstrip().startswith("#") == markdown.lstrip().startswith("#")
        and len(remaining) < len(check.found)
    )
    if not safe:
        log("  script repair changed code or did not help, keeping the draft")
        return markdown, check
    check.remaining = remaining
    return repaired, check
