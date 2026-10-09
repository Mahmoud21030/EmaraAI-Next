"""The review gates the hub enforces, and the points record of every person."""
import pytest

from emaraai_hub.core.errors import InvalidInput


def _on(hub, **more):
    q = hub.settings.quality
    q.checklist = q.entry_points = q.independent_check = True
    for k, v in more.items():
        setattr(q, k, v)


async def _team(hub, master, agent, *names):
    await master("project_create", name="shop", goal="Build a shop")
    msid = (await master("session_start", project="shop"))["session_id"]
    sids = {}
    for n in names:
        await master("agent_create", session_id=msid, name=n, title="QA tester" if n.startswith("qa") else "Frontend developer", instructions="does the work")
    return msid, sids


async def _join(hub, agent, name):
    for _ in range(3):
        await hub.supervisor.tick()
    boot = next((b for _s, b in reversed(hub.driver.opened) if f'role="{name}"' in b), None)
    code = boot.split('join_code="')[1].split('"')[0]
    return (await agent("session_start", role=name, project="shop", join_code=code))["session_id"]


def _pts(hub, name):
    role = hub.services.projects.role(hub.services.projects.resolve("shop")["id"], name)
    return sorted((r["code"], r["points"]) for r in hub.services.quality._rows(role))


async def test_a_task_needs_conditions_evidence_for_each_and_a_reviewer_who_checked(hub, master, agent):
    _on(hub, independent_check=False)
    msid, _ = await _team(hub, master, agent, "dev")
    none = await master("task_assign", session_id=msid, agent="dev", title="Login API", instructions="JWT login")
    assert none["ok"] is False and none["error"]["code"] == "conditions_required" and "done_when" in none["error"]["fix"]
    tid = (await master("task_assign", session_id=msid, agent="dev", title="Login API", instructions="JWT login",
                        done_when=["POST /login returns a token", "wrong password returns 401"]))["result"]["task_id"]
    dev = await _join(hub, agent, "dev")
    told = next(m["text"] for m in (await agent("inbox_read", session_id=dev))["result"]["messages"] if "NEW TASK" in m["text"])
    assert "proof={'checks'" in told
    await agent("task_start", session_id=dev, task_id=tid)
    base = dict(session_id=dev, task_id=tid, outcome="done", summary="Login works and rejects wrong passwords.")
    assert (await agent("task_report", **base))["error"]["code"] == "checklist_required"
    assert (await agent("task_report", **base, proof={"checks": ["curl returned 200 with a token"]}))["error"]["code"] == "checklist_required"      # one of two
    assert (await agent("task_report", **base, proof={"checks": ["curl returned 200 with a token", "done"]}))["error"]["code"] == "checklist_required"
    r = await agent("task_report", **base, proof={"checks": ["curl returned 200 with a token", {"met": False, "evidence": "it returns 500, not 401"}]})
    assert r["error"]["code"] == "condition_not_met"
    assert hub.services.tasks.get(tid)["status"] == "in_progress"
    good = {"checks": ["curl -X POST /login returned 200 and a JWT", "the same call with a wrong password returned 401"]}
    assert (await agent("task_report", **base, proof=good))["ok"]
    report = next(m["text"] for m in (await master("inbox_read", session_id=msid))["result"]["messages"] if "REPORT for" in m["text"])
    assert "returned 401" in report and "confirmed=[" in report
    rv = dict(session_id=msid, task_id=tid, decision="accept")
    assert (await master("task_review", **rv))["error"]["code"] == "checklist_required"
    assert (await master("task_review", **rv, confirmed=good["checks"]))["error"]["code"] == "checklist_required"                                   # copied from the report
    ok = await master("task_review", **rv, confirmed=["I read the test output attached: the token is issued", "I read the 401 case in the test log"])
    assert ok["ok"] and hub.services.tasks.get(tid)["status"] == "done" and hub.services.tasks.get(tid)["accepted_by_role_id"]
    assert _pts(hub, "dev") == [("first_pass", 10)]


