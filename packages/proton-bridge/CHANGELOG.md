# Changelog

## Unreleased

- Breaking: draft idempotency now uses `X-Roboz-Request-Id`. Rename the previous request-ID header on existing drafts before retrying creation to preserve duplicate detection.
- Depend on the renamed `roboshed` distribution and import its email contracts from `roboshed`. Runtime email behavior is unchanged.

## 0.1.0b1

Initial independently installable package. See README for capabilities and setup.
