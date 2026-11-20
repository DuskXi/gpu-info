# GPU Monitor

English | [简体中文](README.zh-CN.md)

An NVIDIA GPU monitor. The backend reads hardware data through NVML; the web UI shows an overview, per-process VRAM pie charts, a process VRAM table and single-metric trends over the last 120 frames. Optionally, whole-device hardware performance counters can be read through CUPTI PM Sampling and Nsight Systems. With several GPUs the devices are shown side by side, each with its own process VRAM table below it.

Runs on Windows and Linux, either directly or as a Podman container, and ships a read-only build for production hosts.

## Platform support

| Feature | Windows | Linux |
| --- | --- | --- |
| NVML device metrics, power, temperature, PCIe, training-health fields | ✅ | ✅ |
| Process VRAM | ✅ WDDM performance counters (local usage / dedicated) | ✅ NVML process list |
| Continuous CUPTI hardware sampling | ✅ `native/build.ps1` | ✅ `native/build.sh` or the container image |
| On-demand Nsight Systems capture | ✅ | ✅ (requires `nsys`) |
| Display configuration and display events | ✅ | — not implemented |
| Dedicated / shared VRAM split | ✅ | — not implemented |

Reading performance counters (CUPTI, Nsight GPU Metrics) needs privileges. On Linux the driver defaults to `RmProfilingAdminOnly=1`, so only root / `CAP_SYS_ADMIN` can read them; alternatively set the kernel module parameter `NVreg_RestrictProfilingToAdminUsers=0` (requires reloading the driver or a reboot). Without the privilege NVML monitoring keeps working and the hardware-sampling endpoints return an explicit error.

## Getting started

