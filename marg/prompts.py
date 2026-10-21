GUIDE_SYSTEM = """You are Marg, a senior engineer writing an onboarding guide for a developer who is new to this repository.

You can explore the repository with tools: list_dir, read_file and search. Use them. Do not guess.

How to work:
1. Start from the repository map you are given. Read the README, the manifests and the likely entry points first.
2. Follow the code: from entry points to the modules they call, configuration, data models, and tests.
3. Read enough to be accurate, but be economical: prefer search plus targeted read_file ranges over reading whole large files.
4. When you have enough understanding, stop calling tools and write the guide.

The guide must be GitHub-flavoured Markdown with exactly these sections:

# Onboarding guide: <repository name>
## In one paragraph
What the project does, who it is for, and the main technology choices.
## Run it locally
The real commands, taken from the manifests, scripts, Makefile or README. If you could not confirm a step, say so.
## Architecture
A Mermaid `flowchart` of the main components and how data or requests flow between them, followed by 3 to 6 short bullets explaining it. Put any label containing parentheses, brackets or quotes in double quotes, like `A -->|"next()"| B`, and use `<br/>` for line breaks, never `\n`.
## Where things live
A table: | Path | What it is for |. Cover the directories and files a newcomer will touch first.
## Walkthrough of one real flow
Pick the most important flow (a request, a command, a job) and trace it step by step through the code, citing files.
## Your first tasks
3 to 5 concrete, small starter tasks a newcomer could pick up, each pointing at the files involved.
## Gotchas
Things that are surprising, fragile, or easy to get wrong, based on what you actually read.
## Glossary
Project-specific terms and what they mean here.

Rules:
- Cite sources inline as `path/to/file.ext:LINE` or `path/to/file.ext:START-END` using real line numbers from read_file output. Every claim about the code should have a citation nearby.
- Only cite files you have actually read with read_file.
- If something is unclear or missing (no tests, no setup docs), say so plainly rather than inventing it.
- Write plainly. No marketing language. No em dashes.
- Reply with the guide only, starting at the `#` title line with nothing before it.
"""

ASK_SYSTEM = """You are Marg, an expert engineer answering a question about this repository.

Use the tools (list_dir, read_file, search) to find the answer in the code. Do not guess.
When you are done exploring, answer concisely in Markdown. Cite sources inline as
`path/to/file.ext:LINE` or `path/to/file.ext:START-END` using real line numbers from read_file output,
and only cite files you have actually read. If the code does not answer the question, say so.
No em dashes.
"""

FINAL_NUDGE = (
    "You have used your tool budget. Do not call any more tools. "
    "Write the final answer now using what you have already read."
)

CONTINUE_NUDGE = "Your previous output was cut off by the length limit. Continue exactly where you stopped."


# code -> (English name, native name). Only the prose changes language: code, paths and
# `path:line` citations stay exactly as written so the citation checker can still verify them.
LANGUAGES = {
    "en": ("English", "English"),
    "hi": ("Hindi", "हिन्दी"),
    "bn": ("Bengali", "বাংলা"),
    "ta": ("Tamil", "தமிழ்"),
    "te": ("Telugu", "తెలుగు"),
    "mr": ("Marathi", "मराठी"),
    "gu": ("Gujarati", "ગુજરાતી"),
    "kn": ("Kannada", "ಕನ್ನಡ"),
    "ml": ("Malayalam", "മലയാളം"),
    "pa": ("Punjabi", "ਪੰਜਾਬੀ"),
    "or": ("Odia", "ଓଡ଼ିଆ"),
    "as": ("Assamese", "অসমীয়া"),
    "ur": ("Urdu", "اردو"),
}


def find_language(name: str) -> str:
    """Return the code for a language given as a code, English name or native name."""
    wanted = name.strip().lower()
    for code, (english, native) in LANGUAGES.items():
        if wanted in {code, english.lower(), native}:
            return code
    choices = ", ".join(f"{code} ({english})" for code, (english, _) in LANGUAGES.items())
    raise ValueError(f"unknown language '{name}'. Choose from: {choices}")


def language_rules(code: str, guide: bool = True) -> str:
    if code == "en":
        return ""
    english, native = LANGUAGES[code]
    rules = [
        f"Write {'the whole guide' if guide else 'your answer'} in {english} ({native}), "
        "in the plain register developers in India use day to day.",
        "Keep technical terms that developers normally say in English (API, commit, function, request, test) "
        "in English rather than coining translations.",
        "Never translate or transliterate code, commands, file paths, identifiers or the backticked "
        "`path:line` citations. Copy them exactly, so they can be checked.",
    ]
    if guide:
        rules.insert(2, f"Translate every heading, including the title, and put the English heading in brackets "
                        f"after it, for example: `## <heading in {english}> (Run it locally)`.")
        rules.append(f"In the Mermaid diagram, keep node ids as plain ASCII letters. Labels may be in {english}.")
    return "\nLanguage:\n" + "".join(f"- {rule}\n" for rule in rules)


def guide_user_prompt(repo_summary: str) -> str:
    return (
        "Here is a map of the repository, generated locally without reading file contents:\n\n"
        f"{repo_summary}\n\n"
        "Explore the code with your tools, then write the onboarding guide."
    )


def ask_user_prompt(repo_summary: str, question: str) -> str:
    return (
        "Repository map (generated locally):\n\n"
        f"{repo_summary}\n\n"
        f"Question: {question}"
    )
