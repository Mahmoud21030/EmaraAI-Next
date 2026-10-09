"""Versioned schema. Append new entries; never edit an applied one."""

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
CREATE TABLE projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, goal TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active', chat_url TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE roles (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  name TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL DEFAULT '', capabilities TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
  UNIQUE(project_id, name)
);
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, role_id TEXT NOT NULL REFERENCES roles(id),
  generation INTEGER NOT NULL, status TEXT NOT NULL, join_code TEXT UNIQUE,
  chat_ref TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, joined_at REAL,
  last_activity_at REAL, last_tool_at REAL, tool_calls INTEGER NOT NULL DEFAULT 0,
  calls_since_checkpoint INTEGER NOT NULL DEFAULT 0, last_checkpoint_at REAL,
  chars_in INTEGER NOT NULL DEFAULT 0, chars_out INTEGER NOT NULL DEFAULT 0,
  observed_chars INTEGER, observed_turns INTEGER, chat_state TEXT NOT NULL DEFAULT 'unknown',
  state_since REAL, continue_count INTEGER NOT NULL DEFAULT 0, last_nudge_at REAL,
  previous_session_id TEXT, closed_reason TEXT NOT NULL DEFAULT '', closed_at REAL,
  waiting_since REAL, waiting_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_sessions_role ON sessions(role_id, status);
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
  instructions TEXT NOT NULL DEFAULT '', acceptance TEXT NOT NULL DEFAULT '[]',
  assigned_role_id TEXT, created_by_role_id TEXT, status TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 3, progress INTEGER NOT NULL DEFAULT 0,
  result_summary TEXT NOT NULL DEFAULT '', result_details TEXT NOT NULL DEFAULT '',
  result_files TEXT NOT NULL DEFAULT '[]', review_note TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL
);
CREATE INDEX ix_tasks_assignee ON tasks(assigned_role_id, status);
CREATE TABLE messages (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, from_role_id TEXT, to_role_id TEXT NOT NULL,
  kind TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL,
  task_id TEXT, reply_to TEXT, needs_reply INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 3,
  status TEXT NOT NULL DEFAULT 'queued', created_at REAL NOT NULL, read_at REAL, read_by_session TEXT
);
CREATE INDEX ix_messages_inbox ON messages(to_role_id, status, created_at);
CREATE TABLE memory (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, role_id TEXT, kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0, session_id TEXT, created_at REAL NOT NULL
);
CREATE INDEX ix_memory_project ON memory(project_id, archived, kind, created_at);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, project_id TEXT, type TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '', cid TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_events_type ON events(type, ts);
CREATE TABLE outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, url TEXT NOT NULL, body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at REAL NOT NULL, last_error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE INDEX ix_outbox_due ON outbox(status, next_attempt_at);
CREATE TABLE chat_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, kind TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, done_at REAL
);
CREATE INDEX ix_chat_commands_status ON chat_commands(status, created_at);
CREATE TABLE tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, plugin TEXT NOT NULL, tool TEXT NOT NULL,
  session_id TEXT, ok INTEGER NOT NULL, error_code TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL,
  cid TEXT NOT NULL DEFAULT '', args_preview TEXT NOT NULL DEFAULT '', result_preview TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_tool_calls_ts ON tool_calls(ts);
CREATE TABLE dedupe (
  key TEXT PRIMARY KEY, result TEXT NOT NULL, created_at REAL NOT NULL
);
"""),
    # v2: restart-safe supervisor state, at-least-once inbox, batch steps, cid on chat commands.
    (2, """
ALTER TABLE sessions ADD COLUMN marks TEXT NOT NULL DEFAULT '{}';
ALTER TABLE messages ADD COLUMN acked INTEGER NOT NULL DEFAULT 0;
UPDATE messages SET acked = 1 WHERE status = 'read';
ALTER TABLE messages ADD COLUMN deliveries INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chat_commands ADD COLUMN cid TEXT NOT NULL DEFAULT '';
ALTER TABLE tool_calls ADD COLUMN step INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_tool_calls_cid ON tool_calls(cid);
CREATE INDEX ix_events_cid ON events(cid);
CREATE INDEX ix_messages_unacked ON messages(read_by_session, acked)
"""),
    # v3: small key/value store for hub-level state (injector result, ...)
    (3, """
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)
"""),
    # v4: recovery engine history
    (4, """
