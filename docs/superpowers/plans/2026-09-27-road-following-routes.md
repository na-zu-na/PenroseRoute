# Road-Following Routes Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task. Checkboxes track completion.

**Goal:** Operations 地图显示沿道路的计划路线，模拟车辆沿同一路线行驶；新规划和事故恢复使用道路距离与时间，避免路线、里程和 ETA 相互矛盾。

**Architecture:** 后端使用可配置的 OSRM 服务。Table API 提供优化矩阵，Route API 按求解后的停靠顺序提供完整 GeoJSON 和每段道路几何。路线及分段索引写入现有 `vehicle_routes.route_geometry` / `route_metrics`；Operations API 继续返回 `path`，模拟位置按对应路段的累计距离插值。路由请求在数据库写事务之外完成。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy、PostgreSQL JSONB、OR-Tools、MapLibre、[OSRM HTTP API](https://project-osrm.org/docs/v5.24.0/api/)。HTTP 客户端优先使用标准库 `urllib.request`，无需新增运行时依赖。

**Spec:** `backend/docs/OPERATIONS_WORKSPACE.md`、`backend/docs/SIMULATED_GPS.md`；本计划的“目标、数据约定和验收”补充现有文档尚未定义的道路路由行为。

## 全局约束与数据约定

- 保留现有计划、停靠点、Operations 和模拟位置接口；不为路线几何新增表或数据库迁移。
- 坐标统一采用 GeoJSON 的 `[longitude, latitude]`；路由请求严格按车辆起点、`sequence_no` 升序停靠点、必要时终点排序。相邻相同位置作为零距离路段处理，不能打乱停靠时间对应关系。
- `route_geometry` 保存完整道路 `LineString`；`route_metrics.geometry_provider = "OSRM"` 表示经过道路路由，`route_metrics.road_leg_end_indices` 与起点后的每个实际行驶路段一一对应。模拟器使用该索引定位当前路段。旧的手写 `LineString` 不能仅凭存在该字段就标记为道路路线。
- `ROUTING_PROVIDER=deterministic|osrm`，默认 `deterministic` 保持现有离线测试行为；演示环境显式设置 `osrm`、`OSRM_BASE_URL`、`OSRM_TIMEOUT_SECONDS=8`。OSRM 模式下超时、`NoRoute`、`NoSegment`、矩阵空值、异常坐标均明确报错，不能悄悄改用直线。配置 URL 指向受控的服务，不把路由请求放到浏览器。
- 原有模拟位置仍标注 `SIMULATED`，不冒充真实 GPS；停靠服务期间保持在道路端点。原有 `cycle_seconds` 播放规则不变。
- OSRM 的道路距离来自最快路线，不必等于几何最短距离；Route 和 Table 使用同一份路网数据与 driving profile。

## 验收标准

1. OSRM 模式下新建 Current/Draft 计划以及事故恢复 Candidate 均有道路折线和可定位的路段边界；规划矩阵取道路距离、时间。
2. `GET /api/operations/workspace` 的 `path` 是道路折线，能区别已验证的 OSRM 线路和旧几何或停靠点直连线；现有地图布局不变。
3. 同一计划的 `GET /api/operations/simulated-positions` 中，车辆行驶点在对应道路折线上；到站停留，随后继续下一段。
4. 现有计划可按业务日期幂等补算几何；补算不改变停靠顺序、计划状态、订单执行状态或历史 KPI。
5. 路由服务异常时不生成伪装为真实道路的路线；旧计划仍可按当前降级显示并明确标识。

## 需特别验证的失败场景

- OSRM 返回 `NoRoute`、`NoSegment`、超时或非 `Ok`：规划不落库半成品；恢复尝试保留明确失败原因。
- Table 矩阵中有 `null` 或维度不符：拒绝传给 OR-Tools，不能拿直线值填空。
- 起点与首站、末站与终点重合：零距离路段与模拟时间对应，避免除零及错误跳点。
- 道路吸附点距业务坐标较远：拒绝超过 250 米的吸附，避免车辆跑到错误道路。
- 恢复路线既有已完成停靠点又有新停靠点：按候选路线最终顺序重算全线，不能沿用旧的整条折线。

---

### Task 1：OSRM 接入与输入验证

**Files:**
- Create: `backend/app/integrations/routing/osrm.py`
- Modify: `backend/app/core/config.py`、`backend/.env.example`、`backend/app/integrations/routing/distance_matrix.py`
- Test: `backend/tests/unit/integrations/routing/test_osrm.py`

**Interfaces:** `OsrmRoutingProvider.build_matrix(locations: tuple[RoutingLocation, ...]) -> RoutingMatrix`；`OsrmRoutingProvider.build_route(waypoints: tuple[RoutingLocation, ...]) -> RoadRoute`。`RoadRoute` 包含 `geometry: dict`、`leg_end_indices: tuple[int, ...]`、`distance_meters: int`、`duration_seconds: int`。公共选择入口依据 `ROUTING_PROVIDER` 返回现有确定性 provider 或 OSRM provider。

- [x] 写失败测试：Table 的 `durations` / `distances` 正确转为整数矩阵；Route 的 `steps=true&geometries=geojson&overview=full` 按 leg 拼成折线及非递减的 `leg_end_indices`（允许零距离 leg）；经纬度顺序正确。
- [x] 运行 `python -m pytest tests/unit/integrations/routing/test_osrm.py -q`，确认测试先失败。
- [x] 实现配置、HTTP 超时、响应形状及有限坐标验证、250 米吸附上限；Table 遇不可达点或 Route 遇无路时抛明确的集成异常。相邻重复 waypoint 形成零距离 leg。
- [x] 运行上述测试及 `python -m pytest tests/unit/integrations/routing/test_distance_matrix.py -q`，确认通过。

### Task 2：正常规划保存道路几何

**Files:**
- Modify: `backend/app/modules/planning/workflow.py`、`backend/app/modules/planning/finalizer.py`
- Test: `backend/tests/integration/test_planning_workflow.py`、`backend/tests/integration/test_operations_workspace_http.py`

**Interfaces:** `PlanFinalizer.finalize(..., road_routes: dict[UUID, RoadRoute] | None = None)`；字典以 `vehicle_id` 为键。确定性模式仍写 `route_geometry=None`，OSRM 模式为所有求解路线写几何和 leg 索引。

- [x] 写失败测试：求解后使用 `facts.locations`、车辆起点和有序停靠点取得 Route；工作区返回密集 `path`；路由失败时不新增 Current/Draft。
- [x] 运行这两个集成测试，确认失败点符合预期。
- [x] 在 `PlanningWorkflow` 的求解及验证之后、数据库写事务之前取得所有路线几何；`PlanFinalizer` 原子保存几何及来源。保持现有并发事实复核。
- [x] 运行两组集成测试，确认确定性旧用例和 OSRM 新用例通过。

Task 1–2 代码与测试已完成。当前工作区原有未提交变更涉及同一规划文件，因此本轮没有代替用户提交这些变更。

### Task 3：事故恢复保存候选路线几何

**Files:**
- Modify: `backend/app/modules/recovery/deterministic_workflow.py`、`backend/app/modules/recovery/deterministic_context.py`（`deterministic_orchestration.py` 已通过公共矩阵入口选择 OSRM，无需修改）
- Test: `backend/tests/integration/test_recovery_workflow.py`

**Interfaces:** 恢复继续使用 Task 1 的道路矩阵和 `RoadRoute`；生成 Candidate 前按最终的 `preserved + solver_route.stops` 次序计算几何，保留未变更路线已验证的几何；重建路线不得复制与新停靠顺序不符的旧几何。

- [x] 写失败测试：新车辆路线、重建路线、含已完成停靠点的路线均取得正确几何；路由失败时不产生 Candidate，恢复尝试记录失败诊断。
- [x] 运行 `python -m pytest tests/integration/test_recovery_workflow.py -q`，确认失败。
- [x] 在写候选计划的事务之前完成外部路由请求；把结果传入 `_persist_candidate_routes`；保留既有恢复并发校验与审核流程。
- [x] 运行该测试及相关恢复 API 测试，确认通过。

### Task 4：模拟车辆沿道路行驶

**Files:**
- Modify: `backend/app/modules/operations/simulation.py`
- Test: `backend/tests/unit/modules/operations/test_simulation.py`、`backend/tests/api/test_operations.py`

**Interfaces:** `position_for_route(route, simulated_at)` 签名不变。只有 `geometry_provider == "OSRM"` 且 leg 索引有效时按当前行驶时间段在对应道路 leg 上按累计距离插值；旧路线保持原行为并通过来源字段标识。

- [x] 写失败测试：弯折道路中点不在起终点直线上；到站停留；继续下一 leg；零距离 leg 和播完终点不跳点；旧数据仍可读取。
- [x] 运行上述单元测试，确认失败。
- [x] 实现按路段累计距离插值；返回的 `vehicles[].path` 使用同一条已存路线几何，避免地图线与车辆点分离。
- [x] 运行单元和 API 测试，确认通过。

Task 3–4 联合回归通过：路由、规划、恢复、Operations 相关 92 个测试通过。当前工作区仍有之前的未提交变更，本轮没有混合提交。

### Task 5：旧计划补算、来源标签及文档

**Files:**
- Create: `backend/app/jobs/backfill_road_geometry.py`
- Modify: `backend/app/modules/operations/workspace.py`、`backend/docs/OPERATIONS_WORKSPACE.md`、`backend/docs/SIMULATED_GPS.md`、`../ai-delivery-operations/app/operations/operations-api.ts`、`../ai-delivery-operations/app/operations/operations-map.tsx`（前三项位于 `PenroseRoute`）
- Test: `backend/tests/integration/test_backfill_road_geometry.py`、`backend/tests/integration/test_operations_workspace_http.py`、`app/operations/operations-api.test.mjs`

**Interfaces:** 补算命令 `python -m app.jobs.backfill_road_geometry --business-date YYYY-MM-DD [--force]`；工作区 route 增加 `road_aligned: bool`，仅 OSRM 来源为 `true`。前端根据该字段显示道路路线或未验证路线标签，沿用现有 `path` 绘制，不改页面排版。

- [x] 写失败测试：指定日期的 Current/Candidate 路线按已有停靠顺序补算；重复执行不重复改写；`--force` 可替换种子数据里的手写几何；失败时不覆盖旧几何；`road_aligned` 与来源一致。
- [x] 运行相关测试，确认失败。
- [x] 实现每条路线先请求 OSRM、成功后单独提交补算的命令；更新工作区及前端来源标签；更新两份接口文档和演示环境的 OSRM 启动、补算步骤。
- [x] 运行路由、规划、恢复、Operations 联合回归（96 个通过）、前端 Operations 测试（4 个通过）及 `npm run build`（通过）；隔离数据库内验证同一业务日期的工作区与模拟位置 `path` 一致，包括未补算路线的返程段。
- [x] 接入实际 OSRM 后，对演示业务日期执行补算并核对路线弯折、车辆贴线和来源标签；记录 OSRM 版本及路网数据日期。实际数据库的计划属于 `2026-09-27`：Current 2 条、Candidate 3 条均成功补算。Current 路线分别有 750/1404 个道路线点，车位到对应折线的距离为 0 米，`road_aligned=true`；来源标签的前端测试通过。OSRM v6.0.0、`osrm-bindings==0.3.0`，Geofabrik 路网数据截至 `2026-09-26T20:22:51Z`，本机 PBF SHA-256 为 `BCCEE760FE8341F28C30D6CA01A5C9DDA9DF8268BB916B220159C1EF4BC42739`。

Task 5 的真实路网数据和接口验收已完成。实际 OSRM 在 leg 交界处返回了约 0.6 米的吸附坐标差；解析器现仅在 leg 交界处允许最多 2 米的连接，其他不连续段仍报错。已重启无响应的前端开发进程；`http://localhost:5173/api/operations/workspace?business_date=2026-09-27` 返回 2 条 `road_aligned=true` 路线。浏览器截图辅助进程启动失败，因此本轮以真实接口数据和前端来源标签测试核对，未进行截图验收。工作区仍有先前未提交变更，本轮没有混合提交。

本轮路由、规划、恢复、Operations 相关回归为 83 个通过，前端 Operations 测试 6 个通过。全量后端回归为 376 个通过、22 个失败；多数旧测试直接依赖本机 `2026-09-25` 的 Current 计划，而当前数据库的 Current/Candidate 位于 `2026-09-27`；另有 P0 OpenAPI 固定清单未纳入已新增的 workspace/draft 端点。这些全量失败未计入本轮 OSRM 验收通过项。

审查修复：旧路线的 `STOP_CONNECTORS` 包含终点，与模拟轨迹一致；混合道路来源时，地图按当前选中路线显示其来源，未选中时提示存在混合路线。

## 实施顺序与完成判据

先完成 Task 1–2 可看到新计划的真实道路折线；完成 Task 4 才能声称车辆沿道路移动；Task 3 保证恢复路线不会退化；Task 5 使现有演示计划也生效。全量回归至少运行路由、规划、恢复、Operations 的现有测试。最终用同一业务日期验证 `/api/operations/workspace` 的道路 `path` 与 `/api/operations/simulated-positions` 的车辆点位，并记录 OSRM 服务版本与路网数据日期。
