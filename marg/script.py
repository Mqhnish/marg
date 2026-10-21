"""Find and repair words written in the wrong script in non-English output.

In a real run, qwen-3.8-27b's Tamil slipped into Korean, Bengali and Chinese
mid-sentence. Prose is checked letter by letter against the target language's
script (plus Latin, for the English technical terms developers use). Code,
paths and citations are never checked or changed.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from marg.llm import Client, LLMError
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
You get words that contain letters from the wrong script, each with the sentence it appears in.
For each word, give it as it should be written in {english}, or as the English technical term developers use.
Keep any attached punctuation and any Latin or {english} letters that were already right.
Reply with JSON only: an object mapping each word exactly as given to its replacement."""
REPAIR_OUTPUT_TOKENS = 4000

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


def _in_context(text: str, word: str, width: int = 60) -> str:
    at = text.find(word)
    return " ".join(text[max(0, at - width):at + len(word) + width].split())


def _parse_map(reply: str) -> dict:
    start, end = reply.find("{"), reply.rfind("}")
    try:
        mapping = json.loads(reply[start:end + 1]) if start != -1 and end > start else {}
    except json.JSONDecodeError:
        return {}
    return mapping if isinstance(mapping, dict) else {}


def _replace_in_prose(text: str, fixes: dict) -> str:
    """Apply replacements outside code blocks and inline code only."""
    parts, last = [], 0
    for match in CODE.finditer(text):
        parts.append(_replace_all(text[last:match.start()], fixes))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_replace_all(text[last:], fixes))
    return "".join(parts)


def _replace_all(prose: str, fixes: dict) -> str:
    for wrong, right in sorted(fixes.items(), key=lambda kv: -len(kv[0])):
        prose = prose.replace(wrong, right)
    return prose


def check_and_repair(client: Client, markdown: str, lang: str, log=lambda _m: None) -> tuple[str, ScriptCheck]:
    """Ask for corrections to the wrong-script words only, and apply them to the prose."""
    check = ScriptCheck(found=foreign_words(markdown, lang))
    check.remaining = list(check.found)
    if not check.found:
        return markdown, check
    log(f"  {len(check.found)} word(s) in the wrong script, asking the model to fix them")
    english, native = LANGUAGES[lang]
    prose = CODE.sub(" ", markdown)
    listing = "\n".join(f"- {json.dumps(word, ensure_ascii=False)} in: {_in_context(prose, word)}"
                        for word in check.found)
    request = [{"role": "user", "content": "Words in the wrong script, with where they appear:\n" + listing}]
    try:
        turn = client.step(REPAIR_SYSTEM.format(english=english, native=native), request, None, REPAIR_OUTPUT_TOKENS)
    except LLMError as exc:
        log(f"  script repair failed, keeping the draft: {exc}")
        return markdown, check
    check.usage = turn.usage
    fixes = {
        wrong: right for wrong, right in _parse_map(turn.text).items()
        if wrong in check.found and isinstance(right, str) and right.strip()
        and not foreign_words(right, lang) and len(right) <= 3 * len(wrong) + 20
    }
    if not fixes:
        log("  script repair returned no usable fixes, keeping the draft")
        return markdown, check
    repaired = _replace_in_prose(markdown, fixes)
    if code_spans(repaired) != code_spans(markdown):  # cannot happen, but code must never change
        return markdown, check
    check.remaining = foreign_words(repaired, lang)
    return repaired, check
