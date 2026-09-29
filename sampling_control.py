"""Machine-readable contract for the unified sampling control endpoint."""
from pm_sampling import MODES


def request_document():
    return {
        "endpoint": "/api/sampling-control", "version": 1, "encoding": "URL query parameters",
        "description": "不带参数的 GET/POST 返回本文档，不改变采样状态。控制使用 POST。",
        "parameters": {
            "action": {"enum": ["state", "start", "stop", "capture"], "required_for_control": True},
            "target": {"enum": ["hardware", "bandwidth", "telemetry", "nsight", "all"], "default": "hardware"},
            "gpu": {"type": "integer", "minimum": 0, "default": 0},
            "mode": {"enum": list(MODES), "default": "compute"},
            "replace": {"type": "boolean", "default": False, "description": "替换不同模式的 CUPTI 会话；同模式启动幂等"},
            "kind": {"enum": ["general", "pipelines"], "default": "general", "applies_to": "nsight capture"},
            "duration": {"type": "integer", "minimum": 1, "maximum": 5, "default": 5, "unit": "seconds"},
        },
        "targets": {
            "hardware": {"actions": ["start", "stop"], "description": "CUPTI PM 全局共享会话，与 Nsight 互斥"},
            "bandwidth": {"actions": ["start", "stop"], "description": "NVML PCIe 快速采样 10 Hz，可与 CUPTI 并行"},
            "telemetry": {"actions": ["start", "stop"], "description": "基础整机/进程/显示采集 1 Hz；关闭后旧快照逐渐过期"},
            "nsight": {"actions": ["capture"], "description": "1–5 秒有界采集，自动结束，无持续开关；不与 CUPTI 并发"},
            "all": {"actions": ["start", "stop"], "description": "控制所有持续开关：telemetry、hardware、bandwidth；不启动 Nsight，已在进行的有界 Nsight 采集会自行结束"},
        },
        "modes": {
            "compute": "20 Hz：FMA/ALU/SFU/Tensor 指令与显存读写共 11 个计数器，同一窗口；约 250 ms 发布；最近最多 3 秒均值",
            "diagnostic": "20 Hz 仅适用于当前活动组；四个物理组快速轮换，每组约 500 ms；全组非同步",
            "all": "10 Hz 六组慢轮换，每组约 3 秒",
            "fixed_groups": "activity/cache/pipelines/tensor/scalar/memory：10 Hz 固定组连续采样",
        },
        "read_endpoints": {
            "state": "/api/sampling-control?action=state",
            "hardware": "/api/hardware-sampling",
            "hardware_samples": "/api/hardware-sampling/samples?after=0&limit=100",
            "bandwidth": "/api/bandwidth-sampling",
            "bandwidth_samples": "/api/bandwidth-sampling/samples?after=0&limit=100",
            "whole_machine": ["/status.json", "/status.md", "/metrics"],
        },
        "cursor": {"parameters": ["after", "limit", "session_id"],
            "usage": "将返回的 next_sequence 和 session_id 用于下一次请求；reset=true 表示会话变更，truncated=true 表示环形缓存已覆盖数据",
            "hardware_retention_packets": 240, "bandwidth_retention_samples": 600,
            "hardware_values": "packets[].samples[].values 按 cohorts[packet.cohort] 顺序对应；GPU start_ns/end_ns 不是 Unix 时间；received_at 是接收时间；原始时间允许少量转换抖动"},
        "quality": [
            "读取 ready、max_age_seconds、missing_groups 和每组 sampled_at / cohort / window_id；ready 不表示所有组同步",
            "仅相同 cohort 和 window_id 的 CUPTI 指标来自同一窗口；NVML PCIe 是独立的 20 ms 驱动窗口，10 Hz 轮询并非无间隙覆盖",
            "百分比不可相加；整卡计数器不能按进程显存拆分归属；Ginst/s 不是 TFLOPS",
            "DRAM GB/s 标记 estimated，是实测百分比乘 NVML 时钟/位宽推算的理论 DDR 带宽；不是直接字节计数",
            "PCIe 百分比以当前协商链路单方向编码后线速为分母，尚未扣除数据包开销；TX/RX 从 GPU 视角",
            "基础整机快照约 1 Hz；硬件 API 直接读取采集缓存，不额外启动探针；采样配置不跨后端重启保存",
        ],
        "examples": [
            {"method": "POST", "url": "/api/sampling-control?action=start&target=all&gpu=0&mode=compute&replace=true"},
            {"method": "GET", "url": "/api/sampling-control?action=state"},
            {"method": "POST", "url": "/api/sampling-control?action=stop&target=hardware"},
            {"method": "POST", "url": "/api/sampling-control?action=capture&target=nsight&gpu=0&duration=5"},
        ],
    }
