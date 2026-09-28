# NVIDIA 训练观测 API 探索（2026-09-29）

本机：Windows / RTX 4090 AD102 / 驱动 616.92 / CUDA、CUPTI 12.6。训练进程独立运行；实验没有注入、重启训练或修改功率、频率。本次增加的是可复现探针，尚未替换网页的 Nsight CLI 采集器。

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

建议实现独立常驻 C++ helper，通过项目统一消息流发送 GPU 时间戳、采样区间、metric set、有效样本数及溢出标志。10 Hz 原始数据汇成每秒更新的 2–5 秒滑窗；窗口滚动必须按实际时间、分子/分母语义聚合。使用循环 counter image 并持续 drain；本探针的固定 128 样本和 6 秒生命周期不是完整常驻实现。

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

建议接入顺序：PM helper 与统一 profiler 互斥 → 能量/时钟原因/温度裕量 → 训练阶段与 kernel 时间线 → 热点 PC/SASS/Range 分析。这样先获得连续观测，再用可以定位到算子的证据指导训练优化。
