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

`GPUMonitor.capture()` 每次生成一个完整硬件采样。单线程采样器将其交给 `TelemetryStore`，后者加上时间戳和递增序号，保留最近 120 帧，并向历史接口与 WebSocket 发布同一格式的消息：

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

前端 `TelemetryClient` 将历史接口和 WebSocket 消息统一送入 `ingest()`，按流标识和序号去重。服务端重启后切换到新流；断线保留旧帧但显示连接状态。采样缺失不会复制上一帧冒充新数据。状态 HTTP 接口在这份原始快照上增加请求时计算的 `health`，包括整帧、Windows 进程计数和显示配置的新鲜度；另附带最近一次按需硬件概况 `activity_probe` 和指令管线 `pipeline_probe`，其时间戳与实时帧分别标明。

| 接口 | 用途 |
| --- | --- |
| `GET /api/status` | 最新快照及 `health`；首次采样前返回 503 |
| `GET /status.json` | 根路径的完整最新快照；与 `/api/status` 内容相同 |
| `GET /status.md` | 根路径的人类可读状态摘要，`text/markdown` |
| `GET /metrics` | 根路径的 Prometheus 文本指标；默认只输出系统和设备指标 |
| `GET /metrics?include_processes=true` | 另外输出带 GPU、PID、进程名标签的逐进程指标 |
| `GET /api/history?limit=120` | 最近 1–120 帧，按时间升序 |
| `POST /api/activity-probe?gpu=0` | 按需采集 5 秒设备级 GPU 硬件活动；需要本机 Nsight Systems CLI 和性能计数器权限 |
| `POST /api/activity-probe?gpu=0&kind=pipelines` | 单独采集 AD10x 的 Tensor、FMA Light/Heavy、ALU、SFU 管线吞吐；不会自动启动 |
| `GET /api/activity-probe[?kind=pipelines]` | 最近一次对应类型的采样结果；服务端重启后清空 |
| `GET /api/display-events?limit=50` | 最近 1–200 条显示配置事件 |
| `WS /api/ws` | 实时快照；慢客户端只保留最新一帧 |

三个根路径端点只格式化 `TelemetryStore` 已有的最新帧，不会因请求而额外采样；前端开发服务器也会将这些路径代理到后端。首次采样前均返回 503。整帧超过 5 秒未更新时，状态接口明确标记过期，`/metrics` 将 `gpu_info_up` 置 0 并停止输出旧的硬件读数；Windows 进程计数超过 30 秒时停止输出旧的逐进程读数。Prometheus 可通过时间戳指标计算样本年龄。指标使用 `gpu_info_` 前缀、字节／赫兹等基础单位和 0–1 比率；缺失计数器不输出样本。逐进程序列会随 PID 更替而增长，只有确实需要长期抓取时才启用。进程本地用量、专用计数和 NVML 整卡显存口径不同，不能把进程序列求和当作真实整卡占用。

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
- 每个进程同时保留 Windows `GPU Engine` 的 `engtype` 分类到 `engine_utilization`；同类多个实例取最忙值，并在最近 120 帧、`/status.json`、趋势页和带进程标签的 `/metrics` 中提供。这里的 3D、Compute、Copy 是 WDDM 队列分类，不等于 CUDA、RT Core 或 Tensor Core 的硬件使用率。本机训练中的 Python 进程约 87% SM 负载时，WDDM 将其约 89% 的活动记在名为 `3d` 的队列；因此 `compute` 缺失不代表 CUDA 空闲。
- `gpus[].power.usage` 是 NVML 读取的整卡功率。功率并未拆分到进程或核心单元；各类引擎可能同时使用同一硬件，不能按百分比分摊瓦数。后续若建立模型，必须用独立负载校准，并把估算值、误差及无法归因部分与实测整卡功率分开显示。
- 首页和趋势页共享最近一次按需 Nsight Systems 结果：以 10 Hz 仅采集 5 秒整卡 GPU Metrics，不采集 CUDA/WDDM API 或 CPU 轨迹。首页展示计算派发、计算 warp、SM 活跃、SM 指令发射、Tensor 活跃和显存读取；趋势页再展示异步派发、活跃 SM 未分配 warp 槽位、显存写入与图形 warp，以及窗口平均值、峰值和非零样本数。同一份默认 `ad10x` 报告已包含这些指标，增加显示无需增加采集计数器。采样时间与实时 NVML 帧分别标明。
- 另有独立的 AD10x 指令管线短采样，10 Hz、5 秒，首页和趋势页可手动触发。它以 Nsight 配置中的实时计数器观察 Tensor、FMA Light/Heavy、ALU、SFU/XU；其采样窗口与概况采样分开。FMA Light/Heavy 不能直接解释为 FP32 CUDA Core 的独占使用率，也可包含整数乘法；ALU 主要反映整数/逻辑指令，XU 还包括类型转换。各管线百分比采用不同峰值基准，不能相加。该集合在本机 RTX 4090 成功单次采集，其他芯片是否可用由 Nsight 配置验证，失败会返回错误。指令条数在本机报告中出现有符号溢出，网页仅显示 10 Hz 粗粒度管线吞吐率，不显示不可信的条数。
- GPU Metrics 属于整张 GPU，无法把 Tensor 或指令活动归给某个 PID；页面另列同期独立取得的进程 NVML SM 负载。计算派发和计算 warp 不能等同于 FP32/整数 CUDA Core 利用率，也可能包括图形 API 的计算着色器；Tensor 低并不能单独证明标量计算管线高。不同百分比不可相加，不能推算单元功率。可用 `GPU_INFO_NSYS_PATH` 指定 CLI 路径；缺少工具或计数器权限时返回明确错误。常驻采样不启动 Nsight。
- 趋势页“进程指标”可切换为当前选中指标排名前 3/5/8 的进程曲线，并按 GPU 过滤；排名只使用最新有效值，缺失值不当成 0。不同进程使用不同颜色和带 PID 的图例。整卡硬件计数器不参与进程排名，因为它们没有进程归属。
- 本机 Nsight Compute 2022.3 的 AD102 指标清单还包含 FP64、LSU、Tensor HMMA/IMMA、warp 停顿等更细计数器，这些尚未验证为低开销实时读数。将常规 FMA/ALU 指标并入 Nsight Systems 默认 10 Hz 集合会要求多次采集，无法生成有效报告；独立实时计数器最小集合则成功。精确的指令类型、Tensor 指令数量以及逐 kernel 的瓶颈仍需要 Nsight Compute 深度分析，网页不自动对训练进程启动可能重放 kernel 的分析。
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
