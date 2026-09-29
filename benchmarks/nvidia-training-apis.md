# NVIDIA 训练观测 API 探索（2026-09-29）

进一步的 Tensor 运算量目录验证、算子效率关联及 Activity 显存控制见 [计算量与实现效率调查](kernel-efficiency-investigation.md)。

本机：Windows / RTX 4090 AD102 / 驱动 616.92 / CUDA、CUPTI 12.6。训练进程独立运行；实验没有注入、重启训练或修改功率、频率。已将可复现探针扩展为连续 CUPTI helper，接入首页、趋势页和统一数据流；原有 Nsight CLI 保留为折叠的扩展短采样。

## 已接入系统的连续观测

`native/cupti_stream.cpp` → `pm_sampling.py` → `TelemetryStore` → WebSocket/history/status.json/status.md/metrics 使用同一套数据。`training_health.py` 同时添加 NVML 能量、瞬时功率、能量差分功率、温度裕量、限制时间比例、原始/未知时钟原因、驱动保留显存与 PCIe 状态。前端首页和趋势页共用服务端开关，多个网页不会重复启动采集器。

- 五组：SM/Tensor、L1/L2/warp、FMA/ALU/SFU、Tensor 指令、FMA Lite 实际启用线程指令。固定组 10 Hz 采样、约每秒发布；按已观测区间计算 3 秒时间加权均值。命中率也是时间均值，并非整段请求数加权命中率。
- 全部轮换每组约 3 秒，实测约 16–18 秒一圈。组间不能直接作同时刻相关性判断；旧值带时间并淡化，曲线断开，过期数值不进入 Prometheus。
- helper 按 PCI bus ID 对应 NVML 与 CUDA 设备，不假设两者编号一致。与 Nsight 共享 profiler 互斥，连续采样中请求旧探针已验证返回 HTTP 409。
- 一个 8 MiB 硬件缓冲，每次解码复用 counter image，最多 128 样本；每组滑窗最多 128 条。停止、错误和父进程 stdin EOF 都释放采样器；十秒没有有效样本即终止。没有 CUDA context、工作负载或 kernel replay。
- 当前驱动在组间仅 Stop/SetConfig/Start 会出现无新数据，改为 Stop/Disable 后重新 Enable。FMA/ALU/SFU 与 Tensor 活跃合组虽然配置报告 1 pass，实际解码报内存不足，改为分组；不能只依赖配置检查断言可用。
- 实际 GPU 时间戳跨解码批次出现边界重叠，早期为 93–343 ns，长期运行还遇到 10106 ns。现在只裁剪不超过样本时长 0.1%、且不超过 100 μs 的重叠并记录 `timestamp_trimmed_ns`；更大的重叠仍报错。未完成样本单独记录，不补齐为零。

### 开销复测

四组轮换的 50 次网页数据读取（49.40 秒）中，helper 的 Windows PID 计数峰值：本地显存 **24 KiB**、非本地/共享约 **8.004 MiB**；系统工作集 **106.05 MiB**，CPU 时间 **0.25 秒**，约为一个逻辑核心的 0.5%。随后加入第五组的连续轮换验证正常。停止开关后确认没有残留 `cupti_stream` 进程。

这里量到的是 helper 的进程记账，不能覆盖所有驱动分配或网页渲染开销；系统工作集不是 VRAM。整卡显存同时随训练波动，不能用整卡前后差值当作采集器的净成本。未进行固定训练步的 A/B 吞吐比较，因此尚不能保证零训练减速或总额外 VRAM 在所有场景严格低于 100 MiB。

### 普通 CUDA 浮点核心确实在工作吗

进一步的同窗口实测：

| 计数器 | 读数 | 含义 |
| --- | --- | --- |
| `sm__thread_inst_executed_pipe_fmalite_pred_on.sum.per_second` | 127.7–234.0 G thread instructions/s | 实际启用线程执行的 FMA Lite 指令 |
| `sm__inst_executed_pipe_fmalite_realtime.sum.per_second` | 4.25–7.88 G warp instructions/s | 同管线的 warp 指令 |
| 两者同窗口之比 | 约 29–30 / 32 | 每条 warp 指令平均启用的线程数 |

