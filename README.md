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
  "hardware_sampling": {},
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
| `POST /api/activity-probe?gpu=0[&duration=2]` | 采集 1–5 秒设备级 GPU 硬件活动（默认 5 秒）；需要本机 Nsight Systems CLI 和性能计数器权限 |
| `POST /api/activity-probe?gpu=0&kind=pipelines` | 采集 AD10x 的 Tensor、FMA Light/Heavy、ALU、SFU 管线吞吐 |
| `GET /api/activity-probe[?kind=pipelines]` | 最近一次对应类型的采样结果；服务端重启后清空 |
| `GET /api/sampling-control` | JSON 格式的统一采样控制请求文档，无参数不改变状态 |
| `GET /api/sampling-control?action=state` | 所有持续采样开关、实际状态及错误 |
| `POST /api/sampling-control?action=start&target=all&gpu=0&mode=compute&replace=true` | 开启基础监控、同窗硬件计数器和 PCIe 快速采样；支持分别控制 hardware/bandwidth/telemetry |
| `POST /api/sampling-control?action=stop&target=all` | 关闭全部持续采样；有限时长的 Nsight capture 自动结束 |
| `POST /api/hardware-sampling?enabled=true&gpu=0&mode=compute` | 兼容硬件开关；mode 可为 compute/diagnostic/all/activity/cache/pipelines/tensor/scalar/memory |
| `POST /api/hardware-sampling?enabled=false` | 停止共享的连续采样，释放性能计数器 |
| `GET /api/hardware-sampling` | 直接读取快速硬件缓存，附带 PCIe 快速采样状态 |
| `GET /api/hardware-sampling/samples?after=0&limit=100` | 原始 CUPTI 样本环形缓存，支持 sequence/session_id 增量游标 |
| `GET /api/bandwidth-sampling` | 10 Hz NVML PCIe 采样状态及最新值 |
| `GET /api/bandwidth-sampling/samples?after=0&limit=100` | PCIe 增量样本；独立会话与游标 |
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
- 趋势页“进程指标”可切换为当前选中指标排名前 3/5/8 的进程曲线，并按 GPU 过滤；排名只使用最新有效值，缺失值不当成 0。不同进程使用不同颜色和带 PID 的图例。整卡硬件计数器不参与进程排名，因为它们没有进程归属。
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

## 连续训练硬件采样

Windows 上需要 CUDA/CUPTI 12.6 和 MSVC x64 编译工具：

```powershell
.\native\build.ps1
```

默认 helper 位于 `native/bin/cupti_stream.exe`（生成文件不入 Git），CUPTI DLL 默认路径为 CUDA 12.6 的 `extras/CUPTI/lib64`。可以通过 `GPU_INFO_PM_HELPER` / `GPU_INFO_CUPTI_PATH` 指定位置。未构建、不支持或无权限时返回明确错误，NVML/进程监控继续运行。指标组为本机 AD102 实测组合，其他架构需要通过配置和解码验证，不能保证同名指标可用。

首页和趋势页共用服务器开关、设备和模式，浏览器刷新或换页不会重启采样。新增 `compute` 同窗模式：FMA/ALU/SFU、Tensor 指令和显存读写共 11 个计数器以 20 Hz 采集、约 250 ms 发布。`diagnostic` 将完整指标分成四个物理组，每组约 500 ms 快速轮换，20 Hz 仅指活动组内频率，不是所有指标同时达到 20 Hz。固定单组仍为 10 Hz、约每秒发布；`all` 每组约 3 秒慢轮换六组。均值使用本轮最近最多 3 秒**已观测区间**，切组重置窗口；曲线跨 epoch 断开。API 标注每组时间、窗口 ID、数据年龄与是否已过期。

页面数值约每秒更新一次，快速缓存和趋势点仍保留约 250 ms 的发布频率；开关、错误状态立即响应。全局数字单次滚动约 1.15 秒，稍长于显示更新周期；新目标继承当前位置和速度，同向继续加减速，反向先用约 340 ms 渐进制动再掉头（计入 1.15 秒）。后台或不可见数字、减少动态效果偏好仍直接落到目标值，避免多余动画。

六个显示组：SM/Tensor 活跃、L1/L2/warp、FMA/ALU/SFU 占用与 warp 指令速率、Tensor warp 指令速率、FMA Lite 实际启用线程指令与有效线程数、显存读写带宽。`Ginst/s` 表示十亿条指令每秒；线程和 warp 口径分别标注。普通浮点管线的执行证据不能用于推断某个 PID、TFLOPS、同时忙碌的 CUDA 核心数或精确 FP32 利用率。有效线程数为同窗口线程指令数 / warp 指令数，范围 0–32；没有指令时不可用，不能把它称为核心占用率。

采样结果经 `TelemetryStore` 一并进入 WebSocket、history、status.json、status.md、metrics。硬件组各自保留 GPU 原始时间区间、接收时间、有效观测时长、样本数和未完成样本数。NVML 另外提供累计能量、能量差分功率、瞬时功率、温度裕量、限制时间比例、原始/未知时钟原因、驱动保留显存及 PCIe 链路状态。缺失字段为 null；能量计数回退时丢弃该次差分。

helper 不创建 CUDA context，不执行 CUDA 工作负载，显式使用一个 8 MiB 硬件缓冲，持续解码并复用 counter image。启动、切换、停止和异常均管理 profiler 生命周期；与原有 Nsight 短采样共享互斥锁。第三方 profiler 争用、缓冲溢出、无样本和解码错误会显示错误并停止，不补零。程序关闭时停止 helper，父进程异常退出也会通过 stdin EOF 通知 helper 停止。详细页面仍保留折叠的 Nsight 扩展采样，读取计算派发、图形 warp、指令发射和显存读写指标。

已验证的开销和 API 限制见 [训练 API 调查](benchmarks/nvidia-training-apis.md)。8 MiB 是指定缓冲大小，不是所有驱动资源的总显存保证。

Tensor 按精度统计运算量、算子形状关联及时间线缓冲控制的进一步验证见 [计算量与实现效率调查](benchmarks/kernel-efficiency-investigation.md)。其中 Range 配置验证不是实时 TFLOPS 读数；新增离线探针不初始化 CUDA，也不进入训练进程。

同步/轮换的实测边界、PCIe/显存带宽口径和 Agent 接入示例见 [高频采样与控制 API](benchmarks/sampling-control.md)。显存 GB/s 是明确标注的估算，PCIe GB/s 来自 NVML 字节吞吐。所有新值进入根路径状态导出；Agent 需要低延迟时使用直接缓存和增量样本端点。
