# Success Criteria and Release Gates

## 1. Product-Level North Star

**Time to Accepted Change (TTAC)**: الوقت من قبول task إلى قبول نتيجة يمكن دمجها بثقة.

يجب قياس TTAC مع:
- correctness,
- rework,
- cost,
- operator interventions,
- resource cleanliness.

## 2. Reliability Gates

### R1 Restart Durability
أثناء coding task حقيقية:
1. ابدأ تعديلًا وتشغيل test طويل.
2. أعد تشغيل Hub.
3. أثبت أن task/attempt/workspace state يستمر.
4. لا يتم تكرار side effects غير الآمنة.
5. يمكن الاستكمال أو التعافي بوضوح.

Pass target: 100% في acceptance scenarios المحددة.

### R2 Worker Crash Recovery
قتل worker/runtime يجب أن:
- يحرر lease بعد timeout،
- يمنع worker القديم من الكتابة بعد fencing,
- يحافظ على workspace/artifacts,
- يتيح retry/handoff.

### R3 Delivery Integrity
لا يجوز وجود state transition من "verified" إلى "unknown/failed" لنفس operation دون interpretation واضح. كل delivery/action لها receipt semantics.

### R4 Cleanup
بعد 1,000 task attempts test:
- 0 orphaned owned processes.
- 0 orphaned owned ports.
- 0 unexplained temp workspaces.
- 0 deleted user/unowned resources.

### R5 Queue Safety
Under overload:
- backpressure works,
- no retry storm,
- no resource starvation,
- queue order/policy observable.

## 3. Coding Quality Gates

For benchmark tasks:
- correct patch rate.
- regression-free rate.
- hidden test pass rate.
- reviewer first-pass acceptance.
- number of rework rounds.
- escaped defect rate.

Initial target values are set only after baseline experiments; architecture must make them measurable.

## 4. Feature Parity Gate

Before Beta:
- every Compact feature row is classified.
- PRESERVE/REDESIGN/REPLACE rows have acceptance tests.
- DEPRECATE rows have owner-approved reason and migration note.
- no unclassified feature.

## 5. Security Gates

- task sandbox cannot read host paths outside policy.
- secrets are scoped and redacted.
- untrusted repository/web content cannot alter platform policy.
- privileged actions require appropriate approval.
- network egress is explicit for isolated workers.
- audit log captures sensitive policy decisions without logging secret values.

## 6. Memory/Learning Gates

Learning is considered useful only if A/B or replay evaluation shows:
- increased success rate, or
- reduced TTAC/cost/rework,
without material regression.

Memory retrieval metrics:
- relevance,
- stale-memory usage,
- contradictory-memory rate,
- useful-memory attribution.

## 7. Model Router Gates

Router must beat at least one fixed-model baseline on a balanced objective:
- acceptance quality,
- cost,
- latency,
- provider availability.

It must also support override and explain chosen route.

## 8. UI/UX Gates

Critical owner workflows must be completable on desktop and mobile:
- create project,
- configure team,
- inspect plan,
- view task execution,
- approve/reject sensitive action,
- review code/evidence,
- inspect failed task,
- resolve quarantine,
- compare models/agents,
- inspect resources,
- pause/resume project.

Required:
- Arabic RTL.
- English LTR.
- keyboard navigation.
- accessible dialogs/forms.
- loading/empty/error/stale/success states.
- no critical operational state hidden only in logs.

## 9. Performance Gates

Define budgets for:
- tool dispatch overhead,
- task state transition latency,
- file read/search latency,
- repo index update latency,
- dashboard event freshness,
- cleanup time,
- recovery detection time.

Optimization must not sacrifice correctness or observability.

## 10. Operational Gates

- backup restore tested, including corrupted backup.
- database/schema migration rollback tested.
- disk-full behavior tested.
- provider outage tested.
- browser outage tested.
- network outage tested.
- process exhaustion tested.
- stale lease tested.
- worker restart tested.
- partial artifact upload tested.

## 11. Release Stages

### Prototype
Can execute benchmark tasks, no compatibility promise.

### Alpha
Core task/workspace/model contracts frozen enough for migration experiments.

### Beta
Feature parity gate passed, durability/security/cleanup acceptance passed.

### Stable
Operational burn-in, low escaped-defect rate, documented upgrade/restore process, repeatable benchmark report.
