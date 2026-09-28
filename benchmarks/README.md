# 监控工具开销实测（2026-09-27）

测试机器：Windows、RTX 4090（24 GB）。测试期间另有持续运行的 Python GPU 工作负载；没有暂停用户进程。采样脚本 [`overhead_probe.py`](overhead_probe.py) 每 0.5 秒读取 NVML，并从现有 `/api/status` 读取 Windows 进程本地显存计数。原始 JSON 保留在本机 `results/`，不纳入 Git。测试生成的多 GB ETL 已清理。

| 场景 | 工具进程本地显存峰值 | 其他实测开销 |
| --- | ---: | --- |
| 本项目后端，PDH 替换后 | 24 KiB | 稳定运行 20 秒消耗 0.36 秒 CPU；约 113 MiB 进程工作集；进程显存样本年龄 0–1.2 秒 |
| WPR，`GPU` + `DesktopComposition`，文件模式 | 未检出常驻工具 GPU 进程 | 约 12 秒观察窗口内全系统写入 4.51 GiB；最终 ETL 8.23 GB，停止后合并约两分钟 |
| GPUView，`log.cmd m` 最小模式 | 未检出常驻工具 GPU 进程；短暂的 `xperf` 进程可能遗漏 | 8 秒观察窗口内全系统写入 966 MiB；最终 ETL 约 3.22 GiB |
| Nsight Systems 2024.4.2，WDDM extensive，无回溯 | 0.02 MiB | 采集/处理进程工作集升至 6.44 GB；为避免继续增加负载，提前结束，未生成完整报告 |
| GPUView 查看器，空载、未打开轨迹 | 0 | 约 69 MiB 进程工作集 |
| Nsight Systems 查看器，空载、未打开轨迹 | 7.2 MiB | 约 130 MiB 进程工作集 |
| Nsight Systems 2024.4.2，仅 `ad10x` GPU Metrics，10 Hz、5 秒 | 0.047 MiB | 约 49–50 组指标；报告约 0.4 MB。采集及处理进程工作集合计最高约 506 MiB |
| Nsight Systems 2024.4.2，AD10x 独立管线集合，10 Hz、5 秒 | 0.05 MiB | 49 组指标；14 秒监测窗口整卡 NVML 显存 21747.52 MiB 无变化。工具进程工作集合计最高约 551 MiB；不能据此推断训练吞吐无影响 |

旧版后端在 10 秒内消耗约 7.3 秒 CPU。单独运行采集器、只保留 NVML 时，10 秒消耗 0.06 秒 CPU；旧 Windows 进程计数是主要开销。改为复用 PDH 通配符数组查询后，首 12 秒（含约 4 秒首次初始化）消耗 1.62 秒 CPU；稳定运行 20 秒消耗 0.36 秒 CPU。DWM 的本地用量、专用计数及提交量与旧查询和页面读数核对一致，单元测试覆盖查询复用与新增进程。

**100 MB VRAM 门槛的结论：**能直接测到的工具进程本地显存均低于 100 MB，本项目后端约 24 KiB。但整卡 VRAM 会随既有 GPU 工作波动：有一段未启动采集工具的 10 秒基线，最小到最大相差约 140 MiB。因此这些短时测试不能证明 WPR、GPUView 或 Nsight 对整卡的*全部*附加显存（包括驱动及跨进程资源）严格小于 100 MB。进程本地计数也可能包含共享资源。

新增的 10 Hz GPU Metrics 单独短测中，第一次整卡 NVML 显存上升约 300 MiB 并在工具退出后保持，不能归因于 Nsight；重复测试的采集前、期间和结束后三段读数均为 3117.2 MiB，匹配 NVIDIA 设备的进程本地显存也未增加。这支持将其作为按需短时探针，但仍不能保证任意工作负载下整卡额外 VRAM 严格低于 100 MiB。常驻监控保持原有 NVML/PDH 路径。

2026-09-28 的细分计数试验：本机 Nsight Compute `--query-metrics --chip ad102` 列出 FMA、ALU、LSU、Tensor HMMA/IMMA 等指标。尝试在 Nsight Systems 2024.4.2 的 10 Hz GPU Metrics 中加入常规 FMA/ALU，默认 `ad10x` 集合和几种缩小集合都没有 `GPU_METRICS` 表，诊断为 `Some metrics require multiple passes which is not supported`。随后从本机 `ad10x-gfxt.config` 取实时 FMA Light/Heavy、ALU、SFU 计数器，另建最小 AD10x 配置，在 10 Hz 下与 Tensor 指标一同成功单次采样。2 秒训练窗口内共 19 组样本：一次报告的 ALU 平均 5.1%、FMA Heavy 1.7%、FMA Light 1.0%、Tensor 17.0%；加入 SFU 后另一次报告的 ALU 3.8%、FMA Heavy 1.3%、FMA Light 1.0%、SFU 1.0%、Tensor 11.6%。训练负载随时间变化，不可直接比较两次窗口。采样报告的指令条数有时出现负值（32 位有符号溢出），不能展示为可信绝对值，项目配置故只输出吞吐率。不同管线的百分比基准不同，不能相加，也不能精确等同于 FP32/整数 CUDA Core 占用。

默认 `ad10x` 报告则已经含有 GR/同步与异步计算派发、SM 活跃和发射、warp 槽位、Tensor、显存读写等 49–50 组样本；页面新增字段只解析已有报告，不增加采样指标。训练中的一次有效采样得到 GR 81.3%、同步计算派发 72.1%、计算 warp 29.5%、SM 活跃 68.2%、Tensor 16.9%、显存读/写 21.2%/17.9%。训练显存本身大幅波动，仍不能由采样前后整卡显存差值严格归因工具开销。

WPR 文件模式、GPUView 最小模式和本机 Nsight 2024 的 extensive 模式均不适合作为常驻低负载监控。需要深度分析时短时按需采集，采集后离线查看。Nsight 2026.1.1 起提供的聚焦 `WDDM Memory Trace` 此机尚未安装，未做负载实测，不能据其说明推断已满足门槛。
