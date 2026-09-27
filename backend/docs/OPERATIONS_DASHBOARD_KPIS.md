# Operations Dashboard KPI 数据口径

`GET /api/operations/dashboard?business_date=YYYY-MM-DD` 在原有 `current_plan`、`orders`、`vehicles`、`open_incidents` 等字段外，增加 `on_time`、`regions`、`trends`。日期必须有 `CURRENT` 配送计划；否则仍返回 `CURRENT_PLAN_NOT_FOUND`。

```json
{
  "on_time": {"on_time_deliveries": 2, "measured_deliveries": 3, "rate": 66.7},
  "regions": [{
    "region": "WEST REGION", "orders": 5, "completed": 2,
    "at_risk": 1, "vehicles": 2, "routes": 2, "on_time_rate": 50.0
  }],
  "trends": {
    "comparison_business_date": "2026-09-26", "orders_delta": 2,
    "vehicles_delta": 1, "on_time_rate_delta_points": 16.7,
    "open_incidents_delta": -1
  }
}
```

- **准时率**：仅统计当前计划中 `DELIVERY` 停靠点已完成、订单已完成且有 `actual_arrival_at` 的订单。实际到达时间不晚于停靠点时间窗终点（缺失时用订单时间窗终点）视为准时。`rate` 为百分数，分母为 `measured_deliveries`；分母为零时返回 `null`。预测性 `AT_RISK` 不参与准时判定。
- **区域**：按订单送达地址的经纬度落入新加坡五大区域边界归属，与前端地图使用同一份边界来源 `ai-delivery-operations/public/data/singapore-regions.geojson`。后端保存六位小数的压缩副本 `app/modules/operations/regions.json.gz`，可独立部署。区域内车辆和路线按服务该区域的计划分配去重计数，并非车辆实时 GPS 位置。未落入五大区域的地址不列入 `regions`，但仍计入 Dashboard 全局订单数。
- **趋势**：比较所选业务日期和最近一个更早且有 `CURRENT` 计划的业务日期。订单、车辆、未解决异常返回数量差；准时率返回百分点差。缺少可比计划或任一准时率没有实际样本时，对应差值为 `null`。这里比较的是**查询时两天计划的现有状态**，不是某个历史时刻的快照；若需小时级趋势或严格历史回放，需要另建定时 KPI 快照。

前端 Overview 的 Backend 模式读取这些字段，展示准时率、KPI 差值及地图区域面板。Demo 模式仍使用独立演示数据。