NVIDIA 的 [Nsight Compute Profiling Guide](https://docs.nvidia.com/nsight-compute/ProfilingGuide/) 将 FMA Lite 列为普通 FP32 和 FP16 算术管线。因此非零的 FMA Lite 执行及 predicated-on 线程指令，是整卡普通浮点执行单元被使用的直接计数证据。它不能归属某个 PID，不能单独区分 FP32/FP16，也不能把 29/32 解释成 91% 的 CUDA 核心占用率。FMA Heavy 还可能执行整数乘加，不能只凭名称把它全部计为浮点。

另一个独立实验成功得到标量 FP16 FMA warp 指令约 1100–3060 万条/秒；它与上述两项一起配置需要 2 passes，尚未加入默认连续组。精确 FLOPS 还需要区分运算类型、有效线程、向量宽度，以及 Tensor 的指令形状/精度/稀疏模式。`Ginst/s` 不等于 TFLOPS，也不能把所有指令乘 2 得到可靠结果。

### 为什么未到 480 W，显存和 PCIe 也没满

本机 480 W 是 NVML 报告的功率上限，不能由此推导每种高效训练都必须达到 480 W，或 Tensor 单独不可能达到某种功耗。实际功耗还随时钟、电压、指令组合和电路活动变化。[NVIDIA-smi](https://docs.nvidia.com/deploy/nvidia-smi/) 区分功率读数与限制值。

[NVIDIA GPU 性能背景](https://docs.nvidia.com/deeplearning/performance/dl-performance-gpu-background/index.html) 说明，除了算术和访存吞吐，还可能受延迟、可用并行度限制。PCIe/DRAM 未满不排除 L1/L2/共享内存访问、指令依赖、同步、寄存器资源、小 kernel 尾部或 CPU 派发空隙。当前计数只能提供这些方向的线索，不能判定具体训练瓶颈；下一步有效证据是训练阶段/kernel 时间线和热点的 PC/SASS/Range 采样。

本轮原始数据位于被忽略的 `benchmarks/results/`：`pm_web_overhead.json`、`pm_five_groups.json`、`pm_fmalite_threads.txt`、`pm_fmalite_lanes.txt`、`pm_scalar_fp16.txt`、`pm_timestamp_audit.json`。

## 已经验证的突破：CUPTI PM Sampling 可边采边读

此前网页约 21–22 秒更新一次，主要受到 Nsight CLI 启停、导出和轮间休息的限制。直接调用 `cuptiPmSamplingStart` → 周期性 `cuptiPmSamplingDecodeData` → `cuptiProfilerHostEvaluateToGpuValues`，本机成功做到 **10 Hz 硬件采样，每秒解码发布一次**。探针只调用 `cuInit`，不创建自己的 CUDA context，不执行 CUDA 工作负载。

每组连续采 6 秒，每次得到 10 个新样本，全部 `overflow=0`。以下区间为各秒内 10 个样本的算术均值的范围；不同组在不同时间采集，不能拼成同一训练阶段，也不能把均值的命中率当作整段按请求数加权的命中率。

| 指标组 | 实际指标 | 本次读数范围 |
| --- | --- | --- |
| 活跃度 | `sm__cycles_active.avg.pct_of_peak_sustained_elapsed` | 46.6%–76.8% |
| 活跃度 | `sm__pipe_tensor_cycles_active_realtime.avg.pct_of_peak_sustained_elapsed` | 11.3%–19.7% |
| 缓存/warp | `l1tex__t_sector_hit_rate.pct` | 5.7%–7.4% |
| 缓存/warp | `lts__average_t_sector_hit_rate_realtime.pct` | 88.3%–90.5% |
| 缓存/warp | `sm__warps_active_realtime.avg.pct_of_peak_sustained_elapsed` | 22.8%–30.5% |
| Tensor 指令 | `sm__inst_executed_pipe_tensor.sum.per_second` | 14.8–27.5 G warp instructions/s |
| 普通管线指令 | `sm__inst_executed_pipe_fmaheavy_realtime.sum.per_second` | 6.27–14.0 G warp instructions/s |
| 普通管线指令 | `sm__inst_executed_pipe_fmalite_realtime.sum.per_second` | 3.34–7.14 G warp instructions/s |
| 普通管线指令 | `sm__inst_executed_pipe_alu_realtime.sum.per_second` | 19.3–39.5 G warp instructions/s |

`GetMetricProperties` 确认这些指令指标单位为 `instruction/second`，描述为相应管线的 warp 指令数。不是线程指令数、浮点操作数，也不是 BF16 专属计数。FMA 管线名称同样不能保证每条指令都是一次 FP32 FMA。Tensor 指令的形状、数据类型、稀疏模式尚未分离，不能统一乘一个常数输出“实测 TFLOPS”。

warp 指标的分母是 elapsed cycles，包含空闲时间；不能直接当作单个 kernel 在 active cycles 下的 achieved occupancy。L1 命中率低可能来自访问模式、流式访问或缓存策略，结合这里较高的 L2 命中率，尚不能断言训练被显存带宽卡住。应继续关联 Tensor、LSU、DRAM 和具体 kernel。

### 资源成本与采集限制

- 显式设置硬件缓冲 **8 MiB**，100 ms 间隔；counter image 约 18–30 KiB（128 样本容量）。不用官方示例的 512 MiB 默认缓冲。
- 活跃度实验中，按 PID 匹配的 Windows 计数显示工具本地显存 24 KiB、非本地/共享内存约 8 MiB；进程系统工作集峰值约 103.4 MiB。
- 同一实验整卡显存从约 18,920 MiB 增至 21,481 MiB，工具退出后仍保持。存在训练等外部变化，不能用整卡差值归因监控成本，也不能保证驱动的所有额外 VRAM 严格小于 100 MiB。
- 本次没有做固定训练步的 A/B 吞吐实验，不能声称没有训练减速。要常驻仍需相同工作负载的多轮吞吐、资源和丢样检验。
- `GetNumOfPasses` 实测：上述每组都是 1 pass；活跃度 + 缓存/warp + Tensor 指令组合为 2 passes；某些调度组合为 5 passes。PM Streaming 不能通过 replay 补齐，探针主动拒绝多 pass。
- `smsp__warp_issue_stalled_long_scoreboard_per_warp_active.pct` 单独就需要 3 passes；不能因为枚举到名字就承诺实时采集。`smsp__sass_thread_inst_executed_op_ffma_pred_on.sum` 在此 PM 路径报无效参数。
- 一次可用性查询返回 `CUPTI_ERROR_UNKNOWN`，稍后成功，根因未确定。当时页面存在间歇 Nsight 进程；正式实现应统一设备 profiler 互斥、返回 busy/unsupported/failed，不能把失败记为 0。
- 这是整卡数据，包括其他 GPU 程序；不能按进程显存比例分配给训练 PID。

最初的 6 秒探针只验证可行性；上方连续实现已增加生命周期、互斥、持续解码及按时间聚合。保留以下初始实验记录用于复现和区分测量范围。

官方：[PM Sampling API](https://docs.nvidia.com/cupti/api/group__CUPTI__PM__SAMPLING__API.html)。最新 [CUPTI Python 已有 PM/Host 包装](https://docs.nvidia.com/cupti-python/13.4.0/user-guide/topics/overview.html)，但其 [13.4 平台支持仍只列 Linux](https://docs.nvidia.com/cupti-python/13.4.0/release-notes/topics/support.html)；本项目 Windows 路径可直接使用已验证的 C API。

## 更能定位训练瓶颈的接口

| 能力 | 具体 API/记录 | 对训练的价值 | 本次验证程度及边界 |
| --- | --- | --- | --- |
| kernel 时间线与阶段关联 | CUPTI Activity `CONCURRENT_KERNEL`、`MEMCPY`、`RUNTIME`、`EXTERNAL_CORRELATION`；`cuptiActivityPushExternalCorrelationId`，NVTX | 关联 forward/backward/optimizer/data transfer，查 CPU 派发空隙、小 kernel、同步和拷贝重叠 | 文档确认；需要训练进程内接入。整卡 PM 不能替代关联；CPU NVTX 区间本身也不等于异步 GPU 完成时间 |
| warp 在等待什么 | `cuptiPCSamplingEnable`、`SetConfigurationAttribute`、`GetData` | 区分访存依赖、屏障、共享内存/MIO、数学管线拥塞；定位等待指令 | 本机 PC API 支持查询通过；未给训练注入。continuous 模式不串行化 kernel，但没有逐次 launch 的时间归属，不能承诺每一步 stall 分解 |
| 访存是否浪费 | CUPTI SASS Metrics `GetNumOfMetrics/GetMetrics`，按选中 kernel 开启 | 实际/理想 global sectors、shared wavefronts、local loads、分支分歧、predicated-on 线程指令 | AD102 实际枚举到 48 项；未开启 patching。可诊断不合并访问、bank conflict 或 spill 线索，需要地址/源码证据确认 |
| 寄存器/共享内存限制 | kernel Activity 中的资源信息；`cudaFuncGetAttributes`、`cudaOccupancyMaxActiveBlocksPerMultiprocessor` | 为自己写的 CUDA/Triton kernel 判断 launch 配置和 occupancy 上限 | 文档确认；函数句柄和上下文属于训练进程；理论 occupancy 不等于实测吞吐，不能盲目追求 100% |
| 分配与缓存池 | Activity `MEMORY2` / `MEMORY_POOL`；`cudaMemPoolGetAttribute` 的 used/reserved 高水位 | 定位 malloc/free、池扩张、临时 workspace；区分峰值显存与留存池 | 需要训练内接入。PyTorch native allocator 不一定使用 CUDA async pool；这些接口不会自动得到张量名或 autograd 引用关系 |
| 算法与矩阵形状 | cuBLASLt logger、matmul descriptor、`cublasLtMatmulAlgoGetHeuristic`、算法配置和 workspace | 对应慢 GEMM 的 M/N/K、dtype、layout、算法选择；检查 BF16 路径和融合机会 | 文档确认，尚未在训练中采集。框架常已管理算法；应先查热点再考虑调参，避免每步重新查询 heuristic |

PC Sampling 的 continuous 模式能避免 kernel 串行化；`GetData` 不做 device synchronization。默认硬件缓冲是 **512 MiB**，必须改小、降低频率并观测丢样，不能沿用默认。建议下一阶段只对训练热点短时试验 16–32 MiB 缓冲，但这是待测方案，并非已满足 100 MiB 的承诺。stall 在等待者指令上采到，不一定是产生慢数据的那条指令；`not_selected` 可能表示有足够可执行 warp，不能当作无效计算。

来源：[PC Sampling API](https://docs.nvidia.com/cupti/api/group__CUPTI__PCSAMPLING__API.html)、[CUPTI 教程中的连续/串行化语义](https://docs.nvidia.com/cupti/tutorial/tutorial.html)、[SASS API](https://docs.nvidia.com/cupti/api/group__CUPTI__SASS__METRICS__API.html)、[Nsight Compute 指标解释](https://docs.nvidia.com/nsight-compute/ProfilingGuide/)、[CUDA occupancy 指南](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/)、[cuBLASLt](https://docs.nvidia.com/cuda/cublas/index.html)。

需要精确浮点数时，应选定热点 kernel，使用 Nsight Compute/Range Profiling 的相应精度计数及反汇编核对操作数，再按执行时间换算。多 pass replay 和 SASS patching 都可能改变性能和内存，不适合作为默认常驻方案。只知道 BF16 输入 dtype 不足以证明整个训练全程在 Tensor 上执行。

## NVML 还遗漏了哪些便宜且有价值的数据

只读脚本 `nvml_training_probe.py` 在本机实际成功读取：

- 累计能量 `nvmlDeviceGetTotalEnergyConsumption` / `NVML_FI_DEV_ENERGY`。可结合完成 token/step 计算整卡 J/token、J/step，比较不同 batch、融合策略或功率设置的效率。本次约 4 秒能量差对应 209.6 W。需处理驱动重载造成计数回退，不能无依据拆为 Tensor/CUDA 各自功耗。
- `NVML_FI_DEV_POWER_AVERAGE` 与 `POWER_INSTANT`。4090 的普通 `GetPowerUsage` 是约 1 秒平均，瞬时字段是另一口径；采样更频繁不代表平均功率的底层分辨率随之提高。
- 功率/温度限频累计计数、温度裕量、当前时钟原因。采样中三个 power/thermal 累计字段均为 0；温度裕量 33–38°C；不能由此排除所有性能限制。原始原因 `0x400` 未能从当前公开定义与已装绑定找到可靠解释，应显示未知位，不能误标。
- PCIe Gen4 ×16、replay 计数 0。字节累计字段实际是 uint32，并在 1 秒间隔中出现回绕；不得直接相减出现负速率，也不能假设一秒最多回绕一次。日常吞吐继续优先用已有 throughput API，细分拷贝归因用 CUPTI MEMCPY。
- `nvmlMemory_v2.reserved` 实测 446,693,376 bytes（426 MiB），可单独展示驱动保留部分。它不是某个训练进程的 allocator cache。

5 次批量字段查询的 Python CPU 时间约 0.016 秒（系统计时粒度较粗），无需 CUDA context。来源：[NVML Device Queries](https://docs.nvidia.com/deploy/nvml-api/api/group__nvmlDeviceQueries.html)、[NVML Field Values](https://docs.nvidia.com/deploy/nvml-api/api/group__nvmlFieldValueEnums.html)。

排除项：本机 `nvmlGpmQueryDeviceSupport` 明确返回 false；不能照搬数据中心 GPM/DCGM 面板。较新的 [PRM Counter API](https://docs.nvidia.com/deploy/nvml-api/api/group__nvmlGPUPRMAccess.html) 要求 Blackwell+Linux，不是 Windows 4090 的 SM 计数捷径。CUPTI 分配来源库追踪的 `cuptiActivityEnableAllocationSource` 当前限 Linux x86_64，不能承诺在本机把显存准确拆成每个库；见 [CUPTI Usage](https://docs.nvidia.com/cupti/main/main.html)。

## 复现

在项目根目录 PowerShell，安装现有 CUDA 12.6 和 MSVC x64 后：

```powershell
.\benchmarks\build_pm_probe.ps1
$env:PATH = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\extras\CUPTI\lib64;' + $env:PATH
.\benchmarks\results\cupti_pm_probe.exe                  # 只枚举 PM
.\benchmarks\results\cupti_pm_probe.exe --sass           # 只枚举 SASS，不 patch
.\benchmarks\results\cupti_pm_probe.exe --check sm__inst_executed_pipe_tensor.sum.per_second
.\benchmarks\results\cupti_pm_probe.exe --describe sm__inst_executed_pipe_tensor.sum.per_second
.\benchmarks\results\cupti_pm_probe.exe --sample         # 默认 SM + Tensor，6 秒
.\benchmarks\results\cupti_pm_probe.exe --sample l1tex__t_sector_hit_rate.pct lts__average_t_sector_hit_rate_realtime.pct sm__warps_active_realtime.avg.pct_of_peak_sustained_elapsed
.\.venv\Scripts\python.exe benchmarks/nvml_training_probe.py
```

采样前关闭其他硬件 profiler 采集，避免争用；不要终止训练。`--check` 只验证配置，不启用采样。单轮大于 1、未知指标、非有限结果或溢出均报错退出。所有探针结果都是 GPU 0。

本机原始记录保存在被忽略的 `benchmarks/results/`：`pm_live.txt`、`pm_overhead.json`、`pm_cache.txt`、`pm_tensor_instructions.txt`、`pm_instructions.txt`、`pm_descriptions.txt`、`sass_metrics.txt`、`nvml_training.json`。它们随本机保留，不作为跨机器基准。

PM helper、统一 profiler 互斥及 NVML 训练状态已接入。尚待深入的是训练阶段与 kernel 时间线、热点 PC/SASS/Range 分析；这些能力需要进程内关联或短时分析，不能冒充现有的无侵入整卡读数。
