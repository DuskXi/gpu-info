# GPU Monitor

本地 NVIDIA GPU 监视器。后端通过 NVML 读取硬件数据，前端提供总览、进程显存饼图、进程显存表和最近 120 帧的单指标趋势。

## 启动

需要 Python 3.13、[uv](https://docs.astral.sh/uv/) 和 Node.js / pnpm。

```powershell
uv sync
uv run main.py
```

后端默认监听 `http://127.0.0.1:8001`。如需从其他设备访问，可设置 `GPU_INFO_HOST=0.0.0.0`。

在另一个终端启动前端开发服务器：

```powershell
cd frontend
pnpm install
pnpm dev
```

生产部署先执行 `pnpm build`，后端会自动服务 `frontend/dist`。前端采用 Hash 路由，因此从浏览器直接打开趋势分析也能正常加载。

## 数据流

`GPUMonitor.capture()` 每次生成一个完整硬件采样。单线程采样器将其交给 `TelemetryStore`，后者加上时间戳和递增序号，保留最近 120 帧，并向 HTTP 与 WebSocket 发布同一格式的消息：

```json
{
  "type": "snapshot",
  "stream_id": "8cc6e9...",
  "sequence": 42,
  "timestamp": 1790488800.0,
  "system": {},
  "gpus": [],
  "processes": [],
  "process_utilization": [],
  "windows_adapter_memory": [],
  "display_state": null,
  "display_events": [],
  "source": "NVML",
  "errors": []
}
```

前端 `TelemetryClient` 将历史接口和 WebSocket 消息统一送入 `ingest()`，按流标识和序号去重。服务端重启后切换到新流；断线保留旧帧但显示连接状态。采样缺失不会复制上一帧冒充新数据。

| 接口 | 用途 |
| --- | --- |
| `GET /api/status` | 最新快照；首次采样前返回 503 |
| `GET /api/history?limit=120` | 最近 1–120 帧，按时间升序 |
| `GET /api/display-events?limit=50` | 最近 1–200 条显示配置事件 |
| `WS /api/ws` | 实时快照；慢客户端只保留最新一帧 |

## 统计口径

- GPU 显存总占用来自 NVML，包含驱动和未归属到进程的分配。
- 首页饼图按 GPU 上各进程的 `Local Usage` 本地显存读数切片，缺少 Windows 读数时使用可用的 NVML 进程读数；只展示最大的五个进程，其余合并。最大进程占读数总和至少 70% 时，旁边增加排除该进程后的局部放大饼图，颜色与全体图保持一致。读数包含共享资源时会重复计入，因此饼图表示进程读数的相对分布，不代表互斥的物理显存占比；旁边保留 NVML 整卡已用量核对。
- Windows 每 5 秒通过 QueryDisplayConfig 检查活动显示路径，记录显示器名称、桌面/信号分辨率、刷新率及 HDR 状态。首次读取记为启动基线，之后只在配置改变时追加事件到 `data/display-events.jsonl`；事件同时保存当时 NVML 显存读数和 DWM 进程计数以便对照。旧版 Windows 若只能报告“高级色彩”，HDR 状态显示为未知。
- Windows WDDM 下，NVML 的进程显存通常不可用。程序用无上下文的 CUDA Driver 设备 LUID 查询，将 Windows GPU 进程计数器精确关联到 NVML 所列 NVIDIA 设备；未匹配的核显计数器不计入。其他系统使用 NVML 的计算和图形进程列表，同一 GPU / PID 重复出现时取较大值。
- Windows 进程表以 `GPU Process Memory\Local Usage` 显示“本地显存”，同时保留 `Dedicated Usage` 为“专用记账”，`Total Committed` 为“总提交”。本地显存更接近当前位于 GPU 本地内存的用量；它仍可能包含跨进程共享的窗口表面，不等于进程独占量，也不能相加推算设备总占用。专用记账在部分系统上会明显高于实际驻留，甚至高于物理显存容量。
- `windows_adapter_memory` 是与进程值同轮读取的 Windows 设备级专用/共享使用量，用于在总览页核对计数口径；NVML 的设备占用仍是独立数据源。当前采样无法把 DWM 的进程值拆成自身私有分配、其他程序共享的表面和驱动内部资源。
- GPU 设备指标约每秒采样，Windows 进程计数器在独立工作线程中异步刷新，查询间隔至少两秒；`processes_sampled_at` 标明最近一次进程采样时间。
- 趋势页的进程选择器可以按进程名或 PID 搜索，并按本地显存、CPU、磁盘 I/O、GPU 占用排序。进程 CPU 是进程 CPU 时间增量除以逻辑处理器数后的整机百分比；磁盘 I/O 是进程实际读取与写入字节数的每秒速率，首次采样显示缺失值。
- Windows 进程 GPU 占用取对应 NVIDIA 设备最忙引擎的性能计数器值；不可用时采用 NVML 的进程 SM 负载。缺失值显示为 `—`，不会当作 0 排序。
- 不把 Windows 共享 GPU 内存或核显显存并入 NVIDIA 专用显存。进程合计不一定等于设备总占用。
- 监视器不调用 CUDA Runtime 的 `cudaSetDevice` / `cudaMemGetInfo`。Windows 的 CUDA Driver 查询只读取设备标识，不创建 CUDA 上下文。
- 不可用的可选计数器返回 `null`，趋势图将其显示为缺口。

可用 PowerShell 直接核对 Windows 的原始计数器：

```powershell
Get-Counter '\GPU Process Memory(*)\Local Usage', '\GPU Process Memory(*)\Dedicated Usage'
Get-Counter '\GPU Local Adapter Memory(*)\Local Usage'
```

微软 [DirectX 说明](https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/)解释了 DWM / csrss 的跨进程共享资源重复记账；微软也记录过 [GPU Process Memory 计数器异常偏高](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/gpu-process-memory-counters-report-wrong-value) 的问题。若需要分配对象和迁移过程，使用 GPUView 或 Nsight Systems 的 WDDM trace，而不是仅凭实时计数器判断泄漏。

## 检查

```powershell
python -m unittest discover -s tests
mypy .
flake8 .
cd frontend
pnpm test
pnpm build
```