async def test_user_facing_work_lists_what_was_tried_and_a_dead_control_blocks_it(hub, master, agent):
    _on(hub, independent_check=False)
    msid, _ = await _team(hub, master, agent, "dev")
    tid = (await master("task_assign", session_id=msid, agent="dev", title="Sidebar", instructions="Build the sidebar", check="user_facing",
                        done_when=["every sidebar item opens its page"]))["result"]["task_id"]
    dev = await _join(hub, agent, "dev")
    assert "USER-FACING WORK" in str((await agent("inbox_read", session_id=dev))["result"]["messages"])
    await agent("task_start", session_id=dev, task_id=tid)
    base = dict(session_id=dev, task_id=tid, outcome="done", summary="The sidebar is built and wired.")
    checks = ["clicked each of the 5 items; each address changed"]
    assert (await agent("task_report", **base, proof={"checks": checks}))["error"]["code"] == "entry_points_required"
    dead = await agent("task_report", **base, proof={"checks": checks, "entry_points": [{"name": "Sales", "tried": "clicked", "observed": "address became /sales"},
                                                                                        {"name": "Repairs", "tried": "clicked", "observed": "nothing happened"}]})
    assert dead["error"]["code"] == "dead_entry_point" and "Repairs" in dead["error"]["message"] and "disable" in dead["error"]["fix"]
    points = [{"name": "Sales", "tried": "clicked", "observed": "address became /sales"}, {"name": "Repairs", "disabled": "the repairs module is not built yet"}]
    # the click check of the page was run for this task and found another dead control: the report is blocked until it is explained or fixed
    role = hub.services.projects.role(hub.services.projects.resolve("shop")["id"], "dev")
    hub.services.quality.store_audit(tid, role, {"url": "http://localhost:3000/", "total": 3, "controls": [
        {"label": "Sales", "outcome": "address"}, {"label": "Repairs", "outcome": "dead"}, {"label": "Reports", "outcome": "dead"}]})
    blocked = await agent("task_report", **base, proof={"checks": checks, "entry_points": points})
    assert blocked["error"]["code"] == "dead_entry_point" and "Reports" in blocked["error"]["message"] and "Repairs" not in blocked["error"]["message"]
    hub.services.quality.store_audit(tid, role, {"url": "http://localhost:3000/", "total": 3, "controls": [
        {"label": "Sales", "outcome": "address"}, {"label": "Repairs", "outcome": "dead"}, {"label": "Reports", "outcome": "content"}]})
    assert (await agent("task_report", **base, proof={"checks": checks, "entry_points": points}))["ok"]
    t = hub.services.tasks.full(hub.services.tasks.get(tid))
    assert t["proof"]["entry_points"][1]["disabled"] and t["proof"]["click_check"]["dead"] == ["Repairs"] and t["user_facing"] is True


