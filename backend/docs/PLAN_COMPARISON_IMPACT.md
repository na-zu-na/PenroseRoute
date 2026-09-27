# Recovery plan impact metrics

`GET /api/recovery-plans/{recovery_plan_id}/comparison` returns `plan_impact` alongside the existing `remaining_metrics`.

`plan_impact` compares the persisted Base and Candidate plan snapshots:

- `base_plan_distance_meters` and `candidate_plan_distance_meters` sum every persisted route's planned distance. `planned_distance_delta_meters` is Candidate minus Base.
- `base_completion_at` and `candidate_completion_at` are the latest planned route end times in each plan. `planned_completion_delta_seconds` is Candidate minus Base; it is `null` if either plan has no route.
- `completed_stops_protected` counts Base stops marked `COMPLETED` whose matching Candidate business action and immutable execution facts are unchanged.
- `unchanged_route_tasks` counts all matching unchanged stop actions, including protected completed stops.

These are **whole-plan planned impacts**, not remaining travel, live GPS estimates, or a probability of success. `remaining_metrics` continues to return `NO_COMPARABLE_REMAINDER_SNAPSHOT` because the plan snapshots do not capture a shared traveled-distance baseline at the comparison time. The Agent page displays the Candidate's validation status instead of inventing a feasibility percentage.
