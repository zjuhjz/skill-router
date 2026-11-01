# skill-router migration boundary

The production router remains at the repository root during the monorepo
transition. Keeping it in place preserves the existing CLI, MCP integration,
discovery, credentials, fallback, health, and circuit-breaker behavior.

Move it into this package only after external Spec and Eval Report integration
tests protect those behaviors. This directory is intentionally not a second
router implementation.
