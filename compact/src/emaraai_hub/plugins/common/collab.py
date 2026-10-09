"""Collaboration tools shared by the Master and Agent plugins.

Naming: <domain>_<verb>  (session_*, inbox_*, message_*, memory_*, task_*, agent_*, project_*).
Parameters: required first, few, enums where possible, defaults that do the right thing.
"""

import re

from typing import Annotated, Any, Literal

from pydantic import Field

from ...core.ids import slug as ids_slug
from ...core.errors import InvalidInput, PermissionDenied, HubError
from ...core.models import RoleKind
from .registrar import call
from .toolspec import Reply, ToolBook

SID = Annotated[str, Field(description="Session id from session_start (S-XXXX).")]


def _hub():
    return call().hub


def _plain(text: str, what: str = "message") -> None:
    """Plain words first (tools.plain_messages): the owner reads this, and the technical part belongs under DETAILS:."""
    from ...services import plain
    if call().hub.settings.tools.plain_messages and (text or "").strip():
        plain.check(text, what)


def _svc():
    return call().hub.services


def _require_master():
    c = call()
    if c.role["kind"] != RoleKind.MASTER.value:
        raise PermissionDenied("Only the master chat can do this.", fix="Ask master with ask_master or message_send(to='master').")


def build_collab_book(*, session_kind: str) -> ToolBook:
    """session_kind: 'master' or 'agent' — decides which roles session_start accepts."""
    book = ToolBook()
    is_master_plugin = session_kind == RoleKind.MASTER.value

    # ------------------------------------------------------------------ session
    role_hint = "Always 'master' in this plugin." if is_master_plugin else "Your agent name from your first message, e.g. 'backend'."

    @book.tool(name="session_start", needs_session=False,
               summary="Join the project as your role and load the full project memory. ALWAYS the first call in a chat.",
               use_when="At the very start of a chat, or when a tool says session_required / session_closed.",
               avoid="Do not call it again during normal work (it would open a new session).",
               returns="session_id (use it in every later call), your memory, your rules, and what to do next.",
               example=f"session_start(role='{'master' if is_master_plugin else 'backend'}', project='shop', join_code='J-ABC123')")
    def session_start(role: Annotated[str, Field(description=role_hint)] = ("master" if is_master_plugin else ""),
                      project: Annotated[str, Field(description="Project name. Empty = the only active project.")] = "",
                      join_code: Annotated[str, Field(description="Code from your first message if it has one (J-XXXXXX). Else empty.")] = "") -> Reply:
        hub = _hub()
        svc = hub.services
        p = svc.projects.resolve(project)
        role_row = svc.projects.role(p["id"], role or ("master" if is_master_plugin else ""))
        expected = RoleKind.MASTER.value if is_master_plugin else RoleKind.AGENT.value
        if role_row["kind"] != expected:
            other = hub.plugin_title("agent" if is_master_plugin else "master")
            raise InvalidInput(f"Role '{role_row['name']}' is a {role_row['kind']} and cannot join through this plugin.",
                               fix=f"Use the {other} plugin for that role, or pass the correct role.")
        session, replaced = svc.sessions.start(p["id"], role_row["name"], join_code=join_code)
        call().session = session
        call().role = role_row
        rules = hub.prompts.render("rules_master" if is_master_plugin else "rules_agent", session_id=session["id"], role=role_row["name"], max_tabs=hub.settings.pc.max_tabs_per_agent or "any number of")
        rules += (f"\nPORT {hub.settings.server.port} BELONGS TO THE EMARAAI HUB. Never start, test or stop anything on port "
                  f"{hub.settings.server.port}; give the project's own servers another port (for example 3000 or 5173).")
        sid = session["id"]
        nxt = ["Read the memory below carefully before acting."]
        second = "project_status" if is_master_plugin else "task_list_mine"
        if svc.inbox.unread_count(role_row["id"]):
            nxt.append(f"Then, in ONE call: hub_batch(session_id='{sid}', steps=[{{'tool':'inbox_read'}},{{'tool':'{second}'}}]).")
        elif is_master_plugin and hub.settings.tools.require_plan and not svc.plan.exists(p["id"]):
            nxt.append("FIRST design the whole project. In this reply write the full plan and the architecture, and save them with "
                       "plan_save(overview, architecture, steps). No task can be assigned before that.")
        elif is_master_plugin:
            nxt.append("Check project_status, then plan and assign tasks with task_assign (several task_assign calls fit in one hub_batch).")
        else:
            nxt.append("Call task_list_mine to see your work.")
        return Reply({"session_id": session["id"], "role": role_row["name"], "project": p["name"],
                      "generation": session["generation"], "replaced_chat": replaced["id"] if replaced else None,
                      "IMPORTANT": f"Put session_id='{session['id']}' in EVERY tool call.",
                      **({"project_folder": p["folder"]} if p.get("folder") else {}),
                      "rules": rules, "memory": svc.memory.boot_packet(session)}, next=nxt)

    @book.tool(name="ask_client", dedupe_seconds=120,
               summary="Ask the CLIENT (the user who ordered the project) a question. Only the master and team leads may.",
               use_when="A decision belongs to the client: scope, priorities, taste, money, access, anything you must not guess.",
               avoid="Not for technical choices the team can make itself. Do not stop all work while waiting.",
               returns="question id. The answer arrives in your inbox. If the client is silent, what you recommended is applied automatically.",
               example="ask_client(session_id='S-7K2P', question='Should invoices be numbered per year or continuously?', "
                       "options=['Per year', 'Continuous'], recommended='Per year: it is what most shops use.')")
    def ask_client(session_id: SID, question: Annotated[str, Field(description="One clear question the client can answer without context.")],
                   options: Annotated[list[str], Field(description="Possible answers to pick from, or empty list.")] = [],
                   recommended: Annotated[str, Field(description="What you would do and why. Done automatically if the client stays silent.")] = "") -> Reply:
        c = call()
        q = _svc().agents.ask_client(c.role, question, options, recommended)
        mins = int(_svc().settings.lifecycle.client_answer_minutes)
        return Reply({"question_id": q["id"], "status": "waiting for the client"},
                     next=[f"Keep working on what does not depend on it. The client has {mins} minutes; after that your recommendation "
                           "is applied automatically and you are told to go on with it."])

    @book.tool(name="skill_read", readonly=True, counts_for_checkpoint=False, allowed_when_checkpoint_due=True,
               summary="Read one of your skills (a how-to guide for your job) in full, or one of the files that belong to it.",
               use_when="Before work a skill applies to, when your first message showed only its beginning or listed more files.",
               returns="the text of the guide (and the names of its other files).",
               example="skill_read(session_id='S-7K2P', name='web-design-guidelines')")
    async def skill_read(session_id: SID, name: Annotated[str, Field(description="Skill name from your first message.")],
                         file: Annotated[str, Field(description="One of the skill's files, or empty for the guide itself.")] = "") -> Reply:
        return Reply(await _svc().skills.read(name, file))

    @book.tool(name="note_save", counts_for_checkpoint=False, allowed_when_checkpoint_due=True, dedupe_seconds=10,
               summary="Write a note into YOUR OWN memory. It stays with you in every future chat of yours, in any task.",
               use_when="You learned something worth keeping: a lesson from a mistake, a tip that makes you work better, a reusable fact, "
                        "or temporary context about the work at hand.",
               avoid="Not for project decisions everybody needs (memory_save) and not for progress snapshots (memory_checkpoint).",
               returns="the saved note.",
               example="note_save(session_id='S-7K2P', kind='lesson', text='Run npm run lint before npm test: a syntax error hides the real failures.')")
    def note_save(session_id: SID, kind: Literal["context", "knowledge", "lesson", "tip"],
                  text: Annotated[str, Field(description="One clear, self-contained note.")]) -> Reply:
        c = call()
        if kind == "lesson":
            text = _svc().agents.check_lesson(text)
        m = _svc().agents.remember(c.role["id"], kind, text, source="agent")
        return Reply({"id": m["id"], "kind": m["kind"]})

    @book.tool(name="decision_say", dedupe_seconds=30,
               summary="Speak in a DECISION ROOM when you have the floor: state your position and answer what the others said.",
               use_when="You received 'YOU HAVE THE FLOOR' (or a vote) for a decision room.",
               avoid="Not when someone else has the floor: wait for your turn.",
               returns="your position, and the result if the room closed with your turn.",
               example="decision_say(session_id='S-7K2P', room_id='R-4KQ2M', position='PostgreSQL', text='I agree with QA Engineer A that we need real transactions; SQLite locks the whole file on every write.')")
    def decision_say(session_id: SID, room_id: str,
                     position: Annotated[str, Field(description="One of the room's options, exactly as written. You may change it when you were convinced. "
                                                                "In a PLANNING room: 'proposal', 'draft' (the editor's whole plan), 'approve' or 'object'.")] = "",
                     text: Annotated[str, Field(description="Your argument. In a discussion: name whose argument you agree with or answer. In a planning room: "
                                                            "your proposal, the whole plan (editor), or your review.")] = "",
                     nothing_new: Annotated[bool, Field(description="True = you keep your position and have nothing to add.")] = False) -> Reply:
        out = _svc().rooms.say(room_id, call().role, position, text, nothing_new)
        return Reply(out, next=["The room is closed: act on the result." if out["status"] != "open" else "Wait: you are told when you have the floor again or when the room closes."])

    @book.tool(name="change_propose", dedupe_seconds=60,
               summary="Maintainer only: propose a change to the EmaraAI Hub itself. The owner approves it; the hub applies it with a backup and can revert it.",
               use_when="You found the cause of a problem in the platform, or an improvement worth making.",
               avoid="Never edit the hub's files yourself. Not for the work of a project.",
               returns="proposal_id and its status (proposed). You are told when the owner decides.",
               example="change_propose(session_id='S-7K2P', title='Wake a chat whose prompt was lost', why='errors.jsonl shows ... because ...; verify by ...', kind='fix', "
                       "edits=[{'path': 'src/emaraai_hub/supervisor/engine.py', 'find': '<exact text, once in the file>', 'replace': '<new text>'}])")
    def change_propose(session_id: SID, title: Annotated[str, Field(description="What changes, in a few words.")],
                       why: Annotated[str, Field(description="What is wrong now, the cause you found (log lines, code), how to verify the change, and whether a restart is needed.")],
                       kind: Annotated[Literal["fix", "enhancement", "idea"], Field(description="idea = described only, no edits.")] = "fix",
                       edits: Annotated[list[dict], Field(description="Each: {'path', 'find', 'replace'} (find = text that is in the file exactly once) "
                                                                      "or {'path', 'content'} for a whole new file. Paths inside the hub's folder.")] = [],
                       source: Annotated[str, Field(description="A link, when the idea comes from a project or article.")] = "") -> Reply:
        p = _svc().maintenance.propose(call().role, title=title, why=why, kind=kind, edits=edits, source=source)
        return Reply({"proposal_id": p["id"], "status": p["status"], "files": [e["path"] for e in p["edits"]]},
                     next=["The owner decides on the Diagnostics page. Go on with your check; do not make the change yourself."])

    @book.tool(name="chat_full", counts_for_checkpoint=False, allowed_when_checkpoint_due=True, dedupe_seconds=60,
               summary="Say that THIS chat is too long to go on (context full, replies cut off). The hub continues your role in a fresh chat.",
               use_when="Only when the conversation really reached its length limit and you can no longer work reliably here.",
               avoid="Not for ordinary blockers or errors: those are fixed in this same chat.",
               returns="Confirmation. A fresh chat takes over with your memory. End your reply and call no more tools.",
               example="chat_full(session_id='S-7K2P', summary='API routes 1-6 migrated, tests green.', next_steps=['Migrate routes 7-9', 'Run npm test'])")
    def chat_full(session_id: SID,
                  summary: Annotated[str, Field(description="Exact state: what is done and verified, what is in progress.")],
                  next_steps: Annotated[list[str], Field(description="Ordered concrete next actions for the fresh chat.")]) -> Reply:
        c = call()
        svc = _svc()
        svc.memory.checkpoint(c.session, summary=summary, next_steps=next_steps, open_questions=[], files_touched=[])
        new = svc.sessions.begin_handoff(c.session["id"], "the chat reported that it is full", svc.memory)
        return Reply({"continues_in": new["id"]}, next=["A fresh chat continues your work from this checkpoint. End your reply now; call no more tools."])

    @book.tool(name="chat_pause", counts_for_checkpoint=False, allowed_when_checkpoint_due=True,
               summary="Tell the hub you are deliberately waiting (for replies/agents), so it will not nudge you to continue.",
               use_when="As the LAST call of a reply when you have nothing to do until a message arrives.",
               avoid="Do not use when you still have work; the hub then sends 'continue' prompts automatically.",
               returns="The call may stay open up to about a minute. If a message arrives meanwhile it is returned here "
                       "(paused=false, messages[]): work on it in this same reply. Otherwise paused=true: end your reply; "
                       "the hub wakes this chat when a message arrives.",
               example="chat_pause(session_id='S-7K2P', reason='waiting for backend report on T-4KQ2M')")
    async def chat_pause(session_id: SID, reason: Annotated[str, Field(description="What you are waiting for.")] = "waiting for messages") -> Reply:
        c = call()
        svc = _svc()
        hold = float(getattr(c.hub.settings.lifecycle, "pause_hold_seconds", 0) or 0)
        if hold > 0 and not svc.inbox.waking_unread(c.role["id"]):
            svc.inbox.hold(session_id, hold)
            try:
                await svc.inbox.wait_waking(c.role["id"], hold)
            finally:
                svc.inbox.release(session_id)
        if svc.inbox.waking_unread(c.role["id"]):
            # mail is here (or arrived while held): hand it over in this reply instead of typing a wake prompt into a tab.
            # It counts as received only once the chat calls another tool (at-least-once ack, see InboxService.ack).
            out = svc.inbox.read(c.role["id"], session_id, limit=10)
            nxt = ["New messages arrived while you waited. Work on them now in this same reply; call chat_pause again when you wait."]
            if out["remaining_unread"]:
                nxt.append("More unread messages remain: call inbox_read.")
            return Reply({"paused": False, **out}, next=nxt)
        svc.sessions.set_waiting(session_id, reason)
        return Reply({"paused": True}, next=["End your reply now. You will be woken when something arrives."])

    # ------------------------------------------------------------------ inbox
    @book.tool(name="plan_get", readonly=True, allowed_when_checkpoint_due=True,
               summary="Read the project plan: overview, architecture and the steps with their states.",
               use_when="Before starting work, and whenever you need the design or want to see what is done.",
               returns="overview, architecture, steps[] (P1, P2 ... with status), done/total.",
               example="plan_get(session_id='S-7K2P')")
    def plan_get(session_id: SID) -> Reply:
        plan = _svc().plan.get(call().session["project_id"])
        if not plan["exists"]:
            return Reply({"exists": False}, next=["There is no plan yet. Master: write it with plan_save."])
        return Reply({k: plan[k] for k in ("overview", "architecture", "steps", "done", "total", "percent")})

    @book.tool(name="inbox_read", readonly=False, allowed_when_checkpoint_due=True,
               summary="Read your unread messages (tasks, questions, answers, reports). Marks them as read.",
               use_when="When notified about unread messages, after session_start, or after waking up.",
               returns="messages[] with id, from, kind, text, task_id; remaining_unread.",
               example="inbox_read(session_id='S-7K2P')")
    def inbox_read(session_id: SID, limit: Annotated[int, Field(ge=1, le=30, description="Max messages to return.")] = 10) -> Reply:
        c = call()
        out = _svc().inbox.read(c.role["id"], session_id, limit=limit)
        nxt = []
        if out["remaining_unread"]:
            nxt.append("More unread messages remain: call inbox_read again.")
        kinds = {m["kind"] for m in out["messages"]}
        if "task" in kinds and not is_master_plugin:
            nxt.append("Start the task with task_start(task_id=...).")
        if "report" in kinds and is_master_plugin:
            nxt.append("Review reports with task_review.")
        if any(m.get("needs_reply") for m in out["messages"]):
            nxt.append("Some messages need a reply: message_send(to=<from>, text=..., reply_to=<id>).")
        if not out["messages"]:
            nxt.append("Inbox empty. Continue your work or call chat_pause if you are waiting.")
        return Reply(out, next=nxt)

    @book.tool(name="inbox_wait", readonly=True, allowed_when_checkpoint_due=True, counts_for_checkpoint=False,
               summary="Wait up to 45 seconds for a new message to arrive, then return.",
               use_when="You expect a reply very soon and want to keep working in this same reply.",
               avoid="Do not loop on it for minutes. For long waits use chat_pause and end your reply.",
               returns="unread count. If > 0 call inbox_read.",
               example="inbox_wait(session_id='S-7K2P', timeout_seconds=40)")
    async def inbox_wait(session_id: SID, timeout_seconds: Annotated[int, Field(ge=5, le=45)] = 40) -> Reply:
        n = await _svc().inbox.wait(call().role["id"], timeout_seconds)
        return Reply({"unread": n}, next=["Call inbox_read." if n else "Nothing arrived. Call chat_pause and end your reply, or continue other work."])

    @book.tool(name="message_send", dedupe_seconds=120,
               summary="Send a message to another role (master or an agent) in this project.",
               use_when="To answer a question, share information, or reply to a message.",
               avoid="Do not use for task results (use task_report) or to create work (master: use task_assign).",
               returns="message_id.",
               example="message_send(session_id='S-7K2P', to='backend', text='Use port 8080.', reply_to='M-8TM55E')")
    def message_send(session_id: SID,
                     to: Annotated[str, Field(description="Role name: 'master' or an agent name. The master may also write to='owner' (the user).")],
                     text: Annotated[str, Field(description="The message. Be specific and self-contained.")],
                     kind: Annotated[Literal["note", "question", "answer"], Field(description="question = you need a reply.")] = "note",
                     reply_to: Annotated[str, Field(description="Message id you answer (M-XXXXXX), or empty.")] = "",
                     task_id: Annotated[str, Field(description="Related task id (T-XXXXX), or empty.")] = "",
                     files: Annotated[list[str], Field(description="Files to attach: full paths on the PC (images, documents) or file ids (F-XXXXXXXX) from a message you received. The receiver sees images in its chat. Empty list = none.")] = []) -> Reply:
        c = call()
        if to in ("owner", "master"):
            _plain(text)
        res = _svc().inbox.send(c.session["project_id"], from_role_id=c.role["id"], to=to, body=text, kind=kind,
                                reply_to=reply_to or None, task_id=task_id or None, needs_reply=kind == "question", files=files)
        nxt = ["If you now wait for the reply, call chat_pause and end your reply."] if kind == "question" else []
        return Reply(res, next=nxt)

    # ------------------------------------------------------------------ memory
    @book.tool(name="memory_checkpoint", counts_for_checkpoint=False, allowed_when_checkpoint_due=True, dedupe_seconds=60,
               summary="Save a snapshot of your progress so any future chat can continue exactly where you are. REQUIRED regularly.",
               use_when="After finishing a step, every ~10 tool calls, before ending a long reply, and when the hub asks.",
               returns="memory_id. Your checkpoint counter resets.",
               example="memory_checkpoint(session_id='S-7K2P', summary='Login API done, tests green.', next_steps=['Add refresh tokens'])")
    def memory_checkpoint(session_id: SID,
                          summary: Annotated[str, Field(description="Current state: what is done, what works, what is in progress.")],
                          next_steps: Annotated[list[str], Field(description="Ordered concrete next actions.")],
                          open_questions: Annotated[list[str], Field(description="Unresolved questions, or empty list.")] = [],
                          files_touched: Annotated[list[str], Field(description="Important file paths you changed, or empty list.")] = []) -> Reply:
        res = _svc().memory.checkpoint(call().session, summary=summary, next_steps=next_steps,
                                       open_questions=open_questions, files_touched=files_touched)
        return Reply(res, next=["Continue with your next step."])

    @book.tool(name="memory_save", counts_for_checkpoint=False, allowed_when_checkpoint_due=True, dedupe_seconds=120,
               summary="Store one durable fact, decision, todo or lesson in project memory.",
               use_when="A decision is made, a fact is learned (paths, ports, versions), or a mistake must not be repeated.",
               avoid="Not for progress snapshots (use memory_checkpoint) and not for messages (use message_send).",
               returns="memory_id.",
               example="memory_save(session_id='S-7K2P', kind='decision', title='DB', content='Use SQLite in data/app.db')")
    def memory_save(session_id: SID,
                    kind: Annotated[Literal["decision", "fact", "todo", "lesson"], Field(description="Type of entry.")],
                    title: Annotated[str, Field(description="Short label (2-6 words).")],
                    content: Annotated[str, Field(description="The full fact/decision. Self-contained.")],
                    scope: Annotated[Literal["project", "role"], Field(description="project = everyone sees it; role = only your role.")] = "project",
                    pinned: Annotated[bool, Field(description="true = always shown at session start. Use rarely.")] = False) -> Reply:
        return Reply(_svc().memory.save(call().session, kind=kind, title=title, content=content, scope=scope, pinned=pinned))

    @book.tool(name="memory_search", readonly=True, counts_for_checkpoint=False, allowed_when_checkpoint_due=True,
               summary="Search project memory (decisions, facts, lessons, checkpoints, handoffs) by keywords.",
               use_when="Before deciding something that may already be decided, or when you lack context.",
               returns="entries[] with memory_id, kind, title, content.",
               example="memory_search(session_id='S-7K2P', query='database port')")
    def memory_search(session_id: SID, query: Annotated[str, Field(description="Keywords. Empty = latest entries.")] = "",
                      limit: Annotated[int, Field(ge=1, le=20)] = 8) -> Reply:
        c = call()
        return Reply({"entries": _svc().memory.search(c.session["project_id"], query, limit, role_id=c.role["id"])})

    @book.tool(name="memory_reload", readonly=True, counts_for_checkpoint=False, allowed_when_checkpoint_due=True,
               summary="Get the full project memory again (same as at session start).",
               use_when="You are confused about the project state or after a long pause.",
               returns="memory text.", example="memory_reload(session_id='S-7K2P')")
    def memory_reload(session_id: SID) -> Reply:
        return Reply({"memory": _svc().memory.boot_packet(call().session)})

    # ------------------------------------------------------------------ master-only
    if is_master_plugin:
        @book.tool(name="project_create", needs_session=False, dedupe_seconds=300,
                   summary="Create a new project (with its master role and pinned brief).",
                   use_when="The user starts something new that has no project yet.",
                   returns="project. Then call session_start(role='master', project=<name>).",
                   example="project_create(name='shop', goal='Online shop with FastAPI + React', constraints='Windows, Python 3.11')")
        def project_create(name: Annotated[str, Field(description="Short unique name, e.g. 'shop'.")],
                           goal: Annotated[str, Field(description="What 'done' looks like, 1-5 sentences.")],
                           constraints: Annotated[str, Field(description="Hard rules: stack, OS, deadlines. Or empty.")] = "") -> Reply:
            p = _svc().projects.create(name, goal, constraints=constraints, actor="master")
            return Reply({"project": p["name"], "status": p["status"]}, next=[f"Call session_start(role='master', project='{p['name']}')."])

        @book.tool(name="project_list", needs_session=False, readonly=True,
                   summary="List projects and their status.", use_when="You do not know the project name.",
                   returns="projects[] with name, status, goal.", example="project_list()")
        def project_list() -> Reply:
            return Reply({"projects": [{"name": p["name"], "status": p["status"], "goal": p["goal"][:200]} for p in _svc().projects.list()]})

        @book.tool(name="project_status", readonly=True,
                   summary="Dashboard of the project: agents and their chats, task counts, unread messages, recent events.",
                   use_when="To decide what to do next, or before answering the user about progress.",
                   returns="agents[], tasks counts, open tasks, recent events.",
                   example="project_status(session_id='S-7K2P')")
        def project_status(session_id: SID) -> Reply:
            return Reply(project_snapshot(_hub(), call().session["project_id"]))

        @book.tool(name="project_set_status", dedupe_seconds=60,
                   summary="Change project status: active, paused (hub stops nudging chats) or done (finished).",
                   use_when="The user pauses the project or all acceptance criteria are met.",
                   returns="new status.", example="project_set_status(session_id='S-7K2P', status='done', reason='All tasks accepted')")
        def project_set_status(session_id: SID, status: Literal["active", "paused", "done"],
                               reason: Annotated[str, Field(description="Why.")] = "") -> Reply:
            _require_master()
            p = _svc().projects.set_status(call().session["project_id"], status, reason=reason, actor="master")
            return Reply({"project": p["name"], "status": p["status"]})

        @book.tool(name="agent_create", dedupe_seconds=300,
                   summary="Add one more agent to the team. The hub opens a separate ChatGPT chat for it when it has work.",
                   use_when="After plan_save, when the work needs more hands or a speciality the team does not have yet.",
                   avoid="Do not create duplicates; check agent_list first.",
                   returns="agent name. Then give it work with task_assign.",
                   example="agent_create(session_id='S-7K2P', name='Software Engineer B', title='Frontend pages', "
                           "instructions='Owns everything under /public: HTML, CSS, JS. Follows the architecture in the plan. Never edits /api. "
                           "Proves each page with a screenshot.')")
        def agent_create(session_id: SID,
                         name: Annotated[str, Field(description="Job title plus a letter: 'Software Engineer A', 'QA Engineer B'. "
                                                                "Same field again = next letter.")],
                         title: Annotated[str, Field(description="Its speciality in this project, e.g. 'Backend API and database'.")],
                         instructions: Annotated[str, Field(description="A full description: what it owns, how it works, what it must not "
                                                                        "touch, how it proves its work.")],
                         skills: Annotated[list[str], Field(description="Capability tags, e.g. ['python','tests'].")] = [],
                         ai: Annotated[Literal["", "chatgpt", "claude_web", "gemini_web", "claude", "gemini", "openrouter", "custom"],
                                       Field(description="Which AI runs it. Empty = the owner's default. Only choose one the owner asked for.")] = "",
                         model: Annotated[str, Field(description="Model name for that AI, or empty for its default.")] = "",
                         mode: Annotated[Literal["", "chat", "work", "code"],
                                         Field(description="Browser chats only. ChatGPT: chat or work. Claude: chat or code. Empty = the owner's default. Only choose one the owner asked for.")] = "") -> Reply:
            _require_master()
            r = _svc().projects.create_agent(call().session["project_id"], name, title, instructions, skills, actor="master")
            if ai or model or mode:
                r = _svc().agents.apply_identity(r["id"], {"ai": ai, "model": model, **({"mode": mode} if mode else {})}, by="master")      # effort: the owner sets it on the agent's profile
            who = r["display"] or r["name"]
            hint = [f"{repo}:{skill}" for repo, skill in _svc().skills.recommended_for(r)]
            return Reply({"agent": who, "title": r["title"], "ai": r.get("provider") or "default"},
                         next=[f"Assign work: task_assign(agent='{who}', ...)."] + ([f"It gets these skills by itself: {', '.join(hint)}. Add others with skill_assign."] if hint else []))

        @book.tool(name="skill_search", readonly=True,
                   summary="Find skills (how-to guides from Vercel, Anthropic, Superpowers and other collections) for a kind of work.",
                   use_when="You hire someone or give out work that has known good practice: UI design, React, testing, debugging, writing.",
                   returns="skills[] with ref, name, description and whether it is already installed.",
                   example="skill_search(session_id='S-7K2P', query='web design accessibility')")
        def skill_search(session_id: SID, query: Annotated[str, Field(description="A few words about the work.")]) -> Reply:
            found = _svc().skills.search(query, 15)
            return Reply({"skills": found}, next=["Give one to an agent with skill_assign(agent=..., skills=['<ref>'])."] if found else
                         ["Nothing matched. The owner can add more sources on the Skills page."])

        @book.tool(name="skill_assign", dedupe_seconds=20,
                   summary="Give skills to an agent: they are put into its first message and it can read them in full with skill_read.",
                   use_when="An agent's job has a matching skill (see skill_search), e.g. a UI designer and web-design-guidelines.",
                   avoid="Do not hand out skills that do not fit the job: every skill costs the agent attention.",
                   returns="the agent's skills.",
                   example="skill_assign(session_id='S-7K2P', agent='UI Designer A', skills=['vercel-labs/agent-skills:web-design-guidelines'])")
        async def skill_assign(session_id: SID, agent: Annotated[str, Field(description="Agent name from agent_list.")],
                               skills: Annotated[list[str], Field(description="Skill refs from skill_search (owner/repo:skill) or names of installed skills.")]) -> Reply:
            _require_master()
            svc = _svc()
            role = svc.projects.role(call().session["project_id"], agent)
            ids = []
            for ref in skills:
                s = svc.skills.find(ref) or await svc.skills.install(ref)
                ids.append(s["id"])
            have = svc.skills.assign(role["id"], ids, by="master")
            return Reply({"agent": role["display"] or role["name"], "skills": [{"name": s["name"], "description": s["description"][:160]} for s in have]})

        @book.tool(name="agent_manage", dedupe_seconds=20,
                   summary="Change an agent's place or runtime: promote, reassign, suspend, restore or archive. Identity and memory are never lost.",
                   use_when="The team must change: somebody becomes a lead, moves to another team/manager, is not needed for now, or is retired.",
                   avoid="Not for giving work (task_assign) and not for adding people (agent_create).",
                   returns="the agent with its new state.",
                   example="agent_manage(session_id='S-7K2P', name='Software Engineer A', action='promote', level='lead', seniority='senior')")
        def agent_manage(session_id: SID, name: Annotated[str, Field(description="Agent name, e.g. 'Software Engineer A'.")],
                         action: Literal["promote", "reassign", "suspend", "restore", "archive"],
                         level: Annotated[Literal["", "lead", "specialist", "worker"], Field(description="promote: the new level.")] = "",
                         seniority: Annotated[Literal["", "junior", "mid", "senior", "staff", "principal"], Field(description="promote: new seniority.")] = "",
                         manager: Annotated[str, Field(description="reassign: its new manager (a lead's name or 'master').")] = "",
                         reason: Annotated[str, Field(description="reassign: the new team name. suspend/archive: why.")] = "") -> Reply:
            _require_master()
            svc, pid = _svc(), call().session["project_id"]
            if action == "promote":
                r = svc.agents.promote(pid, name, level=level, seniority=seniority, by="master")
            elif action == "reassign":
                r = svc.agents.reassign(pid, name, team=reason or None, manager=manager or None, by="master")
            elif action == "suspend":
                r = svc.agents.suspend(pid, name, reason=reason, by="master")
            elif action == "restore":
                r = svc.agents.restore(pid, name, by="master")
            else:
                r = svc.agents.archive(pid, name, reason=reason, by="master")
            c = svc.agents.card(r)
            return Reply({k: c[k] for k in ("display", "person_name", "level", "seniority", "team", "manager_display", "state", "state_why")})

        @book.tool(name="client_decide", dedupe_seconds=30,
                   summary="Decide a question the client did not answer in time (yours or a lead's), so the team can go on.",
                   use_when="The hub told you a question to the client was not answered.",
                   avoid="Not while the client still has time, and not after the client answered.",
                   returns="the recorded decision. A lead who asked is told automatically.",
                   example="client_decide(session_id='S-7K2P', question_id='Q-1A2B3C', decision='Number invoices per year.', reasoning='Safer for accounting; easy to change.')")
        def client_decide(session_id: SID, question_id: str,
                          decision: Annotated[str, Field(description="What the team will do.")],
                          reasoning: Annotated[str, Field(description="Why, in one or two sentences (and whose advice you took).")] = "") -> Reply:
            _require_master()
            c = call()
            q = _svc().agents.decide_for_client(c.role, question_id, decision, reasoning)
            _svc().memory.save(c.session, kind="decision", title=f"Decided without the client: {q['question'][:80]}",
                               content=f"Question {q['id']} from {q['from']}: {q['question']}\nDecision: {q['answer']}")
            return Reply({"question_id": q["id"], "status": q["status"], "asked_by": q["from"]})

        @book.tool(name="agent_list", readonly=True,
                   summary="List agents with their chat state and open task count.", use_when="Before assigning work.",
                   returns="agents[].", example="agent_list(session_id='S-7K2P')")
        def agent_list(session_id: SID) -> Reply:
            return Reply({"agents": project_snapshot(_hub(), call().session["project_id"])["agents"]})

        @book.tool(name="agent_update", dedupe_seconds=60,
                   summary="Change an agent's instructions/title or enable/disable it.",
                   use_when="An agent needs new standing rules, or must be stopped.",
                   returns="updated agent.", example="agent_update(session_id='S-7K2P', name='backend', instructions='Also own the DB schema')")
        def agent_update(session_id: SID, name: str,
                         instructions: Annotated[str, Field(description="New full instructions, or empty to keep.")] = "",
                         title: Annotated[str, Field(description="New title, or empty to keep.")] = "",
                         enabled: Annotated[Literal["keep", "enable", "disable"], Field(description="Enable/disable the agent.")] = "keep") -> Reply:
            _require_master()
            svc, pid = _svc(), call().session["project_id"]
            r = svc.projects.update_agent(pid, name, instructions=instructions or None, title=title or None)
            nxt = []
            if enabled == "disable" and r["state"] == "active":
                # switched off = suspended: everyone sees it (Team page, Communication), and mail for it waits with the reason shown
                r = svc.agents.suspend(pid, name, reason="switched off by the Master", by="master")
                nxt = ["It is suspended: its chats are closed and nothing is delivered to it. Messages sent to it wait until agent_update(enabled='enable')."]
            elif enabled == "enable" and r["state"] != "active":
                r = svc.agents.restore(pid, name, by="master")
            return Reply({"agent": r["name"], "title": r["title"], "enabled": bool(r["enabled"]), "state": r["state"]}, next=nxt)

        @book.tool(name="agent_open_chat", dedupe_seconds=120,
                   summary="Ask the hub to open (or re-open) the ChatGPT chat of an agent now.",
                   use_when="An agent has no live chat and you want it to start immediately (normally automatic).",
                   returns="pending session and its join code.",
                   example="agent_open_chat(session_id='S-7K2P', name='backend')")
        def agent_open_chat(session_id: SID, name: str,
                            fresh: Annotated[bool, Field(description="true = the agent's chat is full or unusable: continue it in a NEW chat "
                                                                     "with its memory. false = only open a chat if it has none.")] = False) -> Reply:
            _require_master()
            svc = _svc()
            role = svc.projects.role(call().session["project_id"], name)
            live = svc.sessions.active_for_role(role["id"])
            if live and fresh:
                new = svc.sessions.begin_handoff(live["id"], "master asked for a fresh chat (the old one is full)", svc.memory)
                return Reply({"agent": role["display"] or role["name"], "old_chat": live["id"], "fresh_chat": new["id"]},
                             next=["The hub opens the fresh chat and hands the agent's memory over. Then tell it what to continue (message_send)."])
            if live:
                return Reply({"agent": role["name"], "already_live": live["id"]}, notices=["That agent already has a live chat."])
            s = svc.sessions.request_chat(role["project_id"], role["id"], reason="master request")
            return Reply({"agent": role["name"], "pending_session": s["id"], "join_code": s["join_code"]},
                         next=["The hub opens the chat automatically (or shows it on the dashboard if manual mode)."])

        @book.tool(name="task_assign", dedupe_seconds=300,
                   summary="Create a task for an agent. It lands in the agent's inbox; the hub wakes or opens the agent chat.",
                   use_when="You have a concrete piece of work for one agent.",
                   avoid="Do not assign vague tasks. One task = one deliverable.",
                   returns="task_id. The agent will report back to your inbox.",
                   example="task_assign(session_id='S-7K2P', agent='backend', title='Login API', instructions='POST /login with JWT...', done_when=['pytest passes'])")
        def task_assign(session_id: SID,
                        agent: Annotated[str, Field(description="Agent name from agent_list.")],
                        title: Annotated[str, Field(description="Short title.")],
                        instructions: Annotated[str, Field(description="Everything the agent needs: context, files, constraints.")],
                        done_when: Annotated[list[str], Field(description="Acceptance criteria the agent must meet.")] = [],
                        priority: Annotated[int, Field(ge=1, le=5, description="1 = urgent, 5 = low.")] = 3,
                        check: Annotated[Literal["auto", "user_facing", "verify", "none"], Field(description="user_facing = something people press, run or call "
                                         "(UI, command, API): every entry point must be tried and an independent checker repeats it. verify = independent check only. "
                                         "auto = the hub decides.")] = "auto",
                        files: Annotated[list[str], Field(description="Files to attach: full paths on the PC (images, documents) or file ids (F-XXXXXXXX) from a message you received. The receiver sees images in its chat. Empty list = none.")] = []) -> Reply:
            _require_master()
            c = call()
            svc = _svc()
            if call().hub.settings.tools.require_plan and not svc.plan.exists(c.session["project_id"]):
                raise HubError("Design the project before giving out work: there is no plan yet.", code="plan_required",
                               fix="Call plan_save(overview=..., architecture=..., steps=[{'title':..., 'details':..., 'agent':...}, ...]) "
                                   "with the complete plan and architecture. Then assign tasks, starting each title with its step id (P1, P2 ...).")
            t = svc.tasks.assign(c.session["project_id"], by_role=c.role, agent=agent, title=title,
                                 instructions=instructions, acceptance=done_when, priority=priority, files=files, check=check)
            step = svc.plan.link_task(c.session["project_id"], t)
            low = svc.quality.low(t["assigned_role_id"])
            return Reply({"task_id": t["id"], "agent": agent, "status": t["status"], **({"plan_step": step} if step else {})},
                         notices=[f"{agent} has a low quality score: this work will be checked independently before you can accept it."] if low else [],
                         next=["Assign other independent tasks now (put several task_assign steps in one hub_batch), "
                               "or call chat_pause and end your reply; reports arrive in your inbox."])

        @book.tool(name="task_list", readonly=True,
                   summary="List tasks, filtered by status and/or agent.", use_when="To see progress or find a task id.",
                   returns="tasks[] with task_id, title, status, agent, progress.",
                   example="task_list(session_id='S-7K2P', status='open')")
        def task_list(session_id: SID,
                      status: Literal["open", "all", "pending", "in_progress", "blocked", "review", "done", "failed", "cancelled"] = "open",
                      agent: Annotated[str, Field(description="Filter by agent name, or empty.")] = "") -> Reply:
            svc = _svc()
            rows = svc.tasks.list(call().session["project_id"], status=status, agent=agent)
            return Reply({"tasks": [svc.tasks.brief(t) for t in rows]})

        @book.tool(name="task_get", readonly=True,
                   summary="Full details of one task: instructions, criteria, report, files and its message thread.",
                   use_when="Before reviewing a report or answering about a task.",
                   returns="task details.", example="task_get(session_id='S-7K2P', task_id='T-4KQ2M')")
        def task_get(session_id: SID, task_id: str) -> Reply:
            svc = _svc()
            return Reply(svc.tasks.full(svc.tasks.get(task_id, call().session["project_id"])))

        @book.tool(name="plan_save", dedupe_seconds=30,
                   summary="Save the project design: overview, architecture and the ordered steps. REQUIRED before the first task_assign.",
                   use_when="In your FIRST reply of a project (design everything first), and whenever the design changes.",
                   avoid="Do not save a thin plan: the hub rejects a short architecture or fewer than 4 steps.",
                   returns="the saved plan with step ids P1, P2 ... Start each task title with its step id.",
                   example="plan_save(session_id='S-7K2P', overview='A booking site for ...', architecture='Frontend: ... Backend: ... Data: ...', "
                           "team=[{'name':'Software Engineer A','field':'Backend API','responsibilities':'Owns /api ...','skills':['python']}], "
                           "steps=[{'title':'Data model','details':'tables ...','agent':'Software Engineer A'}])")
        def plan_save(session_id: SID,
                      overview: Annotated[str, Field(description="Goal, users, scope, what is out of scope, main risks.")],
                      architecture: Annotated[str, Field(description="Components, data model, technologies, folder structure, how parts talk, "
                                                                     "how it is tested and run. Be complete.")],
                      steps: Annotated[list[dict], Field(description="Ordered steps. Each: {'title': str, 'details': str, 'agent': str}. "
                                                                     "'agent' is a name from team. One step = one task for one agent.")],
                      team: Annotated[list[dict], Field(description="The team and hierarchy YOU design. Each member: {'name': 'Software "
                                    "Engineer A' (job title + letter), 'person': 'a realistic full name', 'level': 'lead'|'specialist'|'worker', "
                                    "'manager': name of its lead or 'master', 'team': str, 'career': str, 'seniority': 'junior'|'mid'|'senior'|"
                                    "'staff'|'principal', 'field': str, 'responsibilities': str, 'skills': [str], 'personality': str}. "
                                    "Small project: a few specialists under you. Big project: leads under you, specialists and workers "
                                    "under the leads.")] = []) -> Reply:
            _require_master()
            c = call()
            svc, pid = _svc(), c.session["project_id"]
            named = svc.settings.tools.team_names
            members, taken = [], set()
            for m in team or []:
                if not isinstance(m, dict) or not str(m.get("name", "")).strip():
                    raise InvalidInput("A team member has no name.", fix="Each team entry: {'name': 'Software Engineer A', 'field': '...', "
                                                                          "'responsibilities': '...', 'skills': ['...']}.")
                known = None
                try:
                    known = svc.projects.role(pid, str(m["name"]))
                except HubError:
                    pass
                raw = " ".join(str(m["name"]).split())
                if not known and named and not re.search(r" [A-Za-z]$", raw):
                    # the same job title without a letter, sent again: it means the members that already exist, in order
                    same = [r for r in svc.projects.roles(pid) if r["display"] and r["display"][:-2].lower() == raw.lower() and r["display"] not in taken]
                    known = same[0] if same else None
                display = (known["display"] or known["name"]) if known else (svc.projects.agent_name(pid, str(m["name"]), taken) if named else str(m["name"]))
                taken.add(display)
                members.append((display, known, m))
            existing = [r for r in svc.projects.roles(pid) if r["kind"] != "master"]
            if named and not members and not existing:
                raise InvalidInput("The plan has no team.", code="team_required",
                                   fix="Decide who does the work and pass team=[{'name': 'Software Engineer A', 'field': ..., "
                                       "'responsibilities': ..., 'skills': [...]}, ...]. Use as many agents as the project needs, "
                                       "also several of the same field (A, B, C).")
            valid = {d.lower(): d for d, _, _ in members} | {(r["display"] or r["name"]).lower(): (r["display"] or r["name"]) for r in existing}
            valid |= {ids_slug(v): v for v in list(valid.values())} | {"master": "master"}
            fixed = []
            for st in steps or []:
                st = dict(st) if isinstance(st, dict) else st
                who = str(st.get("agent", "")).strip() if isinstance(st, dict) else ""
                if isinstance(st, dict) and who:
                    hit = valid.get(who.lower()) or valid.get(ids_slug(who))
                    if not hit and (members or existing):
                        raise InvalidInput(f"Step '{st.get('title', '')}' is given to '{who}', who is not in the team.", code="unknown_agent",
                                           fix="Use one of: " + ", ".join(sorted(set(valid.values()) - {"master"})) + ", or add that agent to team.")
                    st["agent"] = hit or who
                fixed.append(st)
            plan = svc.plan.save(pid, overview, architecture, fixed, by=c.role["name"])
            out_team, wanted, notices = [], [], []
            for display, known, m in members:        # only now: a rejected plan must not leave a half-built team behind
                desc, field = str(m.get("responsibilities", "")).strip(), str(m.get("field", "")).strip()
                skills = [str(x) for x in (m.get("skills") or [])][:12]
                if known:
                    svc.projects.update_agent(pid, known["name"], instructions=desc or None, title=field or None)
                    made = known
                else:
                    made = svc.projects.create_agent(pid, display, field, desc, skills, actor="master")
                wanted.append((made, m))
                out_team.append(display)
            for made, m in wanted:               # identity and hierarchy, once everybody exists (a manager may be listed later)
                ident = {k: m.get(k) for k in ("career", "seniority", "personality", "team", "level") if m.get(k)}
                if m.get("person"):
                    ident["person_name"] = m["person"]
                if m.get("manager"):
                    ident["manager"] = m["manager"]
                try:
                    svc.agents.apply_identity(made["id"], ident, by="master")
                except HubError as e:
                    notices.append(f"{made['display'] or made['name']}: {e.message} {e.fix or ''}".strip())
            return Reply({"team": out_team or [r["display"] or r["name"] for r in existing],
                          "steps": [{"step": s["step"], "title": s["title"], "agent": s["agent"], "status": s["status"]} for s in plan["steps"]],
                          "version": plan["version"]},
                         notices=notices,
                         next=["The team exists now. Assign the first steps with task_assign(agent='<name from team>', ...): start every task "
                               "title with its step id, e.g. 'P1 Data model'. The plan is checked off automatically. "
                               "Need more hands later? agent_create(name='<Job Title> <next letter>', ...)."])

        @book.tool(name="plan_update", dedupe_seconds=5,
                   summary="Change one plan step: its state, a note, or its text.",
                   use_when="A step is done without a task, must be skipped, needs rework, or its wording changed.",
                   avoid="Steps linked to a task follow that task by themselves; do not repeat that by hand.",
                   returns="the plan steps with their states.",
                   example="plan_update(session_id='S-7K2P', step='P3', status='done', note='Verified by the test run.')")
        def plan_update(session_id: SID, step: Annotated[str, Field(description="Step id: P1, P2 ...")],
                        status: Annotated[Literal["", "todo", "doing", "testing", "done", "rework", "blocked", "skipped"],
                                          Field(description="New state, or empty to keep it.")] = "",
                        note: Annotated[str, Field(description="Short note shown on the Plan page, or empty.")] = "",
                        title: Annotated[str, Field(description="New title, or empty to keep it.")] = "",
                        details: Annotated[str, Field(description="New details, or empty to keep them.")] = "") -> Reply:
            _require_master()
            c = call()
            plan = _svc().plan.update_step(c.session["project_id"], step, status=status, note=note or None, title=title or None,
                                           details=details or None, by=c.role["name"])
            return Reply({"steps": [{"step": s["step"], "title": s["title"], "status": s["status"]} for s in plan["steps"]],
                          "done": plan["done"], "total": plan["total"]})

        @book.tool(name="decision_room_open", dedupe_seconds=120,
                   summary="Open a DECISION ROOM: you and the people you choose discuss a question in turns and decide it together.",
                   use_when="A choice affects several people or the team disagrees, and it is worth a discussion.",
                   avoid="Not for what you can simply decide, and not for what only the owner may decide (ask_client).",
                   returns="room_id. The hub gives the floor to each member in turn; you speak with decision_say when it is your turn. The result becomes a project decision.",
                   example="decision_room_open(session_id='S-7K2P', question='Which database for the order service? We expect 50 writes a second.', "
                           "options=['PostgreSQL', 'SQLite'], people=['Software Engineer A', 'QA Engineer A'])")
        def decision_room_open(session_id: SID, question: Annotated[str, Field(description="One clear question with the context needed to judge it.")],
                               options: Annotated[list[str], Field(description="2-6 short names of what can be chosen. May be empty (or fewer) when allow_new_options=true.")],
                               people: Annotated[list[str], Field(description="1-8 team members who sit in the room with you.")],
                               how: Annotated[Literal["discuss", "vote", "plan"], Field(description="discuss = a real discussion in turns (default). vote = one answer each. "
                                                                                                "plan = no options: they propose, the editor writes ONE plan (architecture, design), "
                                                                                                "the others review it until it is agreed; ends with a report.")] = "discuss",
                               allow_new_options: Annotated[bool, Field(description="true = members may put a new option on the table instead of only choosing from options.")] = False,
                               editor: Annotated[str, Field(description="how='plan' only: who writes the plan (one of people). Empty = you.")] = "") -> Reply:
            _require_master()
            c = call()
            room = _svc().rooms.open(c.session["project_id"], question=question, options=options, people=people, how=how, by_role=c.role,
                                     allow_new=allow_new_options, editor=editor)
            return Reply({"room_id": room["id"], "members": len(room["seats"]), "how": room["how"]},
                         next=["Go on with other work or chat_pause. You get the floor like everyone else; speak then with decision_say."])

        @book.tool(name="decision_room_get", readonly=True,
                   summary="Read a decision room: positions, what was said, the result.", use_when="To see how a room stands or what it decided.",
                   returns="status, members with positions, the transcript, result and tally.", example="decision_room_get(session_id='S-7K2P', room_id='R-4KQ2M')")
        def decision_room_get(session_id: SID, room_id: str) -> Reply:
            _require_master()
            svc = _svc()
            return Reply(svc.rooms.view(svc.rooms.get(room_id, call().session["project_id"])))

        @book.tool(name="defect_report", dedupe_seconds=120,
                   summary="File a defect found in work that was ALREADY ACCEPTED. The task is reopened for its author; the author, whoever accepted it and "
                           "whoever checked it lose points.",
                   use_when="The owner or anyone finds that accepted work does not do what it should.",
                   avoid="Not for work still in review: send that back with task_review.",
                   returns="the reopened task and who was charged.",
                   example="defect_report(session_id='S-7K2P', task_id='T-4KQ2M', description='Clicking Repairs in the sidebar does nothing: the address and the page stay the same.')")
        def defect_report(session_id: SID, task_id: str,
                          description: Annotated[str, Field(description="What was done, what happened, what should have happened.")]) -> Reply:
            _require_master()
            c = call()
            out = _svc().quality.file_defect(task_id, description, by=c.role.get("display") or c.role["name"], by_role=c.role)
            return Reply(out, next=["The author was told and owes a fix with a lesson. Tighten the conditions (done_when) of similar tasks."])

        @book.tool(name="task_review", dedupe_seconds=120,
                   summary="Decide on an agent's report: accept, request_changes (with feedback), or cancel the task.",
                   use_when="A task is in 'review' (agent reported done) or you want to cancel it.",
                   returns="new task status.",
                   example="task_review(session_id='S-7K2P', task_id='T-4KQ2M', decision='request_changes', feedback='Add input validation')")
        def task_review(session_id: SID, task_id: str, decision: Literal["accept", "request_changes", "cancel"],
                        feedback: Annotated[str, Field(description="Required for request_changes. What exactly must change.")] = "",
                        visual_analysis: Annotated[str, Field(description="REQUIRED for visual tasks (UI, website, graphics): what you see in the "
                                                                          "attached screenshot - layout, texts, colours, alignment, defects.")] = "",
                        confirmed: Annotated[list, Field(description="For decision='accept' on a task with conditions: one entry per condition, in order - "
                                                                     "how YOU checked it and what you saw (not a copy of the report).")] = []) -> Reply:
            _require_master()
            svc = _svc()
            t = svc.tasks.review(task_id, call().role, decision, feedback, visual_analysis, confirmed)
            nxt = []
            still = svc.tasks.list(t["project_id"], status="open", limit=12)
            svc.memory.auto_checkpoint(call().session, f"Reviewed {t['id']} ({t['title']}): {decision}." + (f" Feedback: {feedback.strip()[:500]}" if feedback.strip() else ""),
                                       [f"{x['id']} {x['title']} ({x['status']})" for x in still] or ["All tasks are closed: check the goal, then project_set_status(status='done')."])
            if decision == "accept":
                open_n = len(svc.tasks.list(t["project_id"], status="open"))
                nxt.append("All tasks are closed. If the goal is met call project_set_status(status='done')." if open_n == 0 else f"{open_n} task(s) still open.")
            return Reply(svc.tasks.brief(t), next=nxt)

        @book.tool(name="workflow_save", dedupe_seconds=20,
                   summary="Create or replace an automation (workflow) that the hub runs by itself: a trigger, then steps. The owner sees and "
                           "edits it as a diagram.",
                   use_when="Something should happen automatically: on an event (task.completed, task.blocked, project.done ...), on a schedule, "
                            "or on demand. Call workflow_nodes first to see every node type and its params.",
                   avoid="Not for one-off actions: just do them. Do not create a workflow that only repeats what you already do.",
                   returns="the saved workflow (id, nodes, edges). Saving the same name again replaces it.",
                   example="workflow_save(session_id='S-7K2P', name='Tell me about failed tasks', nodes=[{'id':'n1','type':'on_event','params':{'event':'task.failed'}},"
                           "{'id':'n2','type':'send_message','params':{'to':'master','text':'Task {{event.payload.task_id}} failed: {{event.payload.summary}}'}}], "
                           "edges=[{'from':'n1','to':'n2'}])")
        def workflow_save(session_id: SID, name: Annotated[str, Field(description="Short name that says what it does.")],
                          nodes: Annotated[list[dict], Field(description="Nodes: {id, type, params}. Exactly one trigger first. Types: see workflow_nodes.")],
                          edges: Annotated[list[dict], Field(description="Connections: {from, to}. From a 'condition' node add out='true' or 'false'.")],
                          description: Annotated[str, Field(description="One sentence for the owner.")] = "",
                          enabled: Annotated[bool, Field(description="false to save it switched off.")] = True) -> Reply:
            _require_master()
            w = _svc().workflows.save(name, nodes, edges, description=description, enabled=enabled, by="master")
            return Reply({"workflow_id": w["id"], "name": w["name"], "enabled": w["enabled"], "nodes": len(w["nodes"]), "triggers": w["triggers"]},
                         next=["Test it once with workflow_run(workflow=...) if it has a manual trigger, or tell the owner what it will do."])

        @book.tool(name="workflow_nodes", readonly=True,
                   summary="The node types a workflow can use, with their params, plus the existing workflows.",
                   use_when="Before workflow_save, and to see which automations already exist.",
                   returns="node_types[] (type, kind, params) and workflows[].", example="workflow_nodes(session_id='S-7K2P')")
        def workflow_nodes(session_id: SID) -> Reply:
            from ...services.workflows import catalog
            ws = _svc().workflows.list()
            return Reply({"node_types": [{"type": c["type"], "kind": c["kind"], "does": c["about"],
                                          "params": {p["key"]: (p["hint"] or p["label"]) + ("" if p["required"] else " (optional)") for p in c["params"]}} for c in catalog()],
                          "placeholders": "{{event.type}} {{event.project}} {{event.payload.<field>}} {{trigger.<field>}} {{last.<field>}} {{nodes.<id>.<field>}}",
                          "workflows": [{"id": w["id"], "name": w["name"], "enabled": w["enabled"], "triggers": w["triggers"], "last_status": w["last_status"]} for w in ws]})

        @book.tool(name="workflow_run", open_world=True, dedupe_seconds=20,
                   summary="Run a workflow now and get the result of every step.",
                   use_when="To test a workflow you saved, or to start one that has a manual trigger.",
                   returns="status and steps[] (each with its output or error).",
                   example="workflow_run(session_id='S-7K2P', workflow='Tell me about failed tasks', input={'note': 'test'})")
        async def workflow_run(session_id: SID, workflow: Annotated[str, Field(description="Workflow name or id (WF-...).")],
                               input: Annotated[dict[str, Any], Field(description="Data available as {{trigger.<field>}}.")] = {}) -> Reply:
            _require_master()
            data = dict(input)
            data.setdefault("project", _svc().projects.get(call().session["project_id"])["name"])
            return Reply(await _svc().workflows.run(workflow, trigger="master", data=data))

        @book.tool(name="n8n_list_workflows", readonly=True,
                   summary="List n8n automations you can trigger (notifications, deployments, reports...).",
                   use_when="Before n8n_run_workflow.", returns="workflows[] with name and description.",
                   example="n8n_list_workflows(session_id='S-7K2P')")
        def n8n_list_workflows(session_id: SID) -> Reply:
            return Reply({"workflows": _hub().n8n.list_workflows()})

        @book.tool(name="n8n_run_workflow", open_world=True, dedupe_seconds=60,
                   summary="Trigger a named n8n workflow with a JSON input.",
                   use_when="An automation exists for the job (see n8n_list_workflows).",
                   returns="n8n response (status and body).",
                   example="n8n_run_workflow(session_id='S-7K2P', workflow='notify_me', input={'text': 'Release ready'})")
        async def n8n_run_workflow(session_id: SID, workflow: str,
                                   input: Annotated[dict[str, Any], Field(description="JSON object passed to the workflow.")] = {}) -> Reply:
            _require_master()
            return Reply(await _hub().n8n.run_workflow(workflow, input, project_id=call().session["project_id"], actor="master"))

    # ------------------------------------------------------------------ agent-only
    else:
        def _my_people():
            c = call()
            team = [r for r in _svc().repos.roles.list(c.session["project_id"]) if _svc().tasks.manages(c.role, r) and r["enabled"]]
            if not team:
                raise PermissionDenied("Nobody reports to you, so this is not yours to do.", fix="Report your own work with task_report; ask your manager with ask_master.")
            return team

        @book.tool(name="task_review", dedupe_seconds=60,
                   summary="TEAM LEADS: decide on the report of someone who reports to you: accept, request_changes (with feedback) or cancel.",
                   use_when="A report from one of your people arrived in your inbox (the task is in 'review').",
                   avoid="Not for your own tasks: those you report with task_report.",
                   returns="new task status. Whoever gave the task is told when you accept it.",
                   example="task_review(session_id='S-7K2P', task_id='T-4KQ2M', decision='request_changes', feedback='Add input validation')")
        def task_review(session_id: SID, task_id: str, decision: Literal["accept", "request_changes", "cancel"],
                        feedback: Annotated[str, Field(description="Required for request_changes. What exactly must change.")] = "",
                        visual_analysis: Annotated[str, Field(description="Required for visual tasks: what you see in the attached screenshot.")] = "",
                        confirmed: Annotated[list, Field(description="For 'accept' on a task with conditions: per condition, how YOU checked it.")] = []) -> Reply:
            _my_people()
            svc = _svc()
            t = svc.tasks.review(task_id, call().role, decision, feedback, visual_analysis, confirmed)
            svc.memory.auto_checkpoint(call().session, f"Reviewed {t['id']} ({t['title']}): {decision}." + (f" Feedback: {feedback.strip()[:500]}" if feedback.strip() else ""))
            return Reply(svc.tasks.brief(t))

        @book.tool(name="task_assign", dedupe_seconds=60,
                   summary="TEAM LEADS: give a task to someone who reports to you. Their report comes back to you.",
                   use_when="Work in your area must be split up or passed on to one of your people.",
                   avoid="Only to people who report to you. Do not pass on a task as it is: give a clear, smaller piece.",
                   returns="task_id.",
                   example="task_assign(session_id='S-7K2P', agent='Backend Engineer B', title='Order API tests', instructions='Cover create, pay, cancel...', done_when=['npm test passes'])")
        def task_assign(session_id: SID, agent: Annotated[str, Field(description="One of the people who report to you.")],
                        title: Annotated[str, Field(description="Short title.")],
                        instructions: Annotated[str, Field(description="Everything they need: context, files, constraints.")],
                        done_when: Annotated[list[str], Field(description="Acceptance criteria.")] = [],
                        priority: Annotated[int, Field(ge=1, le=5, description="1 = urgent, 5 = low.")] = 3) -> Reply:
            team, c, svc = _my_people(), call(), _svc()
            target = svc.projects.role(c.session["project_id"], agent)
            if target["id"] not in [r["id"] for r in team]:
                raise PermissionDenied(f"{target['display'] or target['name']} does not report to you.",
                                       fix="Your people: " + ", ".join(r["display"] or r["name"] for r in team) + ". For anyone else ask your manager (ask_master).")
            t = svc.tasks.assign(c.session["project_id"], by_role=c.role, agent=agent, title=title, instructions=instructions, acceptance=done_when, priority=priority)
            return Reply({"task_id": t["id"], "agent": target["display"] or target["name"], "status": t["status"]}, next=["Their report arrives in your inbox. Call chat_pause when you wait."])

        @book.tool(name="task_list_mine", readonly=True, allowed_when_checkpoint_due=True,
                   summary="List YOUR open tasks (in progress first).", use_when="After session_start and after finishing a task.",
                   returns="tasks[] with full instructions and acceptance criteria.",
                   example="task_list_mine(session_id='S-7K2P')")
        def task_list_mine(session_id: SID) -> Reply:
            svc = _svc()
            rows = svc.tasks.mine(call().role)
            tasks = [{**svc.tasks.brief(t), "instructions": t["instructions"], "done_when": t["acceptance"], "review_note": t["review_note"]} for t in rows]
            nxt = ["Call task_start on the first pending task."] if rows else ["No open tasks. Call chat_pause and end your reply."]
            return Reply({"tasks": tasks}, next=nxt)

        @book.tool(name="task_start", dedupe_seconds=60,
                   summary="Mark a task as in progress (tell master you started).", use_when="Right before you begin working on a task.",
                   returns="task.", example="task_start(session_id='S-7K2P', task_id='T-4KQ2M')")
        def task_start(session_id: SID, task_id: str) -> Reply:
            svc = _svc()
            t = svc.tasks.start(task_id, call().role)
            return Reply(svc.tasks.brief(t), next=["Do the work. Use task_progress for long tasks and memory_checkpoint regularly."])

        @book.tool(name="task_progress", dedupe_seconds=30,
                   summary="Record progress on a long task (does not wake master).",
                   use_when="Every major step of a long task.", returns="task.",
                   example="task_progress(session_id='S-7K2P', task_id='T-4KQ2M', percent=50, note='API done, writing tests')")
        def task_progress(session_id: SID, task_id: str, percent: Annotated[int, Field(ge=0, le=100)],
                          note: Annotated[str, Field(description="One line on what is done.")] = "") -> Reply:
            svc = _svc()
            _plain(note, "progress note")
            return Reply(svc.tasks.brief(svc.tasks.progress(task_id, call().role, percent, note)))

        @book.tool(name="task_report", dedupe_seconds=300,
                   summary="Report the result of a task to master: done, failed, or blocked.",
                   use_when="The task is finished (done), impossible (failed), or you need master's input (blocked).",
                   avoid="Do not report 'done' before checking every done_when criterion.",
                   returns="task status. Master is notified automatically.",
                   example="task_report(session_id='S-7K2P', task_id='T-4KQ2M', outcome='done', summary='POST /login works, 12 tests pass', files=['api/auth.py'])")
        def task_report(session_id: SID, task_id: str, outcome: Literal["done", "failed", "blocked"],
                        summary: Annotated[str, Field(description="What you did and the result, 1-6 sentences.")],
                        details: Annotated[str, Field(description="Optional longer notes, logs, commands to verify.")] = "",
                        files: Annotated[list[str], Field(description="Paths of deliverables. For a visual task (UI, website, graphics) include "
                                                                      "at least one image of the result (png/jpg), e.g. from page_screenshot.")] = [],
                        lesson: Annotated[str, Field(description="Needed when this task was sent back: one general sentence on what you will do differently next time.")] = "",
                        proof: Annotated[dict, Field(description="For outcome='done': {'checks': [evidence for each done_when condition, in order], "
                                                                 "'entry_points': [{'name','tried','observed'} for everything a user can press/run/call - user-facing "
                                                                 "tasks], 'verdict': 'pass'|'fail' - only when you report an independent check}.")] = {}) -> Reply:
            svc = _svc()
            _plain(summary, "summary (put the technical part in details=)")
            t = svc.tasks.report(task_id, call().role, outcome, summary, details, files, lesson, proof)
            mine = svc.tasks.mine(call().role)
            svc.memory.auto_checkpoint(call().session, f"Reported {t['id']} ({t['title']}) as {outcome.upper()}: {summary.strip()[:900]}",
                                       [f"{x['id']} {x['title']} ({x['status']})" for x in mine if x["id"] != t["id"]] or
                                       ["Wait for the review of " + t["id"] if outcome == "done" else "Wait for the answer about " + t["id"]], files)
            nxt = ["Your progress is saved. Call task_list_mine for the next task." if len(mine) > 1 or outcome == "blocked"
                   else "Your progress is saved. Call chat_pause, then end your reply."]
            return Reply(svc.tasks.brief(t), next=nxt)

        @book.tool(name="ask_master", dedupe_seconds=120,
                   summary="Ask master a question and get the answer in your inbox.",
                   use_when="Requirements are unclear or you need a decision.",
                   returns="message_id. Then call chat_pause or inbox_wait.",
                   example="ask_master(session_id='S-7K2P', question='Postgres or SQLite?', task_id='T-4KQ2M')")
        def ask_master(session_id: SID, question: str, task_id: Annotated[str, Field(description="Related task id or empty.")] = "") -> Reply:
            c = call()
            boss = _svc().agents.manager_of(c.role)       # a real team: you ask your own manager (your lead), who may take it higher
            if boss and boss["kind"] != "master" and (boss["state"] != "active" or not boss["enabled"]):
                boss = None                                # the lead is suspended / archived: straight to the master
            _plain(question, "question")
            res = _svc().inbox.send(c.session["project_id"], from_role_id=c.role["id"], to=boss["name"] if boss else "master", body=question,
                                    kind="question", task_id=task_id or None, needs_reply=True, priority=2)
            return Reply(res, next=["Call inbox_wait (short wait) or chat_pause and end your reply."])

    return book


