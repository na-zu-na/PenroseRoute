# P1 Agent Workstream Implementation Status

## Delivered scope

- The public recovery entry point remains `POST /api/incidents/{incident_id}/recovery`.
- `RECOVERY_ORCHESTRATION_MODE=agent|deterministic` remains the only internal mode switch.
- Agent mode still invokes the deterministic recovery capability once per attempt and keeps the existing scope-expansion, validation, candidate, and dispatcher-approval boundaries.
- Formal Agent evidence now comes from the canonical U01 Base/Candidate comparison after the candidate has been persisted. The previous pre-persistence full-plan projection was removed from the formal path.
- The stored evidence records comparison time/basis, reviewability, per-order assignment and ETA facts, stop-derived handovers, completed freezes, affected vehicles, and U01's explicit unavailable reason for remaining distance and duration.
- The Agent explanation labels the plan as a candidate and never substitutes solver totals for unavailable U01 remaining-plan metrics.
- Deterministic mode remains available without an explanation model.

## U06 boundary

The repository does not currently contain the P1 U06 alert entity, repository, schema, migration, or alert scan service described by the workstream plan. This change therefore does not create an alternative alert table or embed alert scanning in the Agent. Agent tools remain limited to recovery context, one recovery solve, and reading that result. U06 independence tests must be added with the U06 owning workstream when its canonical contract is present.

## Verification record

The Agent and pure U01 comparison tests run without a real language model. Model ordering uses test doubles and template fallback.

The formal HTTP integration suite requires the configured PostgreSQL database. If PostgreSQL is unavailable, a failure during fixture connection is an environment limitation and is not evidence that recovery, OR-Tools, or database assertions passed or failed.

Current local run:

- `tests/agent` plus the pure U01 comparison tests: **55 passed**.
- After initializing the repository schema and reference data in local PostgreSQL, the complete backend suite reports **268 passed**.
