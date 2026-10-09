# Provider Gateway — Official Source Ledger

Research date: **2026-10-09** (Africa/Cairo). Scope: EmaraAI Next P2 provider/browser research. Authoritative material is linked below. This is a research snapshot, not a claim that every vendor endpoint or product remains unchanged. **Source-backed** denotes an explicit documented capability; **inference** denotes our architectural interpretation; **volatile** denotes details requiring live capability probing or current documentation before deployment.

## Evidence convention

- **Source-backed**: documentation explicitly supports the stated claim, within the model, account, region, SDK, and service version it describes. An API family supporting something does *not* imply every model supports it.
- **Inference/recommendation**: design choice derived from documentation, not vendor guarantee.
- **Volatile/runtime-discovered**: model availability, quota, cost, limits, retention eligibility, tier, endpoint shape, beta status, SDK version, authentication configuration, usage-reset timing, UI selectors and browser behavior. Refresh and record evidence with each route registration.
- Source URLs were collected/rechecked on **2026-10-09**; where a fetched page redirects, the canonical destination is included. Individual vendor documentation pages may update without dated version tags. No stable API/SDK version is inferred from a URL.

## OpenAI — Responses API (official model API)

**Sources (2026-10-09):**
- https://platform.openai.com/docs/api-reference/responses (HTTP 403 to scripted GET in verification environment; access restricted, not a 404; use working official guide URLs below for independently accessible capability evidence)
- https://developers.openai.com/api/docs/guides/streaming-responses
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/guides/conversation-state
- https://developers.openai.com/api/docs/guides/tools
- https://developers.openai.com/api/docs/guides/images-vision

**Source-backed:** Responses is an official model-request surface with conversation/state continuation, tools, structured output, image input on supported models, and streamed response events. Storage and continuation settings are API-specific and should be read from the current reference.  
**Inference:** choose a direct Responses route when EmaraAI owns tool dispatch/state and wants a lower-level model adapter; it is not a repository-aware coding harness by itself.  
**Volatile/uncertain:** specific response-storage default, organization retention/ZDR eligibility, per-model tool/vision support, rates, pricing, max context and streaming transport extensions must be checked against current account settings and live model docs. Earlier research indicated `store` defaults to true; because the API reference could not be fully fetched during this ledger pass, treat that precise default as **requires re-verification**, not as a presently verified fact. Version: HTTP API documentation snapshot 2026-10-09; no immutable API version asserted.

## OpenAI — Agents SDK and sandbox agents

**Sources (retrieved 2026-10-09):**
- https://openai.github.io/openai-agents-python/
- https://openai.github.io/openai-agents-python/agents/
- https://openai.github.io/openai-agents-python/running_agents/
- https://openai.github.io/openai-agents-python/sandbox_agents/

**Source-backed:** Official SDK documents agent loops, tools, handoffs, guardrails, sessions, tracing and sandbox agents with workspaces/resumable sandbox sessions. It states the SDK uses Responses by default for OpenAI models and differentiates owning the model loop (Responses) from SDK-managed turns/tools.  
**Inference:** this is a distinct coding/agent-runtime route, not equivalent to a raw API completion. Workspaces and external side effects still require EmaraAI ownership/evidence policy.  
**Volatile:** SDK package version, sandbox client/platform support, execution locality, provider/tool availability and associated prices. Version: online SDK docs current at review date; implementation must pin exact package version.

## OpenAI — Codex CLI/SDK and app-server

**Sources (2026-10-09):**
- https://developers.openai.com/codex/app-server (redirects to https://learn.chatgpt.com/docs/app-server)
- https://developers.openai.com/codex/cli/reference
- https://github.com/openai/codex/tree/main/sdk/typescript
- https://github.com/openai/codex

**Source-backed:** Codex exposes CLI/SDK/app-server integration surfaces, thread/turn lifecycle and resumable threads as documented in app-server/CLI references.  
**Inference:** classify it as a native coding agent with repository/workspace binding, not a generic chat API. Treat thread resume as context/job continuity only; it does not alone prove exactly-once external side effects.  
**Volatile:** protocol methods/events, binary version, Windows isolation behavior, auth plan, usage caps, session state. Version context: no pinned production version; pin binary and schema per adapter.

## Anthropic — Claude Messages API

**Sources (2026-10-09):**
- https://platform.claude.com/docs/en/api/messages/create
- https://platform.claude.com/docs/en/build-with-claude/streaming
- https://platform.claude.com/docs/en/build-with-claude/tool-use/overview
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- https://platform.claude.com/docs/en/api/rate-limits

