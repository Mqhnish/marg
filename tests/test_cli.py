from marg.cli import main


def test_map_needs_no_key(sample_repo, capsys, monkeypatch):
    monkeypatch.delenv("BHARATCODE_API_KEY", raising=False)
    assert main(["map", str(sample_repo)]) == 0
    out = capsys.readouterr().out
    assert "Likely entry points: app.py" in out


def test_guide_without_key_exits_cleanly(sample_repo, capsys, monkeypatch):
    monkeypatch.delenv("BHARATCODE_API_KEY", raising=False)
    assert main(["guide", str(sample_repo)]) == 1
    assert "BHARATCODE_API_KEY is not set" in capsys.readouterr().err


def test_bad_path(capsys):
    assert main(["map", "/definitely/not/here"]) == 1
