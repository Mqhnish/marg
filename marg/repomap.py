"""Build a compact, LLM-free map of a repository.

The map gives the model a head start (layout, languages, manifests, likely
entry points) so it spends its tool calls reading code that matters instead
of listing directories.
"""

from __future__ import annotations

import fnmatch
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ALWAYS_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build",
    ".next", ".nuxt", ".svelte-kit", "target", "vendor", ".idea", ".vscode",
    "coverage", ".gradle", ".terraform", "Pods", ".dart_tool",
}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svg", ".pdf",
    ".zip", ".gz", ".tar", ".tgz", ".7z", ".rar", ".jar", ".war", ".class",
    ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a", ".pyc", ".whl",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".wav",
    ".db", ".sqlite", ".parquet", ".pkl", ".pt", ".onnx", ".h5", ".npy",
    ".lock",
}

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust",
    ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".rb": "Ruby",
    ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++",
    ".hpp": "C++", ".cs": "C#", ".scala": "Scala", ".dart": "Dart",
    ".vue": "Vue", ".svelte": "Svelte", ".ex": "Elixir", ".exs": "Elixir",
    ".sh": "Shell", ".sql": "SQL", ".html": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".lua": "Lua", ".r": "R", ".jl": "Julia",
}

MANIFESTS = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "Pipfile", "go.mod", "Cargo.toml", "pom.xml", "build.gradle",
    "build.gradle.kts", "Gemfile", "composer.json", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "Makefile",
    "CMakeLists.txt", "pubspec.yaml", "mix.exs", "deno.json", "turbo.json",
    "pnpm-workspace.yaml", "vercel.json", "netlify.toml", "Procfile",
}

ENTRY_POINT_NAMES = {
    "main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "server.py",
    "cli.py", "__main__.py", "index.js", "index.ts", "server.js", "server.ts",
    "app.js", "app.ts", "main.js", "main.ts", "main.go", "main.rs", "lib.rs",
    "Main.java", "Program.cs", "index.html",
}

SECRET_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
    "*credentials*", "*secret*", ".npmrc", ".pypirc", "*.keystore",
]


def is_secret_file(name: str) -> bool:
    lowered = name.lower()
    if lowered in {".env.example", ".env.sample", ".env.template"}:
        return False
    return any(fnmatch.fnmatch(lowered, pat) for pat in SECRET_PATTERNS)


def load_gitignore(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return []
    patterns = []
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.append(line.rstrip("/"))
    return patterns


def is_ignored(rel: str, name: str, patterns: list[str]) -> bool:
    for pat in patterns:
        bare = pat.lstrip("/")
        # As in git, a pattern with a slash at the start or in the middle is anchored to the root.
        anchored = pat.startswith("/") or "/" in bare
        if not anchored and fnmatch.fnmatch(name, bare):
            return True
        if fnmatch.fnmatch(rel, bare) or rel.startswith(bare + "/"):
            return True
    return False


def is_path_ignored(rel: str, patterns: list[str]) -> bool:
    """True if the path, or any directory above it, is skipped by iter_files."""
    parts = rel.split("/")
    for i, part in enumerate(parts):
        if i < len(parts) - 1 and part in ALWAYS_IGNORE_DIRS:
            return True
        if is_ignored("/".join(parts[:i + 1]), part, patterns):
            return True
    return False


@dataclass
class FileInfo:
    path: str
    lines: int
    language: str | None


@dataclass
class RepoMap:
    root: Path
    files: list[FileInfo] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    readme: str | None = None
    skipped_secrets: int = 0

    @property
    def languages(self) -> list[tuple[str, int]]:
        counts: Counter[str] = Counter()
        for f in self.files:
            if f.language:
                counts[f.language] += f.lines
        return counts.most_common()

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.files)


def count_lines(path: Path, limit_bytes: int = 2_000_000) -> int:
    try:
        if path.stat().st_size > limit_bytes:
            return 0
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def iter_files(root: Path, start: Path | None = None):
    """Yield (path relative to root, absolute path) for every non-ignored file under start.

    The root's .gitignore applies wherever the walk starts.
    """
    patterns = load_gitignore(root)
    for dirpath, dirnames, filenames in os.walk(start or root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in ALWAYS_IGNORE_DIRS
            and not d.startswith(".") or d in {".github"}
        )
        dirnames[:] = [
            d for d in dirnames
            if not is_ignored(f"{rel_dir}/{d}".lstrip("/"), d, patterns)
        ]
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}".lstrip("/")
            if is_ignored(rel, name, patterns):
                continue
            yield rel, Path(dirpath) / name


def build_repo_map(root: str | Path) -> RepoMap:
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")
    repo = RepoMap(root=root)
    for rel, abspath in iter_files(root):
        name = abspath.name
        if is_secret_file(name):
            repo.skipped_secrets += 1
            continue
        ext = abspath.suffix.lower()
        if ext in BINARY_EXTS:
            continue
        repo.files.append(FileInfo(rel, count_lines(abspath), LANGUAGES.get(ext)))
        if name in MANIFESTS:
            repo.manifests.append(rel)
        if name in ENTRY_POINT_NAMES and rel.count("/") <= 3:
            repo.entry_points.append(rel)
        if repo.readme is None and name.lower() in {"readme.md", "readme.rst", "readme.txt", "readme"}:
            if "/" not in rel:
                try:
                    repo.readme = abspath.read_text(errors="ignore")[:3000]
                except OSError:
                    pass
    return repo


def render_tree(repo: RepoMap, max_entries: int = 250, max_per_dir: int = 25) -> str:
    """Render the file list as an indented tree, collapsing crowded directories."""
    by_dir: dict[str, list[FileInfo]] = {}
    for f in repo.files:
        parent = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
        by_dir.setdefault(parent, []).append(f)

    lines: list[str] = []
    shown = 0
    for directory in sorted(by_dir):
        if shown >= max_entries:
            remaining = sum(len(v) for v in by_dir.values()) - shown
            lines.append(f"... ({remaining} more files not shown)")
            break
        files = by_dir[directory]
        depth = directory.count("/") + 1 if directory else 0
        if directory:
            lines.append("  " * (depth - 1) + f"{directory.split('/')[-1]}/  ({directory})")
        for f in files[:max_per_dir]:
            lines.append("  " * depth + f"{f.path.split('/')[-1]}  [{f.lines} lines]")
            shown += 1
        if len(files) > max_per_dir:
            lines.append("  " * depth + f"... {len(files) - max_per_dir} more files")
            shown += len(files) - max_per_dir
    return "\n".join(lines)


def render_summary(repo: RepoMap) -> str:
    langs = ", ".join(f"{name} ({lines} lines)" for name, lines in repo.languages[:6]) or "unknown"
    parts = [
        f"Repository: {repo.root.name}",
        f"Files: {len(repo.files)} text files, {repo.total_lines} lines",
        f"Languages: {langs}",
        f"Manifests: {', '.join(repo.manifests) or 'none found'}",
        f"Likely entry points: {', '.join(repo.entry_points) or 'none found'}",
    ]
    if repo.skipped_secrets:
        parts.append(f"Secret-looking files skipped: {repo.skipped_secrets}")
    parts.append("")
    parts.append("File tree:")
    parts.append(render_tree(repo))
    if repo.readme:
        parts.append("")
        parts.append("README (first 3000 chars):")
        parts.append(repo.readme)
    return "\n".join(parts)