**Source-backed:** Messages accepts conversational messages and supports model-dependent image input, tool use, streaming and structured output. The regular Messages API is organized around sending conversation messages; client-managed history is necessary absent another explicit persistent service. Rate limiting and spend/usage constraints are documented separately.  
**Inference:** EmaraAI must store canonical context and tool side effects for a plain Messages route; API retries after ambiguous network termination must follow request-side effect/receipt policy.  
**Volatile:** exact tier quotas, retry-after behavior, rate-limit dimensions, model capabilities, caching and pricing. Version context: versioned `anthropic-version` header must be recorded by adapter; none assumed here.

## Anthropic — Claude Agent SDK / Claude Code

**Sources (retrieved 2026-10-09):**
- https://platform.claude.com/docs/en/agent-sdk/overview (redirects to https://code.claude.com/docs/en/agent-sdk/overview)
- https://code.claude.com/docs/en/agent-sdk/overview
- https://code.claude.com/docs/en/cli-reference

**Source-backed:** Agent SDK exposes the Claude Code agent loop, coding-oriented tools and hooks/permissions in a host-controlled integration; CLI has noninteractive usage and session controls documented in its reference.  
**Inference:** model as a coding-agent process route with explicit cwd/revision, tool permission envelope, trace export and external workspace fencing; provider session restore never substitutes for resource reconciliation.  
**Volatile:** npm/SDK version, CLI command flags, commercial authorization/bundling, supported OS and headless permission behavior. Version: pin installed CLI/SDK versions when benchmarked.

## Anthropic — Claude Managed Agents

**Sources (retrieved 2026-10-09):**
- https://platform.claude.com/docs/en/managed-agents/overview
- https://platform.claude.com/docs/en/managed-agents/migration

**Source-backed:** The managed-agent offering documents hosted agent execution/session and managed infrastructure distinct from the locally operated Agent SDK.  
**Inference:** represent as a *different execution locality and persistence/credential class*, with separately declared controls for files, network, approvals and artifacts. Do not silently substitute managed execution for a local-only agent route.  
**Volatile:** beta/availability status, region, pricing, quotas, support for interruption/recovery and permission semantics. Version: managed service docs snapshot at date of review.

## Google — Gemini native SDK/API

**Sources (retrieved 2026-10-09):**
- https://ai.google.dev/gemini-api/docs/get-started
- https://ai.google.dev/gemini-api/docs/function-calling
- https://ai.google.dev/gemini-api/docs/structured-output
- https://ai.google.dev/gemini-api/docs/image-understanding
- https://ai.google.dev/gemini-api/docs/interactions

**Source-backed:** Official Gemini API documents function calling, schema-constrained structured output and multimodal model inputs on eligible models; SDK/direct-API pathways expose native features.  
**Inference:** prefer native Gemini route when a feature does not map exactly to a compatibility endpoint; capability-probe per model, not merely per vendor.  
**Volatile:** preview/GA service availability, exact model+tool combinations, supported MIME/media limits, thinking configuration, context, prices and rate limits. Version: online docs 2026-10-09; pin SDK/model alias when selected.

## Google — Gemini OpenAI compatibility

**Sources (retrieved 2026-10-09):**
- https://ai.google.dev/gemini-api/docs/openai
- https://ai.google.dev/gemini-api/docs/partner-integration

**Source-backed:** Google documents an OpenAI-library-compatible endpoint/path as a practical interoperability option, with Gemini-specific extension fields/features that may not map one-to-one.  
**Inference:** treat compatibility mode as its *own route* with tested, smaller/independently asserted manifest; never infer full direct-Gemini equivalence.  
**Volatile:** compatibility feature mapping, supported SDK versions, beta/preview status, and model-specific support. Version: documentation snapshot 2026-10-09.

## Local/OpenAI-compatible serving — vLLM and generic servers

**Sources (2026-10-09):**
- https://docs.vllm.ai/en/stable/serving/openai_compatible_server/ (canonical redirect destination)
- https://docs.vllm.ai/en/stable/usage/security/ (official current security guidance; replaces dead /serving/security.html URL, which returned HTTP 404)
- https://docs.vllm.ai/en/stable/models/supported_models.html