async def test_an_independent_checker_must_really_check_and_its_verdict_moves_the_task(hub, master, agent):
    _on(hub, entry_points=False)
    msid, _ = await _team(hub, master, agent, "dev", "qa")
    tid = (await master("task_assign", session_id=msid, agent="dev", title="Export", instructions="Add CSV export", check="verify",
                        done_when=["the export file opens in Excel"]))["result"]["task_id"]
    dev = await _join(hub, agent, "dev")
    await agent("task_start", session_id=dev, task_id=tid)
    proof = {"checks": ["opened export.csv in Excel: 12 rows, the columns are right"]}
    assert (await agent("task_report", session_id=dev, task_id=tid, outcome="done", summary="CSV export added.", proof=proof))["ok"]
    t = hub.services.tasks.get(tid)
    assert t["status"] == "review" and t["verify_state"] == "pending"
    inbox = str((await master("inbox_read", session_id=msid))["result"]["messages"])
    assert "independent check" in inbox and "REPORT for" not in inbox                                    # the reviewer waits for the verdict
    early = await master("task_review", session_id=msid, task_id=tid, decision="accept", confirmed=["I looked at the file myself, it opens"])
    assert early["ok"] is False and "still running" in early["error"]["message"]
    qa = await _join(hub, agent, "qa")
    vtask = next(x for x in (await agent("task_list_mine", session_id=qa))["result"]["tasks"] if x["title"].startswith("Check:"))
    vid = vtask["task_id"]
    assert "INDEPENDENT CHECK" in str((await agent("inbox_read", session_id=qa))["result"]["messages"])
    await agent("task_start", session_id=qa, task_id=vid)
    rep = dict(session_id=qa, task_id=vid, outcome="done", summary="I exported twice and opened both files in Excel; rows and columns are right.")
    assert (await agent("task_report", **rep))["error"]["code"] == "verdict_required"
    lazy = await agent("task_report", **rep, proof={"verdict": "pass", "checks": ["opened the file, 12 rows"]})
    assert lazy["error"]["code"] == "check_not_done"                                                     # read the report, ran nothing
    role = hub.services.projects.role(hub.services.projects.resolve("shop")["id"], "qa")
    for i in range(3):
        hub.services.repos.tool_calls.add({"ts": hub.services.clock.now(), "plugin": "agent", "tool": "shell_run", "session_id": qa, "ok": True, "error_code": "",
                                           "duration_ms": 5, "cid": f"t{i}", "step": 0, "args_preview": "{}", "result_preview": "{}"})
    # a FAIL goes back to the author, costs the author and pays the checker
    assert (await agent("task_report", **{**rep, "summary": "The file opens, but the Arabic names are unreadable: the encoding is wrong."}, proof={"verdict": "fail"}))["ok"]
    t = hub.services.tasks.get(tid)
    assert t["status"] == "in_progress" and "encoding is wrong" in t["review_note"] and t["verify_state"] == ""
    assert _pts(hub, "dev") == [("check_failed", -6)] and _pts(hub, "qa") == [("check_caught", 4)]
    assert hub.services.tasks.get(vid)["status"] == "done"
    # fixed, reported again with a lesson, checked again: PASS -> the reviewer gets the report with the verdict
    again = await agent("task_report", session_id=dev, task_id=tid, outcome="done", summary="Export now writes UTF-8 with BOM.", proof=proof,
                        lesson="Open exported files with non-English text before I say an export works.")
    assert again["ok"], again
    v2 = next(x for x in (await agent("task_list_mine", session_id=qa))["result"]["tasks"] if x["title"].startswith("Check:"))["task_id"]
    await agent("task_start", session_id=qa, task_id=v2)
    for i in range(3):
        hub.services.repos.tool_calls.add({"ts": hub.services.clock.now(), "plugin": "agent", "tool": "file_read", "session_id": qa, "ok": True, "error_code": "",
                                           "duration_ms": 5, "cid": f"u{i}", "step": 0, "args_preview": "{}", "result_preview": "{}"})
    ok = await agent("task_report", session_id=qa, task_id=v2, outcome="done", summary="Exported again: Arabic names are readable in Excel now.",
                     proof={"verdict": "pass", "checks": ["opened the new export in Excel, Arabic text is readable"]})
    assert ok["ok"], ok
    assert hub.services.tasks.get(tid)["verify_state"] == "passed"
    report = next(m["text"] for m in (await master("inbox_read", session_id=msid))["result"]["messages"] if "REPORT for " + tid in m["text"])
    assert "INDEPENDENT CHECK by" in report and "PASS" in report
    assert (await master("task_review", session_id=msid, task_id=tid, decision="accept", confirmed=["I opened the attached export: readable"]))["ok"]
    assert _pts(hub, "dev") == [("after_rework", 5), ("check_failed", -6)]
    # ---- later the owner finds it broken after all: author, reviewer and checker all pay, and the task is open again
    out = hub.services.quality.file_defect(tid, "The export drops every row after the 1000th: a 3000-row list gives a 1000-row file.")
    assert {c["why"] for c in out["charged"]} == {"a defect was found after acceptance", "accepted work that had a defect", "passed work that had a defect"}
    assert ("defect_author", -12) in _pts(hub, "dev") and ("defect_verifier", -8) in _pts(hub, "qa") and _pts(hub, "master") == [("defect_reviewer", -8)]
    t = hub.services.tasks.get(tid)
    assert t["status"] == "in_progress" and "DEFECT FOUND AFTER ACCEPTANCE" in t["review_note"]
    assert (await agent("task_report", session_id=dev, task_id=tid, outcome="done", summary="Paging fixed.", proof=proof))["error"]["code"] == "lesson_required"


