"""Plain words first. The owner reads what the team writes, and the owner is not a programmer.

Every message, progress note and report starts with a few simple sentences anyone can follow: what happened, whether that
is good or bad, what happens next. Whatever is technical (ids, ports, process numbers, file names, commands, log lines)
comes after a line that says DETAILS: - the Control Center shows the plain part and keeps the rest behind a button.

check() refuses a message that is technical from its first line (tools.plain_messages). split() is what the Control Center
uses to show a message; for older messages without the DETAILS line it keeps the sentences that are free of technical items.
"""
from __future__ import annotations

import re

from ..core.errors import InvalidInput

MARK = re.compile(r"(?im)^[ \t>*_-]*(?:technical[ \t]+)?details[ \t]*:?[ \t*_]*$|^[ \t>*_-]*(?:التفاصيل(?:[ \t]+التقنية)?|تفاصيل[ \t]+تقنية)[ \t]*:?[ \t*_]*$")
SECTION = re.compile(r"(?m)^(?:Conditions, with the author's evidence:|Tried by the author:|Files: |Details:|Review it: |Visual review of the screenshot:|Fix it, then task_report)")
REPORT = re.compile(r"^REPORT for \S+ \((.*?)\) — outcome: (\w+)\s*", re.S)
TECH = re.compile(r"""
      \b[A-Z]{1,3}-[A-Z0-9]{4,6}\b                                  # T-6VZFP, AP-1F3C9D
    | \bPID\s*\d+ | \bport\s*\d+                                    # process and port numbers
    | (?<![\w.])\d{4,6}(?![\w.%])                                   # 3020, 121568
    | [A-Za-z]:[\\/][^\s,;)]+ | (?<![\w:])/[\w.-]+(?:/[\w.-]+)+     # paths
    | \b[\w-]+\.(?:py|js|ts|tsx|jsx|json|sql|ps1|md|yaml|yml|css|html|log|exe|env|toml|sh|bat)\b
    | https?://\S+ | \b(?:localhost|127\.0\.0\.1)(?::\d+)?\S*
    | \b[A-Z][A-Z0-9]+_[A-Z0-9_]+\b                                 # API_UNAVAILABLE
    | `[^`\n]+` | (?<!\w)--[a-z][\w-]+                             # code, command flags
    | \bHTTP\s*\d{3}\b | \b[0-9a-f]{7,40}\b(?<![0-9]{7})            # status codes, hashes
    | \b\d+(?:\.\d+)?\s?(?:MB|GB|KB|ms)\b                           # sizes and timings
""", re.X)
SENTENCE = re.compile(r"(?<=[.!?؟؛])\s+|\n+")
LIMIT = 3           # this many technical items in the plain part is too many
FIX = ("Write for the owner, who is not a programmer. Start with 1-3 simple sentences: what happened, whether that is good or bad, and what "
       "happens next - in everyday words, without ids, ports, process numbers, file names, commands or abbreviations. Then a line with only "
       "DETAILS: and under it everything technical. Example:\n"
       "The shop's pages open, but they cannot load data yet, so the final test has to wait. The server team is fixing the connection now.\n"
       "DETAILS:\n/pos and /repairs return API_UNAVAILABLE; web PID 130864, API port 4000 not answering ...")


def tech_items(text: str) -> list[str]:
    return [m.group(0) for m in TECH.finditer(text or "")]


def check(text: str, what: str = "message") -> None:
    """Refuse a text that is technical from its first line (nothing is refused that an owner could read)."""
    text = (text or "").strip()
    m = MARK.search(text)
    plain = text[:m.start()].strip() if m else text
    items = tech_items(plain)
    if m and len(plain) < 25:
        raise InvalidInput(f"The {what} has no plain part before DETAILS:.", code="plain_first", fix=FIX)
    if len(items) >= LIMIT:
        where = "The part before DETAILS: still" if m else f"This {what}"
        raise InvalidInput(f"{where} reads like a log, not like something the owner can follow ({', '.join(items[:4])} ...).", code="plain_first", fix=FIX)


def split(text: str) -> tuple[str, bool]:
    """(what to show first, is there more behind the button)."""
    text = (text or "").strip()
    head = ""
    r = REPORT.match(text)
    if r:
        word = {"DONE": "finished", "FAILED": "failed", "BLOCKED": "is blocked"}.get(r.group(2).upper(), r.group(2).lower())
        head, text = f"Report: “{r.group(1).strip()}” {word}.\n", text[r.end():]
    m = MARK.search(text)
    if m and text[:m.start()].strip():
        return (head + text[:m.start()].strip()), True
    s = SECTION.search(text)
    body, more = (text[:s.start()].strip(), True) if s and text[:s.start()].strip() else (text, bool(head))
    if len(tech_items(body)) < LIMIT and len(body) <= 600:
        return (head + body).strip(), more
    keep = [p.strip() for p in SENTENCE.split(body) if p.strip() and len(tech_items(p)) <= 1]
    plain = " ".join(keep)
    if len(plain) < 40:             # everything in it is technical: show its beginning, the rest is behind the button
        first = SENTENCE.split(body)[0].strip()
        plain = first[:200] + ("…" if len(first) > 200 or len(body) > len(first) else "")
    return (head + plain).strip(), True
