# Project Charter — EmaraAI Next

## 1. Vision

بناء منصة تشغيل لوكلاء الذكاء الاصطناعي موجهة أساسًا لهندسة البرمجيات، تدير فريقًا من Masters/Agents قادرًا على البحث، التخطيط، تعديل الكود، تشغيل الاختبارات، استخدام المتصفح والتطبيقات، مراجعة بعضهم، والتعلم من النتائج، مع استمرارية كاملة حتى عند انقطاع النموذج أو المتصفح أو إعادة تشغيل الـHub.

## 2. Core Problem

Compact أثبت قيمة نموذج "AI chats as a team"، لكنه نشأ أساسًا كمنظومة تنسيق chats. مع التوسع إلى coding ظهرت فئات مشاكل يجب منعها معماريًا:
- task state مرتبط أكثر من اللازم بحالة chat/session/tab.
- browser delivery يمكن أن يصبح uncertain.
- restart/provider handoff قد يؤثر على التنفيذ الجاري.
- background processes وresources تحتاج ownership/cleanup أقوى.
- coding task لا يملك دائمًا contract موحد: repo/base revision/workspace/commit/tests/artifacts.
- جودة model/agent لا يمكن عزلها بسهولة عن جودة harness.
- memory قوية كحفظ سياق، لكنها تحتاج provenance/validation/usefulness measurement.
- UI تعرض operational structure أكثر من engineering outcome.

## 3. Product Mission

EmaraAI Next يجب أن يحول الذكاء الاصطناعي من "محادثات تنفذ أوامر" إلى "software engineering workers governed by a durable control plane".

## 4. Primary Users

### Owner
يحدد المشاريع والسياسات والميزانيات، يراجع القرارات الحساسة، يرى الجودة والتكلفة والـrisks.

### Master / Project Lead
يحوّل goal إلى architecture/workstreams/tasks، يختار capabilities لا sessions، ويراجع نتائج الفريق.

### Coding Agent
يعمل داخل workspace معزول، يعدل repo، يشغل verification، ويسلم evidence.

### QA / Reviewer
يستخدم بيئة مستقلة ويعيد تشغيل acceptance tests ولا يعتمد على تقرير المؤلف.

### Research / Product / Design Agents
يعملون ضمن نفس task/memory/evaluation contracts دون فرض coding tools غير لازمة.

### Operator / Maintainer
يراقب health, queues, failures, cleanup, provider limits, backups, upgrades.

## 5. Scope

### In Scope
- Multi-project.
- Multi-agent hierarchy.
- Multi-provider models.
- Subscription/web chat adapters where permitted.
- Native API adapters.
- Coding-agent backends/SDKs/CLIs.
- Git-native isolated workspaces.
- Durable task orchestration.
- Browser/desktop/PC tooling.
- Project memory + learning.
- Quality/review/evaluation.
- Owner decisions/approvals.
- Observability/replay.
- Local-first operations.
- Remote/multi-worker-ready architecture.
- Full feature parity review against Compact.

### Out of Scope for V1
- Autonomous production deployment without policy approval.
- Training foundation models.
- Replacing Git as the code collaboration source of truth.
- Building a cloud SaaS control plane before local reliability is proven.
- Treating browser consumer subscriptions as guaranteed API infrastructure.

## 6. Strategic Differentiators

1. **Agent OS, not chat manager.**
2. **Durable execution.**
3. **Task-scoped clean workspaces.**
4. **Model × Harness × Task evaluation.**
5. **Evidence-first acceptance.**
6. **Learning only from validated outcomes.**
7. **Human governance without micromanagement.**
8. **Provider independence.**
9. **Arabic/RTL first-class UX.**
10. **Resource ownership and automatic cleanup.**

## 7. Non-Negotiable Guarantees

- A chat crash must not lose task state.
- A Hub restart must not silently duplicate side effects.
- An agent may not accidentally write into another task's workspace.
- Every process/container/port/browser context created by a task has an owner.
- Cleanup may never delete unknown/unowned user data automatically.
- A coding task cannot be accepted from narrative claims alone.
- Uncertain delivery/action outcomes must remain uncertain until evidence resolves them.
- Hidden or independent verification must exist for high-risk acceptance.
- All important state transitions must be traceable.
- Feature parity decisions must be explicit.

## 8. Success Definition

The product succeeds when a real repository feature can flow:
Owner goal → Master plan → coding task → isolated workspace → code change → targeted tests → regression checks → diff review → independent QA → merge/PR → cleanup,
and this flow survives intentional interruption/restart without lost work, duplicated unsafe actions, or orphaned resources.

## 9. Governance

Major architectural choices require ADRs.
No framework/provider becomes core based only on documentation or popularity. At least one prototype and reproducible benchmark is required for decisions affecting:
- coding runtime,
- durable orchestration,
- workspace isolation,
- model routing,
- memory retrieval,
- distributed execution.

## 10. Definition of Done for the Blueprint

Before production coding:
- complete feature inventory exists,
- target architecture accepted,
- state machines written,
- failure model written,
- security boundaries written,
- evaluation methodology written,
- cleanup contract written,
- provider capability protocol written,
- first benchmark corpus prepared,
- at least two coding runtime prototypes compared.
