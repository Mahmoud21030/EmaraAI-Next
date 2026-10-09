"""Provider-independent storage of the actual conversation visible to the owner."""
import hashlib
import re

from ..infra.logging import get_logger

log = get_logger("services")


def keep_turns(hub, session_id: str, turns, project_id: str = "") -> None:
    if not turns:
        return
    try:
        session = hub.services.repos.sessions.get(session_id) or {}
        now = hub.services.clock.now()
        with hub.services.db.tx():
            for i, turn in enumerate(turns):
                who = turn.get("who") or turn.get("role")
                if who not in ("user", "assistant"):
                    continue
                text = re.sub(r"(?m)^\s*(You said|ChatGPT said|Claude responded):\s*", "", str(turn.get("text") or "")).strip()
                if not text:
                    continue
                digest = hashlib.sha256((who + "|" + text).encode()).hexdigest()[:32]
                if len(text) > 200000:
                    text = text[:200000] + "\n[The remaining text exceeds the saved preview limit; open the original chat to read it.]"
                hub.services.db.exec("INSERT OR IGNORE INTO chat_texts(project_id, role_id, session_id, who, text, digest, captured_at) VALUES (?,?,?,?,?,?,?)",
                                     (project_id or session.get("project_id"), session.get("role_id"), session_id, who, text, digest, now + i * .001))
    except Exception as error:
        log.warning("could not keep conversation text", session_id=session_id, error=str(error))
