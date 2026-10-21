from marg.repomap import build_repo_map, render_summary
from marg.tools import RepoTools, redact


def test_repo_map_skips_ignored_binary_and_secret_files(sample_repo):
    repo = build_repo_map(sample_repo)
    paths = {f.path for f in repo.files}
    assert {"README.md", "app.py", "routes.py", "requirements.txt"} <= paths
    assert not any(p.startswith(("logs/", "node_modules/")) for p in paths)
    assert "scratch.tmp" not in paths
    assert "logo.png" in paths  # listed so the model knows it exists, but marked binary
    assert [f.path for f in repo.files if f.binary] == ["logo.png"]
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


def test_read_file_cuts_at_a_whole_line_and_records_what_was_shown(tmp_path):
    (tmp_path / "big.py").write_text("".join(f"x = '{'y' * 200}'\n" for _ in range(300)))
    tools = RepoTools(tmp_path)
    out = tools.read_file("big.py")
    (start, end), = tools.ranges_read["big.py"]
    assert start == 1 and 50 < end < 300
    assert f"[big.py: lines 1-{end} of 300] (call read_file again" in out
    assert out.count("\n") == end  # end lines of code plus the footer line
    assert "has only 300 lines" in tools.call("read_file", {"path": "big.py", "start_line": 999})


def test_tools_refuse_gitignored_and_dependency_paths(sample_repo):
    tools = RepoTools(sample_repo)
    assert "is ignored" in tools.call("read_file", {"path": "logs/out.log"})
    assert "is ignored" in tools.call("read_file", {"path": "scratch.tmp"})
    assert "is ignored" in tools.call("read_file", {"path": "node_modules/x.js"})
    assert "is ignored" in tools.call("list_dir", {"path": "logs"})
    assert tools.files_read == {}


def test_root_gitignore_applies_when_listing_a_subdirectory(tmp_path):
    (tmp_path / ".gitignore").write_text("generated/\n/config\n")
    for rel in ["src/main.py", "src/generated/out.py", "config/local.py", "src/app/config/settings.py"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x = 1\n")
    tools = RepoTools(tmp_path)
    assert tools.call("list_dir", {"path": "src"}).splitlines() == ["src/main.py", "src/app/config/settings.py"]
    paths = {f.path for f in build_repo_map(tmp_path).files}
    assert "src/app/config/settings.py" in paths  # /config is anchored to the root
    assert "config/local.py" not in paths


def test_own_entry_points_come_first_and_demo_ones_are_counted(tmp_path):
    for rel in ["index.js", "src/server.js", "examples/a/index.js", "examples/b/index.js", "test/app.js"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n")
    repo = build_repo_map(tmp_path)
    assert repo.entry_points[:2] == ["index.js", "src/server.js"]
    line = next(l for l in render_summary(repo).splitlines() if l.startswith("Likely entry points"))
    assert line == "Likely entry points: index.js, src/server.js (and 3 more, mostly in example, test or docs folders)"


def test_binary_and_asset_files_are_listed_but_not_counted(sample_repo):
    (sample_repo / "docs").mkdir()
    (sample_repo / "docs" / "diagram.svg").write_text("<svg/>\n")
    text = render_summary(build_repo_map(sample_repo))
    assert "diagram.svg  [binary]" in text and "logo.png  [binary]" in text
    assert "plus 2 binary or asset files" in text
    assert "Files: 5 text files" in text
    assert "is a binary file" in RepoTools(sample_repo).call("read_file", {"path": "docs/diagram.svg"})
