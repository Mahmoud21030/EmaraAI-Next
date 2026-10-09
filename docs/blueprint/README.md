# EmaraAI Next Blueprint

## Purpose

هذا المجلد هو **المصدر المرجعي الرسمي لتصميم EmaraAI Next**: جيل جديد مستوحى من EmaraAI Hub Compact، لكنه مصمم من البداية كـ **Agent Operating System for Software Engineering** وليس كـ chat orchestrator أضيفت له قدرات coding لاحقًا.

الهدف من الـBlueprint هو منع ثلاثة أنواع من الفشل:
1. فقدان Features موجودة بالفعل في Compact أثناء إعادة البناء.
2. تكرار المشاكل المعمارية والتشغيلية القديمة تحت واجهة جديدة.
3. بدء البرمجة قبل حسم عقود التنفيذ، العزل، الاسترداد، التقييم، والأمان.

## Golden Rule

> لا يتم حذف أو تغيير أي Capability من Compact إلا بقرار موثق، سبب واضح، بديل محدد، وAcceptance Test يثبت أن Next لا يفقد القيمة التي كانت تقدمها.

## Product Positioning

EmaraAI Next يجب أن يكون:
- Local-first AI software engineering operating system.
- Multi-agent and multi-model.
- Chat-subscription friendly where allowed, مع دعم API رسمي.
- Git-native.
- Durable across crashes/restarts/provider failures.
- Workspace-isolated.
- Self-cleaning.
- Evidence-driven.
- Evaluation-driven.
- Arabic/RTL and English first-class.
- قابلاً لاحقًا للتوسع من جهاز واحد إلى عدة workers.

## Document Map

### 00-overview
- PROJECT_CHARTER.md — الهدف، النطاق، تعريف النجاح.
- PRINCIPLES_AND_NON_GOALS.md — المبادئ التي لا يجوز كسرها.
- SUCCESS_CRITERIA.md — المقاييس والـrelease gates.

### 01-product
- FEATURE_PARITY_MATRIX.md — كل Features Compact وما سيحدث لها.
- PRODUCT_REQUIREMENTS.md — متطلبات المنتج الوظيفية وغير الوظيفية.
- USER_ROLES_AND_WORKFLOWS.md — Owner/Master/Agent/Reviewer/Operator workflows.

### 02-architecture
- TARGET_ARCHITECTURE.md — Control Plane / Agent Runtime / Execution Plane / Model Gateway / Knowledge / Evaluation.
- DOMAIN_MODEL_AND_STATE_MACHINES.md — Project/Task/Attempt/Agent/Session/Workspace/Delivery state machines.
- DURABILITY_AND_MESSAGING.md — idempotency, outbox, leases, fencing, retry, quarantine.
- DATA_ARCHITECTURE.md — storage, schemas, indexes, artifacts, audit history.
- PROVIDER_ARCHITECTURE.md — chat/API/coding/local provider contracts.
- WEB_CHAT_DELIVERY_AND_WINDOW_POOL.md — dedicated EmaraAI delivery window, shared reusable tab pool, receipts, recovery and uncertain-delivery rules.

### 03-coding-runtime
- CODING_RUNTIME.md — coding execution lifecycle.
- WORKSPACE_LIFECYCLE_AND_CLEANUP.md — worktrees, containers, leases, janitor, cleanup policy.
- GIT_COLLABORATION.md — branches, commits, review, merge queue.
- CODING_HARNESS.md — repo map, AST/symbol search, tests, build, diff/self-review.
- BROWSER_DESKTOP_EXECUTION.md — browser + desktop automation isolation and ownership.

### 04-agents-models-memory
- AGENT_RUNTIME.md — reasoning/runtime/session separation.
- MODEL_ROUTER_AND_PROVIDER_GATEWAY.md — capability/cost/reliability routing.
- MEMORY_AND_LEARNING.md — working/episodic/semantic/procedural/evaluation memory.
- SKILLS_SYSTEM.md — versioned skills with tests and permissions.

### 05-quality-evaluation
- QUALITY_GATES.md — acceptance evidence and independent review.
- EVALUATION_LAB.md — reproducible model/agent/harness experiments.
- AGENT_AND_MODEL_SCORING.md — competence matrix, calibration, decay.
- TESTING_AND_BENCHMARKS.md — unit/integration/E2E/chaos/benchmark strategy.

### 06-security
- SECURITY_MODEL.md — trust boundaries and sandboxing.
- PERMISSIONS_APPROVALS_SECRETS.md — approvals, scopes, vault, egress policies.

### 07-ui-ux
- UI_UX_SPEC.md — Mission Control experience.
- INFORMATION_ARCHITECTURE.md — navigation and product surfaces.

### 08-operations
- OBSERVABILITY_REPLAY.md — traces, replay, debugging.
- OPERATIONS_RECOVERY_DR.md — incidents, backup, disaster recovery.
- PERFORMANCE_OPTIMIZATION.md — latency/context/cache/concurrency.
- COST_RESOURCE_MANAGEMENT.md — CPU/RAM/GPU/tokens/API budgets.

### 09-migration
- COMPACT_TO_NEXT_MIGRATION.md — keep/replace/deprecate strategy.
- FEATURE_PARITY_ACCEPTANCE.md — formal parity gate before beta.

### 10-research-roadmap
- MASTER_RESEARCH_BRIEF.md — أول تكليف للـMaster الجديد.
- TECHNOLOGY_EVALUATION_PLAN.md — prototypes and decision criteria.
- IMPLEMENTATION_ROADMAP.md — phased delivery.

### 11-adrs
Architecture Decision Records for irreversible/high-impact choices.
- ADR-0005 — Ephemeral runners, continuous backup (GitHub/Drive) and auto-resume.

### 12-appendices
- COMPACT_FEATURE_INVENTORY.md
- GLOSSARY.md
- RISK_REGISTER.md
- OPEN_QUESTIONS.md

## Current-System Baseline

الـBlueprint مبني على مراجعة فعلية لـ H:\EmaraAI-Hub-Compact، بما في ذلك:
- README / Architecture / Operations / Tools / Changelog.
- service layer: agents, tasks, sessions, memory, quality, rooms, approvals, workflows, vault, chat API, providers.
- drivers: delivery, extension, web chat, API chat.
- PC runtime: shell sessions, resource scheduler, browser/desktop/file operations.
- test inventory and 2026-10-09 reliability audit.
- known production issues around browser-driven delivery, uncertain sends, restarts, provider/session coupling, cleanup, and recovery.

## Release Philosophy

EmaraAI Next لا يصبح "أفضل" لمجرد أن واجهته أحدث أو أنه يستخدم model أقوى. النجاح يتطلب إثبات:
- accepted-code correctness,
- durable execution,
- clean workspaces,
- recoverability,
- lower operator burden,
- model/agent measurability,
- feature parity or intentional deprecation,
- security boundaries,
- efficient cost and resource use.

## Status

هذا المجلد Specification/Research Blueprint. لا يمثل production implementation، ولا يفترض أن أي تقنية مقترحة قد تم اختيارها نهائيًا قبل prototypes وADRs.
