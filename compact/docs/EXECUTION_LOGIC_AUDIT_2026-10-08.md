# Execution logic audit — Claude Code web and actual software delivery

## What this review addresses

The user's example is specifically the **Code page on the Claude website**, not the Claude Code CLI. The important question is whether an assigned development task reaches the intended execution environment, modifies the intended project, produces a retrievable result, and is accepted on evidence from that result.

The preceding transport audit does not answer those questions. A perfectly delivered message can still reach the wrong surface and produce no implementation.

## Main conclusion

The current implementation treats Claude Code as a label and a composer mode inside its ordinary web-chat driver. It does not implement a complete Claude Code web execution path. Project and task state also lack the binding between an execution environment, its source revision, the resulting changes, and the evidence used to accept them.

This is an architectural mismatch. Changing a URL or selector alone will not repair the whole workflow.

Anthropic documents Code web as repository-backed remote execution with an isolated environment. Its documented web routes use `/code`, including sessions and repository/branch-prefilled new sessions. These are materially different from the project's `/new` and `/chat/...` assumptions. Sources: [Claude Code on the web](https://support.claude.com/en/articles/12618689-claude-code-on-the-web), [documented Code routes](https://support.claude.com/en/articles/14898120-open-the-claude-mobile-app-with-a-link).

## Walkthrough 1: “Use Claude Code to implement this feature”

### Where the current path diverges

1. `services/agents.py:142` accepts `ai='claude_web', mode='code'` as profile metadata.
2. `services/limits.py` describes that selection as `claude_web/code` and calls it Claude Code.
3. `RoutingDriver.open_chat` routes both Claude chat and Code through the same `WebChatDriver`.
4. `drivers/web_chat.py:33` supplies `https://claude.ai/new`; the conversation pattern only recognizes `claude.ai/chat/...`.
5. `_sel` requests a button labelled Code in a generic composer-mode control.
6. `WebChatDriver.open_chat` returns the tab/address without validating the returned execution surface or reported actual mode.
7. `drivers/api_chat.py:437` attaches the **requested** `way` and `mode` to the returned reference. A successful return therefore becomes recorded as `claude_web/code`, even if the returned address is an ordinary chat.

**Reproduced:** a controlled extension returned a normal `/chat/...` URL and reported `mode='chat'`. The hub accepted it and returned a reference labelled `way='claude_web/code', mode='code'`.

Important qualification: the current extension does contain a `mode_required` check in `pageTypeSend`. If its attempted Code switch is absent or fails, that function returns an error. Thus the exact live wrong-chat incident cannot be reduced to “the extension always ignores mode failure.” The failures include the wrong routing model, weak validation of extension results, possible default/fallback selection, and incomplete handling of the actual Code surface.

### The missing execution contract

The request to `wopen` contains no repository, branch, environment, or task-output binding. The reviewed project/task models likewise have no dedicated Code execution session, GitHub repository, base revision, remote environment ID, resulting commit, or PR linkage.

An ordinary role message is not a repository execution request. The current driver assumes that the desired behavior can be obtained by changing a composer label, with no repository/setup/output lifecycle.

### Recovery is built for the wrong address family

`extension/sw.js:567`, `webTab`, can use a still-existing tab on the host, but reopening a missing tab requires the configured `chat_pattern`. The Claude pattern recognizes `/chat` and excludes `/code`.

**Reproduced:** the configured pattern accepts an ordinary Claude chat address and rejects a Code session address. Merely changing the opening address would therefore leave reopen/recovery incomplete.

### Follow-up controls lose the selected execution configuration

`drivers/web_chat.py:256`, `_type`, calls `_sel(site)` without the session's `way`. Model, effort, and mode chosen for the role are not passed on follow-up messages. The chat state does not retain a complete actual execution configuration for that purpose.

**Reproduced:** a follow-up request had no mode, model, or effort label in its selectors. In particular, a model/effort profile change within the same provider/mode does not travel through this web-send path, despite the interface promising that such settings apply.

## Walkthrough 2: “The coding environment hits its limit; continue the work”

`services/limits.py:101` constructs fallback chains based on provider/mode availability. A blocked `claude_web/code` falls back to `chatgpt/chat`, then potentially an API provider.

**Reproduced:** the fallback changed Code to Chat without any required execution capabilities, repository/environment binding, or output contract in the selected route.

A different model might be capable of implementing the same task through other tools, but the system must establish that it has the right workspace, source revision, write/run capabilities, and output handoff. Availability alone does not establish functional equivalence. Work executed remotely in Code also requires an explicit way to recover changes and continue them elsewhere; a local PC path in memory cannot supply those remote changes.

### Unsupported provider/mode combinations are accepted

`services/limits.py:69`, `check_mode`, checks provider-specific validity only when the provider is in `MODES`. API providers are outside that map, so `ai='claude', mode='code'` is accepted. `Limits.own` subsequently drops its mode because this is an API provider.

**Reproduced:** a role retained `mode='code'` while its effective route had no mode and used the Claude API. This allows displayed configuration and actual execution semantics to disagree.

Acceptance: reject unsupported combinations or explicitly convert them with visible behavior and a valid execution contract. A Code requirement must not silently become an ordinary model conversation.

## Walkthrough 3: “The worker writes the code in the right project”

`services/tasks.py:86` appends a local PC folder to assignment messages. `prompts/roles/agent.md` tells all agents to do the work on the user's PC using the hub's PC/browser/desktop tools. The WebChatDriver protocol similarly teaches agents to express Windows tool calls as chat text.

**Reproduced:** a task assigned to a Claude Code-configured role received a local-PC folder instruction, with no remote repository/environment binding in its project data.

