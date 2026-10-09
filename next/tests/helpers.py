import subprocess
import sys
from pathlib import Path


def make_remote(tmp: Path, name: str = "origin.git") -> str:
    """A bare repo with one commit on main, used as the project's GitHub stand-in."""
    bare = tmp / name
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp / f"{name}-seed"
    subprocess.run(["git", "clone", "--quiet", str(bare), str(seed)], check=True, capture_output=True)
    (seed / "README.md").write_text("hello\n")
    for args in (["config", "user.name", "t"], ["config", "user.email", "t@t"], ["add", "-A"],
                 ["commit", "--quiet", "-m", "init"], ["push", "--quiet", "origin", "HEAD:main"]):
        subprocess.run(["git", *args], cwd=seed, check=True, capture_output=True)
    return str(bare)


def py(code: str) -> str:
    """Shell command running python code; works on sh and PowerShell (no quotes inside code)."""
    exe = sys.executable.replace("\\", "/")
    return f'"{exe}" -c "{code}"' if sys.platform != "win32" else f'& "{exe}" -c "{code}"'


def remote_file(remote: str, ref: str, path: str) -> str:
    return subprocess.run(["git", "--git-dir", remote, "show", f"{ref}:{path}"], capture_output=True, text=True).stdout