CREATE TABLE recoveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, target TEXT NOT NULL, project_id TEXT, problem TEXT NOT NULL,
  strategy TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, max_attempts INTEGER NOT NULL DEFAULT 3,
  step TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', cid TEXT NOT NULL DEFAULT '', ended_at REAL
);
CREATE INDEX ix_recoveries_target ON recoveries(target, problem, ts)
"""),
    # v5: files (images, documents) attached to messages between master, agents and the user
    (5, """
ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]';
CREATE TABLE files (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0,
  path TEXT NOT NULL, added_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
)
"""),
    # v6: visual tasks (UI, graphics): the report needs a screenshot, the review needs an analysis of it
    (6, """
ALTER TABLE tasks ADD COLUMN visual INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN visual_review TEXT NOT NULL DEFAULT ''
"""),
    # v7: the project plan (overview, architecture, ordered steps that the work checks off)
    (7, """
CREATE TABLE plans (
  project_id TEXT PRIMARY KEY, overview TEXT NOT NULL DEFAULT '', architecture TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL
);
CREATE TABLE plan_steps (
  project_id TEXT NOT NULL, n INTEGER NOT NULL, title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'todo', note TEXT NOT NULL DEFAULT '', task_id TEXT, updated_at REAL NOT NULL,
  PRIMARY KEY (project_id, n)
)
"""),
    # v8: the team - an agent has a proper name ("Software Engineer A") next to its key ("software-engineer-a")
    (8, """
ALTER TABLE roles ADD COLUMN display TEXT NOT NULL DEFAULT ''
"""),
    # v9: agents as persistent employees - identity, hierarchy, lifecycle state, and their own memory
    (9, """
