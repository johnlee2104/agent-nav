import subprocess
import pytest


@pytest.fixture
def tmp_repo(tmp_path):
    """A throwaway git repo with one committed file."""
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    (tmp_path / "a.py").write_text("print('a')\n")
    git("add", "a.py")
    git("commit", "-qm", "init")
    return tmp_path
