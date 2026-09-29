# 从指令速率到训练实现效率：本机验证（2026-09-29）

环境：Windows、RTX 4090 / AD102、CUDA/CUPTI 12.6（CUPTI API version 24）、Nsight Compute 2022.3.0。此次新增实验只查询本机指标目录、编译配置以及探针自身的缓冲属性；没有启动 GPU 工作负载，没有进入训练进程，没有启用 kernel tracing 或 replay。网页已有的整卡 PM 采样与这些离线查询相互独立。

## 1. 发现了直接的 Tensor 运算量指标，但采集模式不同

同一套 CUPTI DLL，使用 `cuptiProfilerHostInitialize` 的不同 `profilerType`，实际枚举得到 PM 目录 1505 项、Range 目录 2868 项。目录规模不等于硬件上全部可采集；这里未传入设备 counter-availability image。

以 `AD102` 为芯片名，逐项查询描述并构建配置，结果如下：

| 计数器 | PM Sampling | Range 配置 | 含义 |
| --- | --- | --- | --- |
| `sm__ops_path_tensor_src_bf16_dst_fp32.sum` | INVALID_METRIC_NAME | 1 pass | BF16 输入、FP32 结果的 Tensor 运算量 |
| 上述名称的 `_sparsity_off.sum` / `_sparsity_on.sum` | INVALID_METRIC_NAME | 各自 1 pass | 分别统计关闭/开启稀疏的路径 |
| `sm__ops_path_tensor_src_fp16_dst_fp32.sum` | INVALID_METRIC_NAME | 1 pass | FP16 → FP32 Tensor 运算量 |
| `sm__ops_path_tensor_src_tf32_dst_fp32.sum` | INVALID_METRIC_NAME | 1 pass | TF32 → FP32 Tensor 运算量 |
| `smsp__sass_thread_inst_executed_op_ffma_pred_on.sum` | INVALID_METRIC_NAME | 1 pass | 实际启用线程的 FFMA 指令数 |
| `sm__inst_executed_pipe_tensor_op_hmma.sum` | INVALID_METRIC_NAME | 1 pass | HMMA/QMMA warp 指令数，仍不是 FLOPs |
| `sm__thread_inst_executed_pipe_fma_type_fp16_pred_on.sum.per_second` | 1 pass | 1 pass | 普通 FMA 半精度管线的线程指令速率 |

另验证 Range 的 FADD、FMUL、`gpu__time_duration.sum`、DRAM 读/写字节各自可构建 1-pass 配置。BF16 Tensor 的 `.sum.per_second` 也能构建。**这些都是离线配置结果，不是训练读数；各自 1 pass 不代表放在同一组仍为 1 pass，更不代表没有运行开销。** `sass` 指标涉及软件插桩，不能视为普通 PM 计数。

本机 Nsight Compute 2022.3 的 `--query-metrics --chips ad102` 没有列出 `sm__ops_path_tensor_*`，但 CUPTI 12.6 Range 目录确实包含这些指标。旧工具没有列出，不等于 GPU 没有相关分析路径。

