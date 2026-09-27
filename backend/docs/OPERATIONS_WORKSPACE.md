# Operations workspace API

`GET /api/operations/workspace?business_date=YYYY-MM-DD` returns a successful empty-plan snapshot when a business date has no plan. `readiness` counts all orders and distinct usable vehicle/driver pairs overlapping that date's order window. `plan` is the Current Plan if present; otherwise the latest reviewable Draft; otherwise `null`. The response also includes `routes`, `alerts`, `unassigned`, `on_time_rate`, and `constraint_summary`.

Each route has `id`, `vehicle`, `driver`, `vehicle_status`, `route_execution_status`, `distance_km`, `duration_min`, peak-load `utilization`, stop sequence, `path`, `geometry_source`, and `road_aligned`. Each stop includes order code, kind, location name and coordinates, planned arrival, execution status and persisted risk. An active alert may also mark its order at risk. Open incidents are returned separately from risk alerts in the shared `alerts` array. `on_time_rate` is null until completed deliveries have actual arrival measurements.

`geometry_source=STORED_GEOMETRY` means a plan route has a stored GeoJSON LineString. `STOP_CONNECTORS` means the path joins the recorded start, stops, and terminal location; it is **not road-following geometry**. A stored line can also be an old hand-drawn line. `road_aligned=true` requires stored geometry with `route_metrics.geometry_provider=OSRM`; only this value identifies a road-routed line. All other routes have `road_aligned=false`. When sources are mixed, the map shows a mixed label and identifies the selected route's source.

OSRM plans save the full `[longitude, latitude]` GeoJSON line and `road_leg_end_indices` for the ordered stops (and an extra terminal leg when the final destination differs). The simulated-position endpoint follows those saved legs. Legacy routes retain the previous stop-to-stop simulation until backfilled.

## Set up a local road router and backfill the demo date

Use the Windows OSRM installation in [`backend/tools/osrm`](../tools/osrm/README.md). Its driving profile and Malaysia–Singapore–Brunei extract serve both Table and Route requests. From `PenroseRoute/backend`, start the prepared service in a separate terminal and check it:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\osrm\scripts\start-osrm.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\osrm\scripts\test-osrm.ps1
```

Set `ROUTING_PROVIDER=osrm`, `OSRM_BASE_URL=http://localhost:5000`, and `OSRM_TIMEOUT_SECONDS=8` in `backend/.env`, then restart the API so new planning and recovery requests use OSRM. Backfill existing Current and Candidate plans for the date actually present in the database:

```powershell
.venv\Scripts\python.exe -m app.jobs.backfill_road_geometry --business-date 2026-09-27 --force
```

`--force` replaces old hand-drawn lines. Without it, routes with any stored line are skipped; missing geometry is filled, and repeated runs do not rewrite completed routes. Each route is requested outside a database write transaction and committed separately. If OSRM fails, that route keeps its old line and the command exits nonzero with the failed route ID and error code. Historical stop order, plan/route status, order execution, distance, duration, and plan KPIs are unchanged.

On 2026-09-27, OSRM v6.0.0 with `osrm-bindings==0.3.0` and the [Geofabrik Malaysia–Singapore–Brunei extract](https://download.geofabrik.de/asia/malaysia-singapore-brunei.html) (OSM data through `2026-09-26T20:22:51Z`; local PBF SHA-256 `BCCEE760FE8341F28C30D6CA01A5C9DDA9DF8268BB916B220159C1EF4BC42739`) backfilled five routes: two Current and three Candidate, with no failures. The two Current routes returned 750 and 1404 road coordinates from `GET /api/operations/workspace`; both were `road_aligned=true`, and `GET /api/operations/simulated-positions` returned the same paths with vehicle coordinates on their polylines. The API returns a Current Plan on the map; Candidate geometry can be inspected through the stored plan or promoted through normal approval. Do not label old stored lines as road-aligned. When replacing the extract, record its new source date and local file hash before rerunning `--force`.

## Draft lifecycle

- `POST /api/planning/drafts` with `{"business_date":"YYYY-MM-DD"}` runs normal fact validation and OR-Tools planning, then persists a validated `DRAFT`. It returns `PLAN_DRAFT_CREATED` and the same summary structure as `POST /api/planning/generate`.
- Repeating the request for a date with no Current Plan marks the previous latest draft `CANCELLED` and creates a higher version. If a Current Plan exists, the request returns `409 CURRENT_PLAN_ALREADY_EXISTS`.
- `POST /api/planning/drafts/{plan_id}/confirm` atomically confirms only the latest validated draft for its date. It rejects a stale/cancelled draft, an existing Current Plan, or changed planning inputs; on success it returns `PLAN_CONFIRMED` and the Current Plan record.
- Existing `POST /api/planning/generate` retains its immediate Current Plan behavior for existing clients.

`GET /api/operations/simulated-positions?business_date=YYYY-MM-DD` remains the position source for Current Plans. It is a read-only simulation with periodic cycling, not live GPS or a source of ETA/risk truth.