Requires Python 3.13, [uv](https://docs.astral.sh/uv/) and Node.js / pnpm.

```bash
uv sync
uv run main.py
```

The backend listens on `http://127.0.0.1:8001` by default. Set `GPU_INFO_HOST=0.0.0.0` to allow access from other machines and `GPU_INFO_PORT` to change the port.

Start the frontend dev server in another terminal:

```bash
cd frontend
pnpm install
pnpm dev
```

For production run `pnpm build`; the backend serves `frontend/dist` automatically. The frontend uses hash routing, so the trend page also loads when opened directly. The dev server proxies to `http://127.0.0.1:8001` by default; use `GPU_INFO_API=http://host:port pnpm dev` to point it at another backend (for example a remote read-only deployment).

## Container deployment (Podman)

The `Dockerfile` has two final targets; Python dependencies are installed with uv in both:

| Target | Contents | Intended for |
| --- | --- | --- |
| `full` (default) | NVML + Linux CUPTI helper + CUPTI libraries; host Nsight Systems can be mounted | Development machines, your own training boxes |
| `readonly` | NVML only; no CUPTI helper or build scripts, read-only mode baked in at build time | Production hosts |

```bash
podman build -t gpu-info .                               # full
podman build --target readonly -t gpu-info:readonly .    # readonly
```

`deploy/` contains [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) units. Copy one to `/etc/containers/systemd/gpu-info.container`, then run `systemctl daemon-reload && systemctl start gpu-info` to have systemd manage it (start on boot, restart on failure, logs in journald):

- `deploy/gpu-info.container`: the full image. GPU passthrough via CDI, `--pid=host` (PIDs and process names match the host), host networking, `SYS_ADMIN` for performance counters, and the host Nsight Systems mounted read-only. `run-container.sh` rebuilds the image, imports it into root's storage, updates the unit and restarts the service.
- `deploy/gpu-info-readonly.container`: the readonly image. Instead of CDI it mounts `/dev/nvidiactl`, `/dev/nvidiaN` and the host `libnvidia-ml.so.1` read-only, so a stale host CDI spec after a driver upgrade cannot break startup. All capabilities dropped, `no-new-privileges`, read-only root filesystem. Add one `AddDevice=/dev/nvidiaN` line per GPU.

Neither unit changes the driver, kernel or CDI configuration, and the monitor itself allocates no VRAM.

## Read-only production build

The `readonly` target is meant for hosts that run production inference or training services. There is no way to change state through the API:

1. At build time `build_profile.py` is rewritten to `READONLY = True` and made read-only. It does not depend on an environment variable and cannot be switched back at runtime.
2. At startup every non-GET/HEAD route handler is removed, and `/docs` and `/openapi.json` are disabled.
3. An ASGI middleware answers every method other than GET/HEAD with 405, as a second line of defence behind the routing layer.
4. The CUPTI helper, CUPTI libraries and build scripts are not shipped; performance-counter sampling (CUPTI, Nsight) is hard-disabled.
5. `/app` is not writable and the process runs as `nobody`. The build verifies all of the above and fails if any check does not hold.

Every deployment has access control:

- A per-client-IP sliding-window rate limit (`GPU_INFO_RPM`, 240 requests/minute by default in the read-only build). Excess requests get 429 with `Retry-After`. WebSockets per IP are capped by `GPU_INFO_MAX_WS_PER_IP` (8 by default in the read-only build).
- A JSONL access log: IP, method, path, query, status, duration, user agent and the denial reason (`readonly`, `rate_limited`, …). Written to stdout (journald) and additionally to a file when `GPU_INFO_ACCESS_LOG` is set.
- The client IP is taken from the TCP peer only; the forgeable `X-Forwarded-For` header is ignored.

In a read-only deployment the hardware-sampling and Nsight buttons in the UI receive 405; this is expected.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `GPU_INFO_HOST` | `127.0.0.1` (`0.0.0.0` in the container) | Listen address |
| `GPU_INFO_PORT` | `8001` | Listen port |
| `GPU_INFO_COUNTERS` | `on` | `off` disables CUPTI / Nsight sampling; always disabled in the read-only build |
| `GPU_INFO_RPM` | full `0` (unlimited) / readonly `240` | Requests per minute per IP |
| `GPU_INFO_MAX_WS_PER_IP` | full `0` (unlimited) / readonly `8` | Concurrent WebSockets per IP |
| `GPU_INFO_ACCESS_LOG` | unset (`/app/data/access.log` in the readonly image) | Access log file path |
| `GPU_INFO_PM_HELPER` | `native/bin/cupti_stream[.exe]` | CUPTI helper path |
| `GPU_INFO_CUPTI_PATH` | Windows: CUDA 12.6 `extras/CUPTI/lib64`; Linux: `/usr/local/cuda/lib64` | CUPTI library directory |
| `GPU_INFO_NSYS_PATH` | `nsys` looked up in `PATH` | Nsight Systems CLI path |
| `GPU_INFO_API` | `http://127.0.0.1:8001` | Frontend dev server only: proxy target |

## Data flow

`GPUMonitor.capture()` produces one complete hardware sample per call. A single-threaded sampler hands it to `TelemetryStore`, which adds a timestamp and an increasing sequence number, keeps the last 120 frames, and publishes the same message format to the history endpoint and the WebSocket:

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

The frontend `TelemetryClient` feeds both history and WebSocket messages into `ingest()`, de-duplicating by stream id and sequence. After a server restart it switches to the new stream; on disconnect it keeps old frames but shows the connection state. A missing sample is never filled by repeating the previous frame. The status HTTP endpoints add a request-time `health` object to the raw snapshot, covering the freshness of the frame, the Windows process counters and the display configuration. They also include the latest on-demand hardware overview `activity_probe` and instruction-pipeline result `pipeline_probe`, each with its own timestamp separate from the live frame.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Latest snapshot plus `health`; 503 before the first sample |
| `GET /status.json` | Full latest snapshot at the root path; same content as `/api/status` |
| `GET /status.md` | Human-readable status summary at the root path, `text/markdown` |
| `GET /metrics` | Prometheus text metrics at the root path; system and device metrics only by default |
| `GET /metrics?include_processes=true` | Additionally emits per-process metrics labelled with GPU, PID and process name |
| `GET /api/history?limit=120` | The last 1–120 frames in ascending time order |
| `POST /api/activity-probe?gpu=0[&duration=2]` | Captures 1–5 s of device-level GPU hardware activity (5 s by default); requires the Nsight Systems CLI and counter privileges |
| `POST /api/activity-probe?gpu=0&kind=pipelines` | Captures AD10x Tensor, FMA Light/Heavy, ALU and SFU pipeline throughput |
| `GET /api/activity-probe[?kind=pipelines]` | The latest result of that kind; cleared on server restart |
| `GET /api/sampling-control` | JSON request document for the unified sampling control; no parameters means no state change |
| `GET /api/sampling-control?action=state` | All continuous-sampling switches, their actual state and errors |
| `POST /api/sampling-control?action=start&target=all&gpu=0&mode=compute&replace=true` | Starts base telemetry, same-window hardware counters and fast PCIe sampling; hardware/bandwidth/telemetry can be controlled separately |
| `POST /api/sampling-control?action=stop&target=all` | Stops all continuous sampling; a bounded Nsight capture ends on its own |
| `POST /api/hardware-sampling?enabled=true&gpu=0&mode=compute` | Legacy hardware switch; mode is one of compute/diagnostic/all/activity/cache/pipelines/tensor/scalar/memory |
| `POST /api/hardware-sampling?enabled=false` | Stops the shared continuous session and releases the counters |
| `GET /api/hardware-sampling` | Reads the fast hardware cache directly, with fast PCIe sampling state |
| `GET /api/hardware-sampling/samples?after=0&limit=100` | Ring buffer of raw CUPTI samples with a sequence/session_id incremental cursor |
| `GET /api/bandwidth-sampling` | 10 Hz NVML PCIe sampling state and latest values |
| `GET /api/bandwidth-sampling/samples?after=0&limit=100` | Incremental PCIe samples with their own session and cursor |
| `GET /api/display-events?limit=50` | The last 1–200 display configuration events |
| `WS /api/ws` | Live snapshots; slow clients only keep the newest frame |

The POST endpoints do not exist in the read-only build.

The three root-path endpoints only format the latest frame already held by `TelemetryStore`; a request never triggers extra sampling. The frontend dev server proxies these paths to the backend as well. All return 503 before the first sample. When a frame is more than 5 s old, the status endpoints mark it stale and `/metrics` sets `gpu_info_up` to 0 and stops emitting old hardware readings; once the Windows process counters are older than 30 s, old per-process readings are dropped. Prometheus can compute sample age from the timestamp metrics. Metrics use the `gpu_info_` prefix, base units such as bytes and hertz, and 0–1 ratios; missing counters emit no sample. Per-process series grow as PIDs change, so only enable them when you really need long-term scraping. Process local usage, dedicated counters and NVML device memory measure different things; summing the process series is not the real device usage.

## Accounting

- Total GPU memory usage comes from NVML and includes the driver and allocations not attributed to any process.
- The overview pie chart slices each GPU by the processes' `Local Usage` readings, falling back to NVML process readings where Windows has none. Only the five largest processes are shown; the rest are merged. When the largest process accounts for at least 70% of the total, a zoomed pie excluding it is shown next to it, using the same colours. Shared resources are counted more than once, so the pie shows the relative distribution of process readings, not mutually exclusive physical VRAM; the NVML device total is shown alongside for comparison.
- On Windows, active display paths are checked every 5 s via QueryDisplayConfig, recording monitor name, desktop/signal resolution, refresh rate and HDR state. The first read is recorded as a startup baseline; afterwards an event is appended to `data/display-events.jsonl` only when the configuration changes. Each event also stores the NVML memory reading and DWM process counters at that moment for comparison. If an older Windows version can only report "advanced color", HDR state is shown as unknown.
- Under Windows WDDM, NVML per-process memory is usually unavailable. The program uses a context-free CUDA Driver device LUID query to map Windows GPU process counters exactly onto the NVIDIA devices listed by NVML; unmatched integrated-GPU counters are excluded. Other systems use NVML's compute and graphics process lists, taking the larger value when the same GPU / PID appears twice.
- On Windows the process table shows `GPU Process Memory\Local Usage` as "local usage", and keeps `Dedicated Usage` as "dedicated" and `Total Committed` as "total committed". Local usage is closer to what currently resides in GPU-local memory; it can still include window surfaces shared across processes, is not exclusive to the process, and cannot be summed to infer device usage. On some systems the dedicated counter is far higher than actual residency, even above the physical VRAM size.
- `windows_adapter_memory` is the Windows device-level dedicated/shared usage read in the same round as the process values, used on the overview page to cross-check the counters; NVML device usage remains an independent source. The current sampling cannot split DWM's process value into its private allocations, surfaces shared with other programs, and driver-internal resources.
- GPU device metrics are sampled about once per second. Windows process counters are refreshed asynchronously on a separate worker thread at least two seconds apart; `processes_sampled_at` marks the latest process sample.
- The process picker on the trend page can search by process name or PID and sort by local VRAM, CPU, disk I/O or GPU usage. Process CPU is the delta of process CPU time divided by the number of logical processors, as a whole-machine percentage; disk I/O is the per-second rate of bytes actually read and written, missing on the first sample.
- Windows process GPU usage is the performance-counter value of the busiest engine on the matching NVIDIA device; when unavailable, NVML per-process SM utilization is used. Missing values are shown as `—` and are not sorted as 0.
- Each process also keeps the Windows `GPU Engine` `engtype` breakdown in `engine_utilization`; for several instances of one type the busiest wins. It is available in the last 120 frames, `/status.json`, the trend page and process-labelled `/metrics`. 3D, Compute and Copy are WDDM queue categories, not CUDA, RT Core or Tensor Core hardware utilization. On the author's machine a training Python process at about 87% SM load had about 89% of its activity attributed to the queue named `3d`, so a missing `compute` value does not mean CUDA is idle.
- `gpus[].power.usage` is the whole-device power read by NVML. Power is not split by process or functional unit; several engines may use the same hardware at once, so watts cannot be apportioned by percentage. Any future model must be calibrated against independent workloads and show its estimate, error and unattributable part separately from the measured device power.
- The "process metrics" view on the trend page can switch to curves of the top 3/5/8 processes by the selected metric and filter by GPU. Ranking only uses the latest valid value; missing values are not treated as 0. Each process has its own colour and a legend entry with its PID. Whole-device hardware counters are not ranked because they have no process attribution.
- Windows shared GPU memory and integrated-GPU memory are never added to NVIDIA dedicated memory. The process total does not necessarily equal device usage.
- The monitor does not call CUDA Runtime `cudaSetDevice` / `cudaMemGetInfo`. The Windows CUDA Driver query only reads device identifiers and creates no CUDA context.
- Unavailable optional counters are returned as `null` and shown as gaps in the trend charts.

The raw Windows counters can be checked directly in PowerShell:

```powershell
Get-Counter '\GPU Process Memory(*)\Local Usage', '\GPU Process Memory(*)\Dedicated Usage'
Get-Counter '\GPU Local Adapter Memory(*)\Local Usage'
```

Microsoft's [DirectX post](https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/) explains how DWM / csrss shared resources are counted more than once, and Microsoft has also documented [GPU Process Memory counters reporting abnormally high values](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/gpu-process-memory-counters-report-wrong-value). To see individual allocations and migrations, use GPUView or a Nsight Systems WDDM trace rather than judging leaks from live counters alone.

## Checks

```bash
uv run --with pytest --with httpx python -m pytest tests
mypy .
flake8 .
cd frontend
pnpm test
pnpm build
```

## Continuous hardware sampling for training

On Windows you need CUDA/CUPTI 12.6 and the MSVC x64 build tools:

```powershell
.\native\build.ps1
```

On Linux you need the CUDA toolkit headers and CUPTI (distribution packages or a runfile install both work); the helper is built with g++. The `full` container image does this automatically in an `nvidia/cuda:12.6` devel stage:

```bash
./native/build.sh          # set CUDA_ROOT to point at another CUDA install
```

The helper defaults to `native/bin/cupti_stream.exe` (Windows) or `native/bin/cupti_stream` (Linux); build output is not committed. Default CUPTI library paths are listed in the environment variable table above, and both can be overridden with `GPU_INFO_PM_HELPER` / `GPU_INFO_CUPTI_PATH`. If the helper is not built, unsupported or lacks privileges, an explicit error is returned and NVML/process monitoring keeps running. The metric groups are combinations measured on an AD102; other architectures need their configuration and decoding verified, and metrics with the same name are not guaranteed to exist.

The overview and trend pages share one server-side switch, device and mode, so refreshing or switching pages does not restart sampling. The `compute` same-window mode samples 11 counters (FMA/ALU/SFU, Tensor instructions and memory reads/writes) at 20 Hz and publishes about every 250 ms. `diagnostic` splits the full metric set into four physical groups rotated quickly at about 500 ms each; 20 Hz refers to the active group only, not all metrics at once. Fixed single groups run at 10 Hz and publish about once per second; `all` rotates slowly through six groups at about 3 s each. Averages use at most the last 3 s of **observed intervals** in the current round, and switching groups resets the window; curves break across epochs. The API labels each group with its time, window id, data age and whether it is stale.

Page values update about once per second, while the fast cache and trend points keep the ~250 ms publish rate; switches and error states respond immediately. A global digit roll takes about 1.15 s, slightly longer than the display update period. A new target inherits the current position and velocity, keeps accelerating or decelerating in the same direction, and when reversing first brakes progressively for about 340 ms before turning (included in the 1.15 s). Digits in the background or not visible, and the reduced-motion preference, jump straight to the target to avoid needless animation.

Six display groups: SM/Tensor active; L1/L2/warp; FMA/ALU/SFU occupancy and warp instruction rate; Tensor warp instruction rate; FMA Lite thread instructions actually enabled and effective thread count; memory read/write bandwidth. `Ginst/s` means billions of instructions per second; thread and warp bases are labelled separately. Execution evidence from the ordinary floating-point pipelines cannot be used to infer a particular PID, TFLOPS, the number of CUDA cores busy at once, or exact FP32 utilization. Effective thread count is thread instructions / warp instructions in the same window, range 0–32; it is unavailable when there are no instructions and must not be called core occupancy.

Sampling results flow through `TelemetryStore` into the WebSocket, history, status.json, status.md and metrics. Each hardware group keeps its raw GPU time interval, receive time, effective observed duration, sample count and incomplete-sample count. NVML additionally provides cumulative energy, energy-delta power, instantaneous power, temperature margin, throttle time ratios, raw/unknown clock reasons, driver-reserved memory and PCIe link state. Missing fields are null; an energy counter that goes backwards discards that delta.

The helper creates no CUDA context and runs no CUDA workload. It explicitly uses one 8 MiB hardware buffer and continuously decodes and reuses the counter image. Start, switch, stop and errors all manage the profiler lifecycle, and it shares a mutex with the short Nsight captures. Contention with a third-party profiler, buffer overflow, missing samples and decode errors are reported and stop sampling; nothing is zero-filled. The helper is stopped when the program exits, and if the parent dies unexpectedly the helper is told to stop through stdin EOF. The detail page still has the collapsible extended Nsight capture, reading compute dispatch, graphics warps, instruction issue and memory read/write metrics.

Measured overhead and API limits are in [Training API investigation](benchmarks/nvidia-training-apis.md) (Chinese). 8 MiB is the requested buffer size, not a guarantee of total VRAM used by all driver resources.

Further verification of per-precision Tensor operation counts, correlation with operator shapes and timeline buffer control is in [Compute and implementation efficiency investigation](benchmarks/kernel-efficiency-investigation.md) (Chinese). The range-profiling validation there is not a live TFLOPS reading; the new offline probes do not initialize CUDA or enter the training process.

Measured limits of synchronized vs. rotating sampling, PCIe/memory bandwidth semantics and agent integration examples are in [High-frequency sampling and control API](benchmarks/sampling-control.md) (Chinese). Memory GB/s is an explicitly labelled estimate; PCIe GB/s comes from NVML byte throughput. All new values are included in the root-path status exports; agents that need low latency should use the direct cache and incremental sample endpoints.

## License

[MIT](LICENSE)
