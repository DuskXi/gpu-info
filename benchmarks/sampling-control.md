# 高频硬件采样、带宽与 Agent 控制

本机验证：RTX 4090 / AD102、驱动 616.92、CUDA/CUPTI 12.6。组合依赖架构和驱动；配置成功不保证运行解码成功。

## 同步的范围

原五组所有指标放在同一配置需要 4 个 pass；PM Sampling 要求单 pass，不能通过多个 helper 争用同一套计数器来同时获得完整指标。

实测可用组合是 `execution`：FMA Heavy、FMA Light、ALU、SFU/XU 的百分比和 warp 指令速率，Tensor warp 指令速率，DRAM 读取/写入百分比，共 11 个计数器。六秒独立组合试采及共享服务连续采样均成功。所有投影显示组保留相同 `cohort`、`window_id`、GPU 原始窗口，因此能做同窗相关分析。

SM+线程计数、SM+线程计数+L2、SM+部分普通管线的组合虽然配置显示 1 pass，实机 DecodeData 返回 OOM；未作为同步模式发布。没有扩大缓冲来试图绕过错误。

| 模式 | 活动组内原始采样 | 发布 | 覆盖 |
| --- | --- | --- | --- |
| compute | 20 Hz / 50 ms | 约 250 ms | execution 持续同窗 |
| diagnostic | 20 Hz / 50 ms | 约 250 ms | activity、cache、execution、scalar 四组，各约 500 ms 加切换开销 |
| 固定单组 | 10 Hz / 100 ms | 约 1 s | 所选组持续观察 |
| all | 10 Hz / 100 ms | 约 1 s | 六个显示组逐组轮换，每组约 3 s |

滑动均值为当前 epoch 最近最多 3 秒的时间加权均值。快速轮换每次回来只观察约半秒，不能当作过去 3 秒的完整平均。切换间隔保留带时间的旧值，前端曲线断开。`coverage_ratio` 只描述该组当前窗口内部有效区间占比，不包括离开组期间的盲区。

## 带宽口径

- DRAM 百分比：CUPTI `dram__read_throughput.avg.pct_of_peak_sustained_elapsed` 与 write 对应项，整卡窗口内带宽百分比。
- 本机 PM 接口不接受 `dram__bytes_read.sum.per_second` 或 `dram__read_throughput.avg.per_second`，所以没有把直接字节测量伪装成已实现。
- DRAM GB/s 为 **估算**：实测百分比 × NVML 显存时钟 MHz × 1e6 × 2 × 总线位宽 / 8。参考时钟在发布时读取，不是窗口内积分；时钟变化和 sustained peak 定义都会产生误差。JSON 单位 `estimated_bytes_per_second`、`estimated=true`，Prometheus 名称也包含 estimated。无法读取参考值时为 null。此换算基于 DDR 理论值，不能当作所有架构上的精确有效字节带宽。
- PCIe TX/RX：NVML 字节吞吐，按 KB/s × 1024 转成 B/s；前端 GB/s 为十进制 1e9 B/s。TX 为 GPU 发出，RX 为 GPU 接收。
- PCIe 百分比分母为**当前协商**链路单方向线速扣编码损耗后的容量，尚未扣 PCIe 数据包开销。Gen1/2 用 8/10，Gen3/4/5 用 128/130；未知编码（包括 Gen6）返回 null，不套用旧公式。Gen4 ×16 是约 31.51 GB/s 每方向。
- NVML 官方定义吞吐为约 20 ms 驱动窗口。10 Hz 轮询会留下间隔，TX/RX 查询也非同一个原子窗口；不能声称全时段覆盖或与 CUPTI 精确同窗。保留 `sampled_at` 和 `query_duration_seconds`。
- PCIe 常规采集跟随基础监控约 1 Hz；独立快速开关提升到 10 Hz，可与 CUPTI 并行。不创建 CUDA context。

## 控制与读取

`GET /api/sampling-control` 或无参数 `POST` 返回 JSON 请求文档，不改变状态。参数通过 URL query 提交；只有 POST 可以改变状态，非法参数返回 422。

