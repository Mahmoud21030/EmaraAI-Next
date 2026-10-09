# Provider Architecture and Capability Protocol

## 1. Objective

Stop hardcoding business logic around provider names. A route is selected by capabilities and policy.

## 2. Provider Families

### WebChatProvider
Examples: ChatGPT browser, Claude web, Gemini web.
Characteristics:
- browser/session dependent.
- potentially economical subscription use.
- UI drift risk.
- explicit delivery uncertainty.
- provider usage limits.

### APIProvider
Official or OpenAI-compatible API.
Characteristics:
- structured requests/receipts.
- cost/token metering.
- API authentication.
- streaming/tool support varies.

### CodingAgentProvider
Repository-aware external agent/harness such as Codex/Claude Code/OpenHands/other CLI/SDK.
Characteristics:
- long-running.
- may own internal tools/environment.
- produces patch/commit/session.
- repository binding required.

### LocalModelProvider
Local inference/OpenAI-compatible runtime.
Characteristics:
- resource-heavy.
- low marginal API cost.
- capability dependent on model/harness.

## 3. Capability Manifest

Each provider/model route advertises:
- chat.
- coding.
- repository_binding.
- tool_calls.
- structured_output.
- streaming.
- vision.
- browser.
- long_running.
- resume.
- cancellation.
- context_window.
- effort_levels.
- persistence/privacy class.
- max concurrency.
- cost model.
- rate/usage limits.
- supported auth.

## 4. Provider Interface

Conceptual methods:
- health()
- list_models()
- capabilities(model/mode)
- create_session(context)
- resume_session(ref)
- send/infer()
- stream()
- cancel()
- inspect_receipt()
- usage()
- close_session()

Coding provider adds:
- bind_repository(repo, revision, branch)
- start_job(task_spec)
- inspect_job()
- collect_changes()
- collect_artifacts()

## 5. Route Decision

Inputs:
- task type.
- required capabilities.
- security policy.
- project preference.
- agent competence.
- provider availability.
- limits.
- benchmark scores.
- predicted cost/TTAC.
- data privacy.

Output:
route + explanation + fallback set + policy version.

## 6. Fallback

Fallback cannot silently reduce required capability.
Example:
Claude Code unavailable → generic Chat model is NOT equivalent for repo-native task unless system provisions its own coding harness.

Fallback rules:
- capability-compatible only.
- preserve workspace.
- new attempt or explicit handoff depending semantics.
- record reason.
- budget checks.

## 7. Web Adapter Safety

- validate exact target conversation/provider/mode.
- record URL/session identity.
- layered send receipts.
- never infer success from "typed command returned no error".
- provider UI selector versioning.
- adapter health checks.
- no automatic unsafe resend.

## 8. Limit Handling

Provider state records:
- limit kind.
- reset_at.
- source evidence.
- retry_after.
- whether route-level or account-level.

Router excludes blocked route until reset/manual override.

## 9. API Gateway Outward Compatibility

Next may expose OpenAI-compatible endpoints, but:
- auth mandatory according to deployment mode.
- model names map to explicit route policies.
- actual route/model returned in metadata.
- private mode never falls back to persistence-changing route without consent.
- tool/image support advertised truthfully.

## 10. Provider Evaluation

Track per route:
- success rate.
- accepted change rate.
- latency.
- cost.
- timeout.
- tool error.
- recovery rate.
- rework.
- task-type performance.

No global winner assumption.
