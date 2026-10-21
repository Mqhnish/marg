from marg.repomap import build_repo_map, render_summary
from marg.tools import RepoTools, redact


def test_repo_map_skips_ignored_binary_and_secret_files(sample_repo):
    repo = build_repo_map(sample_repo)
    paths = {f.path for f in repo.files}
    assert {"README.md", "app.py", "routes.py", "requirements.txt"} <= paths
    assert not any(p.startswith(("logs/", "node_modules/")) for p in paths)
    assert "scratch.tmp" not in paths
    assert "logo.png" not in paths
    assert ".env" not in paths
    assert repo.skipped_secrets == 1
    assert "requirements.txt" in repo.manifests
    assert "app.py" in repo.entry_points
    assert repo.languages[0][0] == "Python"


def test_summary_mentions_readme_and_tree(sample_repo):
    text = render_summary(build_repo_map(sample_repo))
    assert "A tiny Flask app." in text
    assert "app.py  [8 lines]" in text


def test_read_file_numbers_lines_and_tracks_reads(sample_repo):
    tools = RepoTools(sample_repo)
    out = tools.read_file("app.py", 1, 3)
    assert "    1  from flask import Flask" in out
    assert "[app.py: lines 1-3 of 8]" in out
    assert tools.files_read == {"app.py": 8}


def test_read_file_redacts_tokens(sample_repo):
    out = RepoTools(sample_repo).read_file("routes.py")
    assert "sk-abcdef" not in out
    assert "REDACTED" in out


def test_tools_refuse_path_traversal_and_secrets(sample_repo):
    tools = RepoTools(sample_repo)
    assert tools.call("read_file", {"path": "../../etc/passwd"}).startswith("error: path")
    assert "secrets file" in tools.call("read_file", {"path": ".env"})
    assert tools.call("read_file", {"path": "/app.py"}).startswith("    1")  # leading slash stays inside root


def test_search_and_list(sample_repo):
    tools = RepoTools(sample_repo)
    hits = tools.call("search", '{"pattern": "def \\\\w+", "path_glob": "*.py"}')
    assert "routes.py:3: def register(app):" in hits
    assert "supersecret" not in tools.call("search", {"pattern": "SECRET"})
    listing = tools.call("list_dir", {"path": "."})
    assert "app.py" in listing and ".env" in listing or "app.py" in listing
    assert "node_modules" not in listing


def test_bad_tool_calls_return_errors_not_exceptions(sample_repo):
    tools = RepoTools(sample_repo)
    assert tools.call("rm_rf", {}).startswith("error: unknown tool")
    assert tools.call("read_file", "{not json").startswith("error: arguments")
    assert tools.call("read_file", {"nope": 1}).startswith("error: bad arguments")
    assert tools.call("search", {"pattern": "("}).startswith("error: invalid regex")


def test_redact_generic_assignment():
    assert redact('password = "hunter2hunter2"') == 'password = "[REDACTED]"'
    assert "bc_live" not in redact("export BHARATCODE_API_KEY=bc_live_abcdef123456")


def test_bad_argument_values_and_read_errors_return_errors(sample_repo, monkeypatch):
    tools = RepoTools(sample_repo)
    assert tools.call("read_file", {"path": "app.py", "end_line": "end"}).startswith("error: read_file failed")
    assert tools.call("read_file", {"path": 123}).startswith("error: read_file failed")

    def unreadable(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_text", unreadable)
    assert "permission denied" in tools.call("read_file", {"path": "app.py"})
