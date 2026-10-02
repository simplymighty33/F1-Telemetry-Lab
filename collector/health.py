"""Low-cost, hysteretic resource policy. Never drops a Raw datagram."""
from dataclasses import dataclass
from functools import lru_cache

MIB = 1024 * 1024


@lru_cache(maxsize=1)
def _windows_memory_reader():
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return kernel, psapi, Counters


def process_memory_bytes():
    """Own-process Windows resident/private bytes; no dependency or UI metric.

    Best-effort once per health check. A failed/unsupported query is unknown,
    not zero. Start/last/peak samples alone do not prove a memory leak.
    """
    import os
    if os.name != "nt":
        return None
    try:
        import ctypes
        kernel, psapi, Counters = _windows_memory_reader()
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return {"working_set_bytes": counters.WorkingSetSize, "private_bytes": counters.PrivateUsage}
    except (OSError, AttributeError):
        return None


@dataclass(frozen=True)
class ResourceDecision:
    pause_derived: bool
    stop_capture: bool
    warnings: tuple[str, ...]


def resource_decision(queue_size, capacity, free_bytes, *, paused=False, foundation_lag=0, analysis_lag=0):
    ratio = queue_size / max(1, capacity)
    pause = ratio >= (0.25 if paused else 0.60) or (free_bytes is not None and free_bytes < 1024 * MIB)
    warnings = []
    if ratio >= 0.60 or (paused and ratio >= 0.25):
        warnings.append("写盘压力较高：后台整理与分析暂缓，优先保存原始数据")
    if free_bytes is None:
        warnings.append("暂时无法检查磁盘剩余空间")
    elif free_bytes < 128 * MIB:
        warnings.append("磁盘剩余空间不足128 MiB：正在安全停止采集，请释放空间")
    elif free_bytes < 1024 * MIB:
        warnings.append("磁盘剩余空间不足1 GiB：后台处理暂缓，请尽快释放空间")
    if foundation_lag >= 10000 or analysis_lag >= 10000:
        warnings.append("后台处理已有积压；原始数据继续保存，可在停止后恢复分析")
    return ResourceDecision(pause, free_bytes is not None and free_bytes < 128 * MIB, tuple(warnings))
