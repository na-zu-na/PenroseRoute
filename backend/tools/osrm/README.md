# Windows 本地 OSRM

OSRM 根据 OpenStreetMap 道路网络提供 Route、距离、时间、距离／时间矩阵和道路 GeoJSON。它单独运行；FastAPI 通过 HTTP 使用它，OR-Tools 仍负责车辆分配及路线顺序。

本目录位于 `PenroseRoute/backend/tools/osrm/`。在 `PenroseRoute/backend` 目录执行：

```powershell
.\tools\osrm\scripts\setup-osrm.ps1
.\tools\osrm\scripts\start-osrm.ps1
```

另开终端检查 Route 和 Table API：

```powershell
.\tools\osrm\scripts\test-osrm.ps1
```

脚本可从其他工作目录以绝对路径调用。服务只监听 `http://localhost:5000`。后端配置：

```env
OSRM_BASE_URL=http://localhost:5000
ROUTING_PROVIDER=osrm
```

修改 `backend/.env` 后重启 FastAPI，新的规划和事故恢复请求才会读取 OSRM 配置。已有计划的路线另行补算；例如当前本机演示计划位于 `2026-09-27`：

```powershell
.venv\Scripts\python.exe -m app.jobs.backfill_road_geometry --business-date 2026-09-27 --force
```

`--force` 会替换已有的手绘路线几何，但不会重新计算历史里程、ETA 或 KPI。接口验收方法见 [`OPERATIONS_WORKSPACE.md`](../../docs/OPERATIONS_WORKSPACE.md)。如果 PowerShell 禁止运行 `.ps1`，可用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\osrm\scripts\test-osrm.ps1` 运行检查脚本。

`ROUTING_PROVIDER` 默认仍为 `deterministic`，不启动 OSRM 时现有后端可照常运行。`setup-osrm.ps1` 优先使用 Python 3.12；若没有，则使用已安装的 64 位 Python 3.12+。OSRM 安装在独立的 `.venv/`，不修改 FastAPI 的虚拟环境。脚本固定使用已验证的 Windows 预编译 `osrm-bindings==0.3.0`（OSRM 6.0.0），不从源码编译。

地图数据下载到 `data/malaysia-singapore-brunei-latest.osm.pbf`。大型地图与生成的 `*.osrm*` 文件被 Git 忽略。再次运行安装脚本会跳过已完成的预处理；地图更新后明确运行 `setup-osrm.ps1 -Force` 才重新处理。不要同时运行两个预处理进程。

如果安装脚本报告没有可用 Windows wheel、缺少 `car.lua` 或 CLI，先核对包版本及官方发布文件；不要改装 C++ 源码包。运行 `start-osrm.ps1` 前必须完成 setup。
