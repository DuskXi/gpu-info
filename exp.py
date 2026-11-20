import os

if os.name == "nt":
    import wmi
else:
    import pynvml

def get_process_gpu_memory():
    # 初始化 WMI
    w = wmi.WMI()
    
    print(f"{'PID':<10} | {'专用 GPU 内存 (MB)':<20} | {'共享 GPU 内存 (MB)':<20}")
    print("-" * 55)
    
    # 获取 GPU 进程内存性能计数器
    # 注意：在某些系统中可能需要使用 Win32_PerfRawData_GPUPerformanceCounters_GPUProcessMemory
    gpu_counters = w.Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory()
    
    for counter in gpu_counters:
        # 计数器的 Name 格式通常为: pid_1234_luid_0x00000000_0x00000000_phys_0
        name = counter.Name
        
        # 过滤掉非特定进程的总计数据
        if "pid_" in name:
            try:
                # 从 Name 中解析出 PID
                pid_str = name.split('_')[1]
                pid = int(pid_str)
                
                # 获取专用和共享显存（单位为 Bytes，转换为 MB）
                # DedicatedUsage 对应任务管理器中的 "专用 GPU 内存"
                # SharedUsage 对应任务管理器中的 "共享 GPU 内存"
                dedicated_mb = int(counter.DedicatedUsage) / (1024 * 1024)
                shared_mb = int(counter.SharedUsage) / (1024 * 1024)
                
                # 过滤掉占用为 0 的休眠进程
                if dedicated_mb > 0 or shared_mb > 0:
                    print(f"{pid:<10} | {dedicated_mb:<20.2f} | {shared_mb:<20.2f}")
                    
            except IndexError:
                continue


def get_nvidia_process_memory():
    pynvml.nvmlInit()
    device_count = pynvml.nvmlDeviceGetCount()
    
    print(f"{'GPU ID':<8} | {'PID':<10} | {'VRAM 占用 (MB)':<15}")
    print("-" * 40)
    
    for i in range(device_count):
        handle = pynvml.nvmlDeviceGetHandleByIndex(i)
        
        # 获取计算进程（如 CUDA/深度学习）
        compute_processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
        # 获取图形进程（如 游戏/桌面渲染）
        graphics_processes = pynvml.nvmlDeviceGetGraphicsRunningProcesses(handle)
        
        # 合并两类进程
        all_processes = compute_processes + graphics_processes
        
        for p in all_processes:
            pid = p.pid
            # 显存单位为 Bytes，转换为 MB
            used_mb = (p.usedGpuMemory / (1024 * 1024)) if p.usedGpuMemory else 0
            
            print(f"{i:<8} | {pid:<10} | {used_mb:<15.2f}")
            
    pynvml.nvmlShutdown() 
if __name__ == "__main__":
    if os.name == "nt":
        get_process_gpu_memory() 
    else:
        get_nvidia_process_memory()