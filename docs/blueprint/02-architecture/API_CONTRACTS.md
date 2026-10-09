# API and Contract Principles

## API Families
- Owner/UI API.
- Agent/MCP API.
- Worker protocol.
- Provider adapter interface.
- OpenAI-compatible outward API.
- Event/stream subscription.

## General Contract Rules
- versioned.
- typed.
- idempotency key on mutations where applicable.
- optimistic version/ETag for mutable resources.
- explicit error codes.
- actionable fix.
- no ambiguous null-as-error responses.

## Mutation Response
Return:
resource state/version, operation_id, trace_id, next allowed actions.

## Errors
Categories:
INVALID_INPUT, CONFLICT, NOT_FOUND, FORBIDDEN, APPROVAL_REQUIRED, LIMIT_BLOCKED, RESOURCE_BUSY, PROVIDER_UNAVAILABLE, UNCERTAIN_OUTCOME, INTERNAL.
Every error declares retry_safe boolean or strategy.

## Pagination
All unbounded lists paginated; aggregate counts calculated independently from page limit.

## Streaming
Streaming cancellation must release provider/worker permits.
Events include sequence/cursor for reconnect.

## MCP
Keep Compact advantage: small surfaces, few parameters, help/fix messages, batch support.
Do not expose dozens of provider-specific tools if one capability contract suffices.

## Compatibility
API schema changes versioned and migration tested. UI does not access DB directly.
