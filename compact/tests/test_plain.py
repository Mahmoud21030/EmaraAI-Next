"""Plain words first: what the owner reads, and the technical part behind a button."""
import pytest

from emaraai_hub.core.errors import InvalidInput
from emaraai_hub.services import plain

LOG = "[72%] Post-refresh rerun: /pos and /repairs fail with POS_API_UNAVAILABLE, web PID 130864, API port 4000, see C:/p/run.log."
GOOD = ("The shop's pages open, but they cannot load their data yet, so the final test has to wait. The server team is fixing the connection now.\n"
        "DETAILS:\n/pos and /repairs return POS_API_UNAVAILABLE; web PID 130864, API port 4000 not answering; log in C:/p/run.log")


def test_a_message_that_is_a_log_is_sent_back_and_a_plain_one_passes():
    plain.check("I finished the login page. It works on the phone too.")
    plain.check("Task T-6VZFP is done and the checks passed.")                       # an id or two is not a log
    plain.check(GOOD)
    with pytest.raises(InvalidInput) as e:
        plain.check(LOG)
    assert e.value.code == "plain_first" and "DETAILS:" in e.value.fix and "reads like a log" in str(e.value)
    with pytest.raises(InvalidInput):
        plain.check("DETAILS:\nPID 130864 on port 4000")                              # the plain part is missing
    with pytest.raises(InvalidInput):
        plain.check("PID 130864 died, port 4000 closed, see run.log.\nDETAILS:\nmore")  # the part before DETAILS: is the log


def test_what_the_control_center_shows_first():
    assert plain.split("All good, the page is ready.") == ("All good, the page is ready.", False)
    easy, more = plain.split(GOOD)
    assert more and easy.endswith("fixing the connection now.") and "PID" not in easy
    easy, more = plain.split("REPORT for T-AB12C (Build the cart) — outcome: DONE\n\nThe cart works and keeps its items after a reload.\n\n"
                             "Conditions, with the author's evidence:\n1. adds items\n   -> ran cart.test.js, 12 passed")
    assert more and easy == "Report: “Build the cart” finished.\nThe cart works and keeps its items after a reload."
    easy, more = plain.split("The page is slow today. " + LOG + " We are looking into it.")       # an older message without the DETAILS line
    assert more and "The page is slow today." in easy and "We are looking into it." in easy and "PID" not in easy


async def test_the_tools_ask_for_plain_words_and_the_owner_is_never_corrected(hub, master, agent):
    hub.settings.tools.plain_messages = True
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    await master("agent_create", session_id=msid, name="dev", title="Dev", instructions="writes the code")
    sid = (await agent("session_start", role="dev", project="shop"))["session_id"]
    bad = await agent("message_send", session_id=sid, to="master", text=LOG)
    assert bad["ok"] is False and bad["error"]["code"] == "plain_first"
    assert (await agent("message_send", session_id=sid, to="master", text=GOOD))["ok"]
    pid = hub.services.projects.resolve("shop")["id"]
    shown = [m for m in hub.services.company.comms(pid, "*")["messages"] if m["has_details"]]
    assert len(shown) == 1 and shown[0]["plain"].endswith("fixing the connection now.") and "PID" not in shown[0]["plain"]
    hub.services.inbox.send(pid, from_role_id=None, to="dev", body=LOG)                # the owner may write as they like
    mine = [m for m in hub.services.company.comms(pid, "*")["messages"] if m["from_owner"]]
    assert mine and mine[-1]["plain"] == LOG and mine[-1]["has_details"] is False