async def test_cancelling_an_independent_check_releases_parent_for_a_replacement(hub, master, agent):
    _on(hub, checklist=False, entry_points=False)
    msid, _ = await _team(hub, master, agent, "dev", "qa")
    tid = (await master("task_assign", session_id=msid, agent="dev", title="Export", instructions="Add CSV export", check="verify"))["result"]["task_id"]
    dev = await _join(hub, agent, "dev")
    await agent("task_start", session_id=dev, task_id=tid)
    assert (await agent("task_report", session_id=dev, task_id=tid, outcome="done", summary="CSV export added."))["ok"]
    qa = await _join(hub, agent, "qa")
    vid = next(x for x in (await agent("task_list_mine", session_id=qa))["result"]["tasks"] if x["title"].startswith("Check:"))["task_id"]
    assert (await master("task_review", session_id=msid, task_id=vid, decision="cancel"))["ok"]
    parent = hub.services.tasks.get(tid)
    assert parent["status"] == "in_progress" and parent["verify_state"] == "" and parent["verified_by_role_id"] is None
    assert "cancelled" in parent["review_note"].lower()
    assert (await agent("task_report", session_id=dev, task_id=tid, outcome="done", summary="CSV export is still ready for verification."))["ok"]
    replacement = next(x for x in (await agent("task_list_mine", session_id=qa))["result"]["tasks"] if x["title"].startswith("Check:"))["task_id"]
    assert replacement != vid and hub.services.tasks.get(tid)["verify_state"] == "pending"


async def test_points_grade_manual_changes_and_what_a_low_score_does(hub, master, agent):
    _on(hub, checklist=False, entry_points=False)
    msid, _ = await _team(hub, master, agent, "dev", "qa")
    q, pid = hub.services.quality, hub.services.projects.resolve("shop")["id"]
    dev = hub.services.projects.role(pid, "dev")
    assert q.score(dev)["grade"] == "-" and not q.low(dev["id"])
    with pytest.raises(InvalidInput):
        q.manual(pid, "dev", -5, "")                                                # points by hand need a reason
    q.manual(pid, "dev", -20, "Delivered the sidebar with dead links twice.")
    s = q.score(dev)
    assert s["last_30_days"] == -20 and s["grade"] == "E" and s["low"] and q.low(dev["id"])
    assert "quality.low_score" in [e["type"] for e in hub.services.bus.recent(limit=20)]
    # a low scorer's ordinary task now gets the independent check without anyone marking it
    r = await master("task_assign", session_id=msid, agent="dev", title="Fix the footer text", instructions="Change the year")
    assert any("low quality score" in n for n in r.get("notices", []))
    tid = r["result"]["task_id"]
    sid = await _join(hub, agent, "dev")
    await agent("task_start", session_id=sid, task_id=tid)
    await agent("task_report", session_id=sid, task_id=tid, outcome="done", summary="The footer shows the new year.")
    assert hub.services.tasks.get(tid)["verify_state"] == "pending"
    q.manual(pid, "dev", 30, "Fixed everything and added tests for it.")
    assert not q.low(dev["id"]) and q.score(dev)["grade"] == "C" and "quality.recovered" in [e["type"] for e in hub.services.bus.recent(limit=20)]
    rank = q.ranking(pid)
    assert rank[0]["key"] == "dev" and rank[0]["last_30_days"] == 10 and {x["key"] for x in rank} >= {"dev", "qa"}
    assert [l["why"] for l in q.ledger(dev)][:2] == ["given by the owner", "given by the owner"]
    # the score follows the person into another project
    hub.services.repos.roles.set(dev["id"], person_name="Omar Adel")
    hub.services.repos.db.exec("UPDATE agent_points SET person_name = 'Omar Adel' WHERE role_id = ?", (dev["id"],))
    await master("project_create", name="other", goal="x")
    other = hub.services.projects.resolve("other")["id"]
    hub.services.agents.reuse(pid, "dev", other)
    new = next(r for r in hub.services.repos.roles.list(other) if r.get("person_name") == "Omar Adel")
    assert q.score(new)["total"] == 10
