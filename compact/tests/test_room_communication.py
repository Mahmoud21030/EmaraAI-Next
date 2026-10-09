"""Decision rooms remain separate backend objects but live under Communication tabs."""
import os
from pathlib import Path
SOURCE = Path(os.environ.get("EMARA_ROOM_PREVIEW_JS", str(Path(__file__).resolve().parents[1] / "src" / "emaraai_hub" / "ui" / "company.js")))
def ui():
    return SOURCE.read_text(encoding="utf-8")
def test_room_chats_are_communication_tabs():
    s = ui()
    assert "Decision room chats</a>" in s
    assert "const cur = S.route[0] === 'rooms' ? 'messages'" in s
    assert "VIEWS.messages = async project =>" in s
    assert "VIEWS.rooms = async id =>" in s
    assert "roomlog" in s and "roomtxt" in s
def test_existing_room_controls_stay_available():
    s = ui()
    for action in ("roomChatSend", "roomMore", "roomOpts", "roomClose", "roomOverrule", "roomArchive", "roomDelete"):
        assert action in s
    assert "['archived', 'Hidden']" in s
    assert "${room.archived ? 'Show' : 'Hide'}" in s
    assert "archive" in s and "unarchive" in s