```powershell
# 请求文档
Invoke-RestMethod http://127.0.0.1:8001/api/sampling-control
# 开启全部持续开关（基础监控、同窗硬件、PCIe 快速采样）
Invoke-RestMethod -Method Post 'http://127.0.0.1:8001/api/sampling-control?action=start&target=all&gpu=0&mode=compute&replace=true'
# 查询真实状态，start 成功仅表示启动已受理，需要等 ready
Invoke-RestMethod 'http://127.0.0.1:8001/api/sampling-control?action=state'
# 完整快速轮换
Invoke-RestMethod -Method Post 'http://127.0.0.1:8001/api/sampling-control?action=start&target=hardware&gpu=0&mode=diagnostic&replace=true'
# 停止全部持续开关
Invoke-RestMethod -Method Post 'http://127.0.0.1:8001/api/sampling-control?action=stop&target=all'
```

支持分别控制 `hardware`、`bandwidth`、`telemetry`；Nsight 经同一端点的 `action=capture&target=nsight&kind=general&duration=5` 触发有限时长采样，与 CUPTI 互斥。Nsight 没有持续开关，`stop all` 不强杀已有的有限时长分析，它会自行结束。切换到不同 CUPTI 模式需要 `replace=true`；同一 GPU/模式的 start 是幂等操作。所有配置只保留在当前服务进程中。

低延迟读取：

- `GET /api/hardware-sampling`：最新硬件窗口和 PCIe 快速状态，来自共享缓存，不触发新探针。
- `GET /api/hardware-sampling/samples?after=0&limit=100`：最多保留 240 个发布包，每包包含若干 50/100 ms 原始样本。响应 `cohorts[packet.cohort]` 对应 values 数组顺序。
- `GET /api/bandwidth-sampling/samples?after=0&limit=100`：最多保留 600 条 NVML 原始采样，独立游标/会话。
- 用各自返回的 `next_sequence` 作为下一次 `after`，同时附带对应 `session_id`。`reset=true` 清除旧会话相关分析；`truncated=true` 说明有数据被覆盖，不能假定区间完整。
- CUPTI `start_ns/end_ns` 是 GPU 原始时间，不能直接当 Unix 纳秒。`received_at` 是接收时间；原始区间可能有小幅时钟转换抖动，滚动统计只对 ≤0.1% 且 ≤100 µs 的重叠做裁剪，并报告 `timestamp_trimmed_ns`，更大重叠停止采样。
- API `ready` 表示预期各组都存在且在新鲜度预算内，不等于各组同时采集。还需检查 `enabled`、`state`、`error`、`max_age_seconds`、`missing_groups` 和每组 `cohort/window_id`。
- `/status.json`、`/status.md`、`/metrics` 包含新指标，但整机快照仍约 1 Hz。高频 Agent 用直接缓存或原始样本。前端数值约 1 Hz、趋势保留高频点；这个显示节奏不改变采样或 API 更新频率。

Agent 可据此关联自己已记录的 step time、tokens/s、batch size、显存压力；整卡计数器不能按进程显存份额分配给 PID，也不能把 Ginst/s 换名为 TFLOPS。

## 本机开销与延迟

`benchmarks/validate_sampling_modes.py` 通过共享 API 对两种模式各观察 20 秒，未修改、重启或注入训练。原始结果保存在本机忽略目录 `benchmarks/results/sampling_modes_validation.json`。

- 同步模式发布间隔中位数约 263 ms，轮换模式约 265 ms。
- 同步模式观察到的最大接收年龄约 0.263 s；快速轮换模式约 1.91 s，单组两次发布的最长间隔约 1.95 s。这是本次运行观测值，不是实时保证。
- helper 本地 GPU 内存 24,576 B（24 KiB），共享 GPU 内存 8,392,704 B（约 8 MiB）。RSS 约 104–106 MiB 是系统内存，不是 VRAM。
- helper CPU 约单核 2.3%（同步）、3.2%（轮换）。不代表整机或浏览器总开销；未做训练吞吐 A/B，不能宣称零影响。

## 依据

- [CUPTI PM Sampling 约束](https://docs.nvidia.com/cupti/main/main.html)：需要单 pass，硬件计数器资源有限。新版 13.2 新增单 pass set 查询 API；本项目使用 12.6，不假装已调用新版 API。
- [NVML Device Queries](https://docs.nvidia.com/deploy/nvml-api/api/group__nvmlDeviceQueries.html)：PCIe 吞吐单位、20 ms 窗口、链路代数/宽度及显存时钟/位宽查询。
