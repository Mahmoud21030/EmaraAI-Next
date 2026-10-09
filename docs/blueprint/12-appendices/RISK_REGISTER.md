# Initial Risk Register

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Overengineering durable core | High | Medium | prototype minimal outbox/state machine before adopting heavy engine |
| Browser provider UI drift | Medium/High | High | adapter isolation, capability health, fallback |
| Unsafe cleanup deletes user work | Critical | Low/Medium | ownership markers, quarantine unknown, snapshots |
| Model silently weakens tests | High | Medium | hidden verifier/diff review |
| Memory reinforces wrong lesson | High | Medium | validation/provenance/usefulness |
| Secret leakage through tools/logs | Critical | Medium | scoped vault/redaction/sandbox |
| Parallel Git conflicts | Medium | High | task worktrees + merge queue |
| Resource exhaustion | High | Medium | scheduler/budgets/backpressure |
| API/provider cost runaway | High | Medium | budgets/caps/alerts |
| Framework/vendor lock-in | High | Medium | adapters + ADR/prototype |
| Windows isolation limitations | Medium | Medium | WSL/container options |
| SQLite concurrency ceiling | Medium | Medium later | worker writes through control plane; PostgreSQL path |
| Benchmark overfitting | High | Medium | varied hidden cases + production feedback |
| Reputation gaming | Medium | Medium | independent evidence/difficulty normalization |
| UI hides operational risk | High | Medium | shared attention model/task-centric UX |
| Migration loses Compact feature | High | Medium | formal parity matrix/gate |
| External terms/auth changes | High | Medium | official-source research + replaceable adapters |
| Too much context/memory | Medium | High | retrieval budgets and measurement |

Every risk gets owner, trigger, mitigation status and review date during implementation.
