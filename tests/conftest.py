import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# Demo\nA tiny Flask app.\n")
    (tmp_path / "requirements.txt").write_text("flask\n")
    (tmp_path / ".gitignore").write_text("logs/\n*.tmp\n")
    (tmp_path / ".env").write_text("SECRET_KEY=supersecretvalue123\n")
    (tmp_path / "app.py").write_text(textwrap.dedent("""\
        from flask import Flask
        from routes import register

        app = Flask(__name__)
        register(app)

        if __name__ == "__main__":
            app.run()
    """))
    (tmp_path / "routes.py").write_text(textwrap.dedent("""\
        API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"

        def register(app):
            @app.get("/health")
            def health():
                return {"ok": True}
    """))
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "out.log").write_text("ignored\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("ignored\n")
    (tmp_path / "scratch.tmp").write_text("ignored\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    return tmp_path
