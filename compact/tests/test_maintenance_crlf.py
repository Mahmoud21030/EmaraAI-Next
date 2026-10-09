"""Regression: maintainer proposals can edit Windows CRLF files safely."""
from pathlib import Path

from emaraai_hub.core.errors import Conflict
import pytest


def test_multiline_proposal_applies_to_crlf_and_preserves_line_endings(hub, tmp_path):
    root = tmp_path / "hubcopy"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "data").mkdir()
    (root / "docs" / "MAINTAINER.md").write_text("# Handbook\n", encoding="utf-8")
    win = root / "src" / "win.py"
    unix = root / "src" / "unix.py"
    original = b"def first():\r\n    return 1\r\ndef second():\r\n    return 3\r\n"
    win.write_bytes(original)
    unix.write_bytes(b"def f():\n    return 7\n")
    hub.settings.base_dir = str(root)
    hub.settings.maintenance.enabled = True
    m = hub.services.maintenance
    role = m.maintainer(create=True)
    edits = [
        {"path": "src/win.py", "find": "def first():\n    return 1\n",
         "replace": "def first():\n    return 2\n"},
        {"path": "src/win.py", "find": "def second():\n    return 3\n",
         "replace": "def second():\n    return 4\n"},
        {"path": "src/unix.py", "find": "def f():\n    return 7\n",
         "replace": "def f():\n    return 8\n"},
    ]
    p = m.propose(role, title="Handle multiline Windows source", why="The existing approval path must retain CRLF when matching LF proposal snippets.", edits=edits)
    assert m.approve(p["id"])["status"] == "applied"
    assert win.read_bytes() == original.replace(b"return 1", b"return 2").replace(b"return 3", b"return 4")
    assert unix.read_bytes() == b"def f():\n    return 8\n"
    assert m.revert(p["id"])["status"] == "reverted"
    assert win.read_bytes() == original
    assert unix.read_bytes() == b"def f():\n    return 7\n"


def test_stale_crlf_proposal_still_fails_without_overwriting_changes(hub, tmp_path):
    root = tmp_path / "hubcopy"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "data").mkdir()
    (root / "docs" / "MAINTAINER.md").write_text("# Handbook\n", encoding="utf-8")
    path = root / "src" / "win.py"
    path.write_bytes(b"def f():\r\n    return 1\r\n")
    hub.settings.base_dir = str(root)
    hub.settings.maintenance.enabled = True
    m = hub.services.maintenance
    role = m.maintainer(create=True)
    p = m.propose(role, title="Detect changed Windows source", why="Avoid applying a stale patch even with CRLF source.", edits=[
        {"path": "src/win.py", "find": "def f():\n    return 1\n", "replace": "def f():\n    return 2\n"}
    ])
    path.write_bytes(b"def f():\r\n    return 99\r\n")
    with pytest.raises(Conflict):
        m.approve(p["id"])
    assert path.read_bytes() == b"def f():\r\n    return 99\r\n"