ALTER TABLE roles ADD COLUMN person_name TEXT NOT NULL DEFAULT '';
ALTER TABLE roles ADD COLUMN career TEXT NOT NULL DEFAULT '';
ALTER TABLE roles ADD COLUMN seniority TEXT NOT NULL DEFAULT '';
ALTER TABLE roles ADD COLUMN personality TEXT NOT NULL DEFAULT '';
ALTER TABLE roles ADD COLUMN team TEXT NOT NULL DEFAULT '';
ALTER TABLE roles ADD COLUMN level TEXT NOT NULL DEFAULT '';
ALTER TABLE roles ADD COLUMN manager_role_id TEXT;
ALTER TABLE roles ADD COLUMN state TEXT NOT NULL DEFAULT 'active';
ALTER TABLE roles ADD COLUMN state_reason TEXT NOT NULL DEFAULT '';
CREATE TABLE agent_memory (
  id TEXT PRIMARY KEY, role_id TEXT NOT NULL, project_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'agent', task_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX ix_agent_memory_role ON agent_memory(role_id, kind, created_at)
"""),
    # v10: questions to the client (the user): asked by the master or a lead, answered in the hub, escalated when unanswered
    (10, """
CREATE TABLE client_questions (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, from_role_id TEXT NOT NULL, question TEXT NOT NULL, options TEXT NOT NULL DEFAULT '[]',
  recommendation TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', answer TEXT NOT NULL DEFAULT '',
  answered_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, deadline_at REAL NOT NULL, answered_at REAL
)
"""),
    # v11: the company - departments with a mission, company knowledge; approvals for risky PC actions
    (11, """
CREATE TABLE departments (
  name TEXT PRIMARY KEY COLLATE NOCASE, mission TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE knowledge (
  id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'owner',
  project_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX ix_knowledge_cat ON knowledge(category, updated_at);
CREATE TABLE approvals (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, digest TEXT NOT NULL, command TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  project_id TEXT, session_id TEXT, role_id TEXT, plugin TEXT NOT NULL DEFAULT '', tool TEXT NOT NULL DEFAULT '', cid TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, expires_at REAL NOT NULL, decided_at REAL, executed_at REAL
);
CREATE INDEX ix_approvals_digest ON approvals(digest, status)
"""),
    # v12: workflows built in the hub (a graph of trigger -> steps) and their runs
    (12, """
CREATE TABLE workflows (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', graph TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL DEFAULT 'owner', created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE workflow_runs (
  id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, trigger TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL,
  log TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX ix_workflow_runs ON workflow_runs(workflow_id, started_at)
"""),
    # v13: which AI runs an agent (provider + model); skills (how-to guides) and who has them
    (13, """
ALTER TABLE roles ADD COLUMN provider TEXT NOT NULL DEFAULT '';
ALTER TABLE roles ADD COLUMN model TEXT NOT NULL DEFAULT '';
CREATE TABLE skills (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, source TEXT NOT NULL,
  path TEXT NOT NULL DEFAULT '', files TEXT NOT NULL DEFAULT '[]', installed_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE role_skills (
  role_id TEXT NOT NULL, skill_id TEXT NOT NULL, assigned_by TEXT NOT NULL DEFAULT '', assigned_at REAL NOT NULL,
  PRIMARY KEY (role_id, skill_id)
);
CREATE TABLE skill_files (
  skill_id TEXT NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (skill_id, path)
)
"""),
    # v14: how hard an agent's AI thinks (low | medium | high; empty = the provider's default)
    (14, """
ALTER TABLE roles ADD COLUMN effort TEXT NOT NULL DEFAULT ''
"""),
    # v15: a message from a chat to the owner (the user)
    (15, """
ALTER TABLE messages ADD COLUMN to_owner INTEGER NOT NULL DEFAULT 0
"""),
    # v16: the project's folder on the PC is a fact of its own (it used to sit at the end of the brief and was cut off)
    (16, """
ALTER TABLE projects ADD COLUMN folder TEXT NOT NULL DEFAULT ''
"""),
    # v17: what a chat itself wrote (and was told) in ChatGPT, read from the page whenever a delivery tab visits the chat
    (17, """
CREATE TABLE chat_texts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, role_id TEXT, session_id TEXT NOT NULL, who TEXT NOT NULL,
  text TEXT NOT NULL, digest TEXT NOT NULL, captured_at REAL NOT NULL);
CREATE UNIQUE INDEX chat_texts_once ON chat_texts(session_id, digest);
CREATE INDEX chat_texts_role ON chat_texts(role_id, captured_at)
"""),
    # v18: calls of the chat API (/v1/chat/completions): when, which model, how long, how big - never the text
    (18, """
CREATE TABLE api_calls (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, model TEXT NOT NULL, way TEXT NOT NULL, status TEXT NOT NULL, prompt_chars INTEGER NOT NULL DEFAULT 0,
  reply_chars INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0, chat_url TEXT NOT NULL DEFAULT '', caller TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '');
CREATE INDEX api_calls_ts ON api_calls(ts)
"""),
    # v19: quality - the proof a report carries, the independent check, and the points record
    (19, """
CREATE TABLE agent_points (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, role_id TEXT NOT NULL, person_name TEXT NOT NULL DEFAULT '', project_id TEXT, task_id TEXT,
  points INTEGER NOT NULL, code TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', by TEXT NOT NULL DEFAULT '');
CREATE INDEX agent_points_role ON agent_points(role_id, ts);
CREATE INDEX agent_points_person ON agent_points(person_name, ts);
ALTER TABLE tasks ADD COLUMN user_facing INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN verify INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN verify_state TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN checks TEXT;
ALTER TABLE tasks ADD COLUMN confirmed TEXT;
ALTER TABLE tasks ADD COLUMN entry_points TEXT;
ALTER TABLE tasks ADD COLUMN audit TEXT;
ALTER TABLE tasks ADD COLUMN verifies_task_id TEXT;
ALTER TABLE tasks ADD COLUMN accepted_by_role_id TEXT;
ALTER TABLE tasks ADD COLUMN verified_by_role_id TEXT
"""),
    # v20: decision rooms - a round-table discussion that ends in a decision
    (20, """
CREATE TABLE decision_rooms (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, question TEXT NOT NULL, options TEXT NOT NULL, how TEXT NOT NULL, status TEXT NOT NULL,
  circle INTEGER NOT NULL DEFAULT 1, max_circles INTEGER NOT NULL DEFAULT 4, floor_role_id TEXT, floor_since REAL, seats TEXT NOT NULL DEFAULT '[]',
  opened_by_role_id TEXT, opened_by TEXT NOT NULL DEFAULT '', opened_at REAL NOT NULL, result TEXT NOT NULL DEFAULT '', result_how TEXT NOT NULL DEFAULT '',
  tally TEXT NOT NULL DEFAULT '{}', summary TEXT NOT NULL DEFAULT '', overruled_by TEXT NOT NULL DEFAULT '', overrule_reason TEXT NOT NULL DEFAULT '', closed_at REAL);
CREATE INDEX decision_rooms_project ON decision_rooms(project_id, status);
CREATE TABLE decision_members (
  room_id TEXT NOT NULL, role_id TEXT NOT NULL, position TEXT NOT NULL DEFAULT '', changed_in INTEGER NOT NULL DEFAULT 0, spoke_in INTEGER NOT NULL DEFAULT 0,
  reminded INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (room_id, role_id));
CREATE TABLE decision_turns (
  id TEXT PRIMARY KEY, room_id TEXT NOT NULL, circle INTEGER NOT NULL, role_id TEXT, who TEXT NOT NULL, position TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
  nothing_new INTEGER NOT NULL DEFAULT 0, at REAL NOT NULL);
CREATE INDEX decision_turns_room ON decision_turns(room_id, at)
"""),
    # v21: the mode an agent's chat runs in (ChatGPT: chat | work, claude.ai: chat | code)
    (21, """
ALTER TABLE roles ADD COLUMN mode TEXT NOT NULL DEFAULT ''
"""),
    # v22: what the maintainer proposes to change in the hub itself (the owner approves; applied with a backup; revertible)
    (22, """
CREATE TABLE maintenance_proposals (
  id TEXT PRIMARY KEY, created_at REAL NOT NULL, role_id TEXT, kind TEXT NOT NULL, title TEXT NOT NULL, why TEXT NOT NULL, edits TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, decided_at REAL, applied TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '');
CREATE INDEX maintenance_proposals_status ON maintenance_proposals(status, created_at)
"""),
    # v23: a decision room can be archived (hidden from the list, kept with its decision)
    (23, """
ALTER TABLE decision_rooms ADD COLUMN archived INTEGER NOT NULL DEFAULT 0
"""),
    # v24: a room may let its members put a new option on the table
    (24, """
ALTER TABLE decision_rooms ADD COLUMN allow_new INTEGER NOT NULL DEFAULT 0
"""),
    # v25: devices that get push notifications while the Control Center is closed
    (25, """
CREATE TABLE push_subs (
  endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, cats TEXT NOT NULL DEFAULT '[]', label TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, last_ok REAL, fails INTEGER NOT NULL DEFAULT 0)
"""),
    # v26: planning rooms - the members write one plan together, review it until it is agreed, and it ends with a report
    (26, """
ALTER TABLE decision_rooms ADD COLUMN phase TEXT NOT NULL DEFAULT '';
ALTER TABLE decision_rooms ADD COLUMN draft TEXT NOT NULL DEFAULT '';
ALTER TABLE decision_rooms ADD COLUMN draft_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE decision_rooms ADD COLUMN editor_role_id TEXT;
ALTER TABLE decision_rooms ADD COLUMN report TEXT NOT NULL DEFAULT ''
"""),
    # v27: the hourly housekeeping deletes old events by time alone; without this index every run scans the whole table
    (27, """
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts)
"""),
]
