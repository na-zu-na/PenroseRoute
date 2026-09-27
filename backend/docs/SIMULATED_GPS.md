# 模拟车辆位置接口

该接口供前端地图联调使用。后端根据**当前配送计划**的车辆路线、起终点、逐站计划到达和离开时间计算模拟坐标。它不接收真实 GPS，不写入车辆当前位置，也不推进订单、站点或路线的业务状态。

## 请求

启动后端后，每秒请求一次：

```http
GET /api/operations/simulated-positions?business_date=2026-09-25&cycle_seconds=120
```

- `business_date` 必填，格式 `YYYY-MM-DD`；查询该日期的 `CURRENT` 计划。不存在时返回 `CURRENT_PLAN_NOT_FOUND`（404）。
- `cycle_seconds` 可选，默认 `120`，允许 `30`–`3600`。当前计划压缩到周期的前 90%，最后 10% 在终点停留，然后从起点重新播放。
- 服务端按 UTC 时间计算周期位置；同一时刻、同一参数的多个客户端看到相同进度。无需启动后台定时任务或单独的模拟器进程。
- 后端默认允许 `localhost` 和 `127.0.0.1` 的 3000、5173 端口跨域访问。其他前端地址通过 `FRONTEND_ORIGINS` 配置，使用逗号分隔的完整 origin。

示例：

```powershell
curl.exe "http://127.0.0.1:8000/api/operations/simulated-positions?business_date=2026-09-25&cycle_seconds=120"
```

## 响应数据

接口使用项目统一的 `{success, code, message, data, request_id}` 响应结构。`data` 包含：

| 字段 | 含义 |
| --- | --- |
| `delivery_plan_id`、`business_date` | 当前计划标识和运营日期 |
| `generated_at` | 本次响应的真实 UTC 时间 |
| `simulated_at` | 压缩后映射到计划时间轴的模拟时间 |
| `cycle_seconds` | 一轮模拟的真实秒数 |
| `vehicles[]` | 当前计划中每条车辆路线的位置，按 `route_no` 排序 |

每辆车包含 `vehicle_id`、`vehicle_code`、`route_id`、`route_no`、`latitude`、`longitude`、`motion`、`next_stop_id`、`path` 和 `source: "SIMULATED"`。`motion` 为 `BEFORE_START`、`MOVING`、`AT_STOP` 或 `FINISHED`。`path` 是可供 GeoJSON `LineString` 使用的 `[longitude, latitude]` 坐标数组；车辆位置字段分别为 `latitude` 和 `longitude`。

前端可在地图组件挂载后按 `1000 ms` 间隔请求，用 `vehicle_id` 复用已有 marker，并将其移动到 `[longitude, latitude]`。卸载时清除定时器。每次响应都带计划 ID，计划版本变化时应替换路线和车辆集合，而不是继续沿旧路线补间。界面必须标注“模拟位置”。

## 轨迹和业务边界

当路线有 `route_metrics.geometry_provider=OSRM` 和有效的 `road_leg_end_indices` 时，`path` 返回已存的道路 GeoJSON 折线，车辆按当前计划行驶时间段沿对应道路路段的累计距离插值；在 Stop 服务期间停留在道路吸附点。旧路线没有 OSRM 来源时，继续按起点、顺序 Stop 和终点之间的直线插值。旧的手写 `route_geometry` 不代表已验证道路路线；可按 [Operations 工作区文档](OPERATIONS_WORKSPACE.md#set-up-a-local-road-router-and-backfill-the-demo-date)启动 OSRM 并补算。

无论哪种几何，车辆坐标仍是**模拟位置**，不能作为实际 ETA、偏航、异常检测或恢复决策的依据。有真实 GPS 时应另建受鉴权的位置上报与存储流程，不应把本接口的模拟坐标写入业务 `locations` 表。

接口只读取当前生效计划，`Candidate` 计划不会作为模拟来源。读取不会修改 `vehicles.current_location_id` 或 `current_location_recorded_at`。