That protocol makes sense for an ordinary chat driving local tools. A repository-backed Code session needs its own execution instructions and a defined relationship to the local project. Without that distinction, the model can be asked to follow two different execution models: use Code's environment versus call hub tools on the PC. The implementation supplies no complete mapping of source, changes, tests, or artifacts between them.

The project directory also lacks a task-level baseline/revision and a recorded destination for coding results. The system cannot establish that the files tested by a reviewer are the files changed by the worker in the intended environment.

Acceptance: model local-tool work and remote Code work explicitly. Bind each coding run to its project source and execution target before prompting it; retrieve changes and evidence from that run before treating it as delivered software.

## Walkthrough 4: “The implementation is done and verified”

### Report text can substitute for an implementation

`services/tasks.py:134`, `report`, and `:249`, `review`, accept non-visual work using textual checks and confirmations. `quality.read_checks` checks shape, length, and simple content rules; it does not resolve those claims against an artifact or execution result.

**Reproduced:** with checklist and independent-check settings enabled, a backend task was reported and accepted using plausible text. No `parser.py` existed and no tool call had run. There was no eligible QA role, so the implementation fell back to the reviewer checking it manually; the reviewer confirmation was still only text.

This does not mean that the software can prove every natural-language claim automatically. It means that coding work has no minimum machine-verifiable result contract: the system can reach Done without even an implementation file, revision, or test-run result associated with that task.

### The independent-check gate can count unrelated activity

`services/quality.py:172`, `used_tools`, counts successful hands-on calls for a role since the check started. It includes `window_list`, and does not require a relationship between the call and the checked task or artifact.

**Reproduced using controlled tool-call records:** three unrelated window-list queries plus textual claims satisfied the hands-on threshold and produced a QA pass for a nonexistent parser. The evidence script does not pretend that those records came from real PC execution.

Acceptance: distinguish discovery activity from evidence of running/inspecting the deliverable. Record which artifact/revision, command, environment, and outcome support each check. Review remains judgment, but it must have a traceable object to judge.

### A changed result keeps an old QA pass

`services/quality.py:137`, `_changed_since_check`, always returns `False`. A task still in Review can be reported again with changed result content. `TaskService.report` replaces the report without invalidating an existing `verify_state='passed'`.

**Reproduced:** a previously passed task was reported as a different implementation. The pass remained and `needs_check` returned false, even with `verify_all` enabled.

Acceptance: attach QA to the artifact/report revision, not just task ID. Changes to implementation, reported output, requirements, or evidence must invalidate the relevant prior approval and notify the reviewer.

## Walkthrough 5: “The project plan accurately represents remaining work”

`services/plan.py:121`, `link_task`, stores one `task_id` per plan step. Explicit `P1` in a new task title can overwrite the existing link without requiring the previous task to finish.

**Reproduced:** two tasks implementing different parts of P1 were linked in sequence. The second replaced the first link. Completing only the second marked P1 Done while the first was still Pending.

`PlanService.save` also retains status and task linkage using only a matching step title, even when details and the architecture change.

**Reproduced:** a completed step kept Done after its requirements were replaced with a new incompatible grammar and new tests under the same title.

Ordered plan text is therefore not an enforced dependency or completion model. It can show progress that does not correspond to the currently required implementation.

Acceptance: support explicit step/task relationships and aggregate completion over required work. Scope changes need revision-aware invalidation. Do not infer causal links solely from title strings.

## Runtime observations and limits

Read-only metadata from `data/hub.sqlite3` showed two roles configured with `provider='claude_web', mode='code'`, one Claude web role with no explicit mode, and four historical failed Claude sessions recorded as chat with chat URLs. No Claude Code URL was present in those session references.

This snapshot does not establish when profile modes changed relative to those sessions, so it does not by itself prove the exact cause of the user's live incident.

The available inspection browser was not signed into Claude. Opening the documented Code URL reached an authentication redirect, and the temporary tab was closed. No Code task, chat message, repository connection, permission grant, or remote execution was submitted. Current authenticated Code DOM behavior still needs verification through the user's actual browser session.

## Required architectural corrections

1. Separate **model provider**, **execution surface**, **workspace**, and **task output contract**. Claude API, Claude Chat, and Claude Code web cannot share one assumed lifecycle merely because all are Claude.
2. Build a Code-specific route for repository selection, base branch/revision, environment preparation, task submission, progress, output retrieval, and continuation. Verify the actual execution target before accepting its session reference.
3. Give fallback selection compatibility requirements. Preserve/recover the task's source and results before moving execution elsewhere; otherwise mark it blocked with an actionable reason.
4. Make coding outputs first-class artifacts with provenance. Bind reports, test results, QA, and acceptance to a concrete result revision.
5. Make plan completion depend on the required task set and requirement revision.
6. Add scenario tests spanning assignment → environment selection → implementation → result retrieval → verification → acceptance. Transport and metadata tests alone cannot prove software delivery.

Changing only `/new` to `/code` would leave repository selection, URL recovery, the local-PC protocol, fallback semantics, output retrieval, and acceptance gates unresolved.

## Verification

- Eleven isolated execution-logic scenarios reproduced the above behavior in `.tmp/codex-audit/test_execution_logic_reproductions.py` (11 passed in 4.93 seconds).
- Existing mode, quality, and plan tests passed: 18 tests in 15.83 seconds.
- Production metadata was read through a SQLite connection with `mode=ro`; no production records were changed.
- Reproduction checks use temporary project folders/databases and controlled extension/tool responses. They assert current defects and must be inverted into desired-behavior tests when repairs are implemented.

This is a functional architecture review of the paths described above, not a claim that all product behavior or the authenticated Claude interface has been exhaustively verified. Application source remains unchanged.
