# EmaraAI — proposed UI direction

These are visual proposals, not implemented screens. The local service refused the connection during review; conclusions below come from the UI source and route inventory. Example people, tasks, messages and counts in the images are illustrative.

## Visual direction

Pearl reading surfaces, an espresso navigation rail, chartreuse primary actions, confident typography, thin separators, generous message spacing. Dark owner home uses the same palette and hierarchy. Agent identities should use initials or clearly artificial avatars rather than imply human employees. Icons must have labels or accessible tooltips; status must include words, not color alone. Arabic uses RTL message direction with mixed code/path blocks staying LTR. Body text 16px, useful line length, keyboard access and visible focus states.

## Communication is a first-class workspace

- Original agent chat and team messages are two clearly named views, available inline rather than inside a modal. Show provider, agent, timestamp, source and last synchronization time.
- Important prose remains visible; only tool traces collapse. Full text is preserved, with search, pin, quote-reply, copy and jump-to-original actions. Suggested important passages require confirmation before becoming a saved decision.
- The context rail holds pinned quotes, questions needing answers, linked tasks and evidence. Every quote links to its source message. Important is distinct from unread and from needs-action.
- Composer names the recipient, keeps drafts per conversation, supports mentions/attachments and shows queued, sending, confirmed and failed separately. Broadcast explicitly lists recipients and partial outcomes.
- Keep scroll position while reading, show a new-message counter, and avoid jumping to the latest message automatically. Synchronization gaps and unsupported original transcripts are explicit.
- Mobile uses a single conversation pane with list/back navigation and a context drawer; critical text is never lost merely because the rail is hidden.

## Mapping every existing section

| Existing pages | Proposed destination and interaction |
| --- | --- |
| Command Center | Today: decisions first, important original quotes, project movement, Master conversation |
| Communication / direct messages | Inbox: project rooms, agents, assistants, original transcripts, unread and important filters |
| Decision rooms | Inbox room type: visible speakers, proposals, votes, final decision and linked source discussion |
| Decisions | Needs you queue: question/approval context, exact proposed action, answer/reject and recorded outcome |
| Projects and all project tabs | Project workspace: Overview, Plan, Work, Conversation, Files, Knowledge; preserve team/activity/report access |
| Tasks | Work: board/list views, explicit dependencies, discussion, evidence, review and completion conditions |
| Company chart / people / departments | Team: directory and optional org map, workload, identity, provider, memory, chat and assignment |
| My assistants | Inbox personal assistant scope plus Team personal group; direct work, reports and review remain accessible |
| Activity / events | Project timeline or Operations event explorer; tool traces separated from readable conversation |
| Workflows | Automations: editor plus run history, paused approval step, errors and recovery |
| Knowledge / people and project memories | Library knowledge: source-linked decisions, rules, project notes and scoped memory |
| Deliverables | Library files: preview, author, source task/message, local versus cloud location, evidence state |
| Reports | Insights: readable briefing with links to underlying work and messages |
| Setup / Connections | Operations connection guide: browser, providers, connectors, repository/branch, actual availability |
| Settings | Operations settings: grouped sections, search, clear effect and save feedback |
| PC load / Tools and cost | Operations resources: running/waiting jobs by owner and actionable cost breakdown |
| Quality | Work review queue and Operations verification policy; distinguish claimed from verified completion |
| API | Operations integrations: endpoints, access, models and request history |
| Phone and remote | Operations devices: access status, device setup and connection guidance |
| Recovery / Diagnostics / maintainer proposals | Operations issues: symptom, affected work, evidence, proposed fix, approval and recovery outcome |
| Plan technical view | Project Plan; preserve architecture, scope and dependency detail |
| n8n | Operations integrations and Automations triggers |
| Logs | Operations logs: filtered detail view, connected to issue/task/session |
| Backups and trash | Operations history: backups, contents, restoration impact, recoverable items |
| About | Operations about: version, folders and installed integrations |

## Implementation gaps to plan for

The current UI exposes original chat text through a separate modal and manual read operation; a reliable inline transcript with provider-neutral provenance needs backend support and synchronization behavior. Existing messages and transcripts must not be merged into a fabricated history. Durable pins, full-text search across transcripts, quote references and a unified action queue need explicit storage/API contracts. Generated mockups illustrate the goal; their labels, permissions and source links must be adapted to actual capabilities before implementation.

## Generated concepts

Built-in imagegen was used. Prompt themes: (1) pearl Inbox with original chat, important prose and evidence rail; (2) pearl project workspace combining work, discussion and artifacts; (3) espresso owner home prioritizing decisions and important team quotations. The prompts explicitly request readable substantial chat text and collapse tool traces rather than prose. The final prompt specifications are also encoded by the screen requirements above.
