# Model Router and Provider Gateway

## Goal
Choose the best viable route for a task based on measured performance, not vendor preference.

## Route Dimensions
- provider.
- model.
- mode.
- effort.
- harness.
- execution environment.
- context strategy.

## Hard Filters
- capability required.
- security/privacy compatibility.
- provider availability.
- usage limit.
- max context.
- tool support.
- repository binding if required.
- project policy.

## Scoring Inputs
- task-type benchmark.
- agent identity competence with route.
- recent reliability.
- expected cost.
- latency.
- availability probability.
- rework rate.
- owner preference.
- data locality.

## Dynamic Escalation
Example:
cheap/fast model for discovery → stronger model when complexity/failed checks crosses threshold.
Escalation creates trace explaining why.

## Fallback
Must preserve required capabilities.
A fallback route never silently changes privacy/persistence semantics.

## Routing Overrides
Owner/Master can pin:
- provider.
- model.
- no API.
- no web.
- local only.
- maximum spend.
Override is logged.

## Exploration
Small configurable percentage of eligible tasks may test alternative route for learning, never for high-risk tasks without consent.

## Provider Health
Track:
- auth.
- latency.
- timeout.
- error rate.
- rate limit.
- reset time.
- browser/connector status.
- API quota.

## Cost
Normalize:
- API tokens/currency.
- subscription message budget estimate.
- compute time.
- operator intervention.
Primary optimization remains accepted outcome, not cheapest raw call.