NVIDIA 工程师也建议通过 `sm__ops_path_tensor_*` 计算不同类型的 Tensor 运算量，而不要为所有 MMA 指令套统一系数；见 [Tensor FLOPs 讨论](https://forums.developer.nvidia.com/t/tensor-core-flops/353191)、[HMMA 变体与运算量](https://forums.developer.nvidia.com/t/number-of-floating-point-operations-in-one-hmma-instruction/275450/2)。这些讨论的架构/版本不是本机验证的替代品。

### 计算时必须保留的口径

- BF16、FP16、TF32 及 sparse/dense 分开。某类型总计与它的 sparse/dense 子项不能再次相加。
- 精确 FP32 算术计数可从对应的 predicated-on FADD + FMUL + 2 × FFMA 构成；只涵盖这些运算，不涵盖 Tensor、整数、特殊函数等。
- 普通 FP16 还涉及 packed 指令、lane 选择和工具版本，不从泛化的 `fma_type_fp16` 指令数直接推算 FLOPs。Nsight Compute 2025.1 曾修复非 Tensor FP16 Roofline 的乘数，旧脚本系数不能无条件沿用：[发布说明](https://docs.nvidia.com/nsight-compute/ReleaseNotes/index.html)。
- 对 Tensor 稀疏计数，需要用已知形状的 GEMM 校准其实际操作数/稠密等效口径，再决定如何与峰值比较；本轮未做校准。
- `gpu__time_duration.sum` 的描述表明会在用户测量时间和墙钟测量时间之间选择可用来源。最终报告必须保留时间来源，不能混用算子 GPU 时间、CPU API 时间和训练 step 墙钟时间。

## 2. 如何真正评价代码实现

| 名称 | 分子与分母 | 解释范围 |
| --- | --- | --- |
| 实际算术吞吐 | 对应类型的执行运算量 / 对应 GPU 区间时间 | 单元完成运算的速度，不保证这些运算都有用 |
| 算子有效吞吐 | 已知问题的算法运算量 / 算子 GPU 时间 | 比较同形状、精度、准确度要求下的实现 |
| 训练吞吐 | 有效 token/样本/step / 墙钟时间 | 包括数据供给、CPU 派发、同步等完整路径 |
| 算术强度 | 同区间运算量 / 同层级搬运字节数 | 用 Roofline 分析算术与带宽约束；L1/L2/DRAM 分母分别标明 |
| 整卡能量效率 | NVML 能量增量 / 完成的 token 或 step | 含同卡其他工作，不能自动归到一个 kernel |

普通稠密 GEMM 主乘加部分可使用 `2 × batch × M × N × K` 的算法工作量，融合 epilogue、激活、归约等需另算。它是指定算法的参考计算量，不能将参考量与实际量的比值当成所有算法通用的“效率评分”。例如重计算可能用更多算术换取更小显存和更大 batch，最终 step 吞吐反而提高。

并发 kernel 的持续时间不能简单相加当作总 GPU 忙碌时间：设备时间覆盖率应按区间并集统计。Kernel 时间间隙也不自动等于 CPU 瓶颈；还需看依赖、复制、同步以及采集是否丢失。来源：[Nsight Compute 性能分析](https://docs.nvidia.com/nsight-compute/ProfilingGuide/)、[矩阵乘法背景](https://docs.nvidia.com/deeplearning/performance/dl-performance-matrix-multiplication/index.html)。

## 3. 关联矩阵形状和代码的可用接口

### cuBLASLt：参数与性能提示

本机 `cublasLt.h` 存在 `cublasLtMatrixLayoutGetAttribute`、`cublasLtMatmulDescGetAttribute`、`cublasLtLoggerSetCallback`。在训练进程内可读取行列、batch、数据类型、转置、计算类型等描述信息，并通过 CUPTI correlation ID / NVTX 关联实际 GPU kernel。

`CUBLASLT_LOG_LEVEL=2` 可记录启动 kernel 的 API 参数，级别 3 包括潜在性能改进提示；自定义 callback 可以把日志写入有界队列。启动环境变量只影响随后启动的目标进程；它不会自动改变当前正在运行的训练。cuBLASLt 也不覆盖 Triton、FlashAttention 或全部 cuDNN kernel。日志格式不宜当成稳定的机器协议，CPU 调用耗时不等于 GPU 执行耗时。[cuBLASLt Logging](https://docs.nvidia.com/cuda/cublas/#cublaslt-logging)

### PyTorch：短窗口形状与算子关联

`torch.profiler` 的 `with_flops=True` 是按公式估计特定算子（矩阵乘、2D 卷积）的计算量，不是读取整卡硬件 FLOPs；融合/自定义 kernel 的覆盖必须单独确认。`record_shapes=True` 会暂时持有张量引用，可能影响优化并产生额外复制。适合有明确时间范围的诊断，不适合直接承诺常驻零开销。[PyTorch Profiler](https://docs.pytorch.org/docs/stable/profiler)

### CUPTI：时间线与 Range

Activity 的 concurrent-kernel 记录可提供 kernel 名称、时间、stream、launch 配置及资源信息，但未必包含原始 GEMM 的 M/N/K；不能从 grid/block 尺寸可靠反推问题形状。自动 Range 和精确 SASS 分析可能改变并发行为；1-pass 配置仍可能有同步、插桩和数据解码开销。[CUPTI Usage](https://docs.nvidia.com/cupti/main/main.html)

公开文档中的 Dynamic Attach 指 CUPTI 在目标进程内初始化/终止的机制，不是当前外部监控器传一个 PID 就能无侵入拿到所有 kernel。新版 Nsight Compute 提供了动态加载 injection 的接口，但仍需要目标进程参与；本机 2022.3 不具备该新增接口。[Nsight Compute 发布说明](https://docs.nvidia.com/nsight-compute/ReleaseNotes/index.html)

## 4. 降低时间线 VRAM 的新控制点：已经验证设置，尚未测真实 tracing

使用 `cuptiActivityGetAttribute/SetAttribute`，只在独立探针进程中读写，不调用 `cuInit`，不注册 tracing 回调、不启用任何 Activity。实测结果：

| 属性 | 本机读到的默认值 | 设置并成功读回 |
| --- | ---: | ---: |
| `DEVICE_BUFFER_SIZE` | 3,200,000 bytes | 4 MiB |
| `DEVICE_BUFFER_POOL_LIMIT` | 250 / context | 4 / context |
| `DEVICE_BUFFER_PRE_ALLOCATE_VALUE` | 3 / context | 1 / context |
| `MEM_ALLOCATION_TYPE_HOST_PINNED` | 0（设备内存） | 1（锁页主机内存） |

默认的 250 是增长上限，不是启动就分配 250 个。本机 SDK 不同属性的注释对默认位置存在新旧描述差异，因此采用实际 API 读回的 0，不能依据旧注释声称默认位于主机内存。

4 × 4 MiB 只限制该类缓冲的每上下文名义容量；其他驱动分配、trace 输出缓冲、多个 context、代码插桩都另计。锁页主机内存也消耗系统资源并可能增加传输成本。后续若开启 tracing，必须同时验证 dropped records、训练耗时及 GPU 本地/非本地内存，不能把“设置被接受”写成“VRAM 已实测低于 100 MiB”。现有 PM helper 的 8 MiB 缓冲不由这些 Activity 属性控制。

本机 CUDA 12.6 头文件还明确指出，旧的 profiling-semaphore-pool 属性从 CUDA 12.3 起不再支持，不能继续靠它们限制缓冲。

## 5. 本轮修复与复现

长期 PM 采样实际遇到 `10106 ns` 的跨批次边界重叠，触发原先固定 10 μs 阈值。现在仅裁剪不超过该样本时长 0.1%、且不超过 100 μs 的重叠，记录 `timestamp_trimmed_ns`；更大重叠继续拒绝。新增了该实际数值、绝对上限和短区间相对误差的回归检查。

离线目录查询不需要停止网页现有 PM 采集：

```powershell
.\benchmarks\build_pm_probe.ps1
$env:PATH = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\extras\CUPTI\lib64;' + $env:PATH
.\benchmarks\results\cupti_pm_probe.exe --catalog pm AD102
.\benchmarks\results\cupti_pm_probe.exe --catalog range AD102
.\benchmarks\results\cupti_pm_probe.exe --catalog range AD102 sm__ops_path_tensor_src_bf16_dst_fp32.sum
.\benchmarks\results\cupti_pm_probe.exe --catalog pm AD102 sm__ops_path_tensor_src_bf16_dst_fp32.sum
.\.venv\Scripts\python.exe benchmarks/cupti_activity_config_probe.py
```

芯片名在此 API 中使用 `AD102`；小写 `ad102` 本机初始化报错。`--catalog` 在 CUDA 初始化之前返回；不调用 Range/PM 的 target start API。输出中的 `offline_passes` 只代表本机版本的离线调度结果。全部探针输出保存在忽略目录 `benchmarks/results/` 的 `catalog_*`、`activity_buffer_controls.json` 与 `pm_clock_tolerance_validation.json`。

## 下一阶段的最小实验

先让训练进程在一个可重复的短窗口输出 step/NVTX 与 kernel 时间线，并收集代表性 GEMM 形状；再针对占时最高的一个形状测 BF16 Tensor 运算量和持续时间，用已知工作量校准。PM、Range 与其他 profiler 按互斥窗口运行。以相同训练设置比较无采集/有采集的 step 时间分布、额外 VRAM 和丢样，达到约定预算后再考虑接入默认网页流程。当前没有为未测出的 TFLOPS 或代码效率评分填入估计数字。