def project_snapshot(hub, project_id: str) -> dict:
    svc = hub.services
    p = svc.projects.get(project_id)
    agents = []
    for r in svc.projects.roles(project_id):
        live = svc.repos.sessions.live_for_role(r["id"])
        s = live[0] if live else None
        open_tasks = len(svc.repos.tasks.list(project_id, statuses=("pending", "in_progress", "blocked"), role_id=r["id"]))
        card = svc.agents.card(r, s)
        agents.append({**{k: card[k] for k in ("name", "display", "person_name", "kind", "title", "enabled", "skills", "level", "team", "manager",
                                                 "manager_display", "seniority", "state", "state_why", "tab_open", "parked")},
                       "instructions": r["instructions"][:600],
                       "chat": ({"session_id": s["id"], "status": s["status"], "chat_state": s["chat_state"],
                                 "waiting": bool(s["waiting_since"])} if s else None),
                       "open_tasks": open_tasks, "unread": svc.inbox.unread_count(r["id"])})
    tasks = svc.repos.tasks.list(project_id, statuses=("pending", "in_progress", "blocked", "review"), limit=20)
    events = svc.bus.recent(project_id=project_id, limit=10)
    return {"project": p["name"], "status": p["status"], "goal": p["goal"], "agents": agents,
            "task_counts": svc.repos.tasks.counts(project_id), "open_tasks": [svc.tasks.brief(t) for t in tasks],
            "recent_events": [{"type": e["type"], "actor": e["actor"], "at": svc.clock.iso(e["ts"]),
                               **{k: v for k, v in e["payload"].items() if k in ("task_id", "agent", "to", "reason", "title")}} for e in events]}
