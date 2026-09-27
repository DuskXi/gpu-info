# GPU Monitor

本地 NVIDIA GPU 监视器。后端通过 NVML 读取硬件数据，前端提供总览、进程显存表和最近 120 帧的单指标趋势。

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
  "source": "NVML",
  "errors": []
}
```

前端 `TelemetryClient` 将历史接口和 WebSocket 消息统一送入 `ingest()`，按流标识和序号去重。服务端重启后切换到新流；断线保留旧帧但显示连接状态。采样缺失不会复制上一帧冒充新数据。

| 接口 | 用途 |
| --- | --- |
| `GET /api/status` | 最新快照；首次采样前返回 503 |
| `GET /api/history?limit=120` | 最近 1–120 帧，按时间升序 |
| `WS /api/ws` | 实时快照；慢客户端只保留最新一帧 |

## 统计口径

- GPU 显存总占用来自 NVML，包含驱动和未归属到进程的分配。
- Windows WDDM 下，NVML 的进程显存通常不可用。程序用无上下文的 CUDA Driver 设备 LUID 查询，将 Windows GPU 进程计数器精确关联到 NVML 所列 NVIDIA 设备；未匹配的核显计数器不计入。其他系统使用 NVML 的计算和图形进程列表，同一 GPU / PID 重复出现时取较大值。
- Windows 进程值是系统性能计数器的专用分配量，与 NVML 的设备驻留总量口径不同。部分 Windows 版本的该计数器还可能报告偏高，因此不能将进程值相加与设备总量比较。
- GPU 设备指标约每秒采样，Windows 进程计数器在独立工作线程中异步刷新，查询间隔至少两秒；`processes_sampled_at` 标明最近一次进程采样时间。
- 不把 Windows 共享 GPU 内存或核显显存并入 NVIDIA 专用显存。进程合计不一定等于设备总占用。
- 监视器不调用 CUDA Runtime 的 `cudaSetDevice` / `cudaMemGetInfo`。Windows 的 CUDA Driver 查询只读取设备标识，不创建 CUDA 上下文。
- 不可用的可选计数器返回 `null`，趋势图将其显示为缺口。

## 检查

```powershell
python -m unittest discover -s tests
mypy .
flake8 .
cd frontend
pnpm test
pnpm build
```