**Source-backed:** vLLM publishes an OpenAI-compatible HTTP serving surface. Feature support depends on model/template/server configuration and is not guaranteed by endpoint spelling.  
**Inference:** gateway should capability-probe tool calling, JSON/schema adherence, vision, stream cancellation, concurrency and usage metering; reverse proxy, network isolation and scoped auth are mandatory when exposing local endpoints. Official vLLM security guidance explicitly states that `--api-key` protects the `/v1`, `/v2`, and `/inference` prefixes but not other endpoints such as `/invocations`; do not rely on API-key-only exposure. Deploy behind a restrictive network boundary/reverse proxy. Source-backed security claim: https://docs.vllm.ai/en/stable/usage/security/ (section "API Key Authentication Limitations"; accessed 2026-10-09).  
**Volatile:** vLLM release, launch flags, endpoint auth implementation changes, supported models, GPU compatibility, OS support and resource footprint. Version: stable-doc alias can advance; benchmark against a pinned container/package revision.

## Web Chat browser providers: ChatGPT / Claude / Gemini

**Source context:** official API/SDK documentation above does **not** warrant stable automation selectors or guaranteed programmatic access to consumer Web Chat UI. Browser Web Chat route claims are based on **read-only Compact source observations**, not API vendor guarantees:
- `H:\EmaraAI-Hub-Compact\src\emaraai_hub\drivers\delivery.py`
- `H:\EmaraAI-Hub-Compact\extension\sw.js`
- `H:\EmaraAI-Hub-Compact\tests\js\delivery-window-reliability.cjs`

**Observed in Compact:** shared bounded delivery pool; remembered and rediscovered canonical EmaraAI window; active-tab loading; exact ChatGPT conversation URL verification; restored-message matching; post-send observation/uncertain outcome; serialized shared-window operations; unleased-only sweep. Local JS reliability test returned `5 shared delivery window checks passed`.  
**Inference:** Next must give Web Chat a weaker receipt confidence class than official APIs and use quarantine for ambiguous post-submit failures.  
**Volatile:** consumer UI selectors, service usage caps/terms, authentication state, local extension lifecycle and browser loading behavior. Provider-specific browser windows cannot be merged into ChatGPT's shared pool without explicit compatibility evaluation.

## Cross-provider decision and verification requirements

1. **Hard-match manifest:** chat, coding/repo binding, tools, structured output, vision, streaming, resume, cancellation, privacy/persistence, cost class, limits/auth, locality, tool execution owner, and browser window policy.
2. **Separate source facts from decisions:** support at provider-family level is not proof of support in every model, auth tier or route.
3. **Record evidence:** capture URL, access date, source section, adapter version, model ID, account/plan capability probe, provider limit evidence and route-policy version.
4. **Safe failover:** do not downgrade privacy, data locality, coding harness, exact-chat checks, or receipt guarantees under fallback.
5. **Browser uncertainty:** timeout after possible submit is not proof of non-delivery; quarantine until transcript/provider receipt/operator resolves it. No blind resend.
6. **P8 release gates:** real-provider capability probes, restart/reconciliation fault injection, zero user-tab adoption, no duplicate unsafe sends, exact chat verification, and durable receipt/audit replay.

**Verification boundary:** This ledger captures inspectable official URL anchors and explicit inference/volatility status. It is not live account capability certification and does not replace controlled provider/API benchmark and fault tests.

## Link integrity verification — 2026-10-09

- Automated external URL sweep of original ledger: **29 HEAD HTTP 200**, **6 HEAD failures**. GET fallback distinguished **three HTTP 200** (Claude Agent SDK URL, Claude CLI reference, Anthropic Agent SDK redirect), **two HTTP 404** (obsolete vLLM /serving/security.html and OpenAI sandbox_agents/quickstart/), and **one HTTP 403** (platform.openai.com API-reference page; access restriction, not proof that the resource was removed).
- Both 404s were replaced with verified current official destinations: https://docs.vllm.ai/en/stable/usage/security/ and https://openai.github.io/openai-agents-python/sandbox_agents/ .
- vLLM 'API Key Authentication Limitations' explicitly states /v1, /v2 and /inference protected prefixes and unauthenticated other endpoints including /invocations; security/network boundary recommendation is source-backed. Accessed 2026-10-09. vLLM docs use floating **stable** version alias; no package release pinned.
- HEAD rejection is *not* classified as dead link where GET succeeds. Redirects are recorded as redirects, not HTTP 404s. External domain access can differ between the verification host, public crawler and an authenticated browser.
- Retain source-access restrictions in the provider evidence model; routes require independent live capability tests before certification.
