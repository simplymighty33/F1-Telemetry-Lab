"""Low-cost, hysteretic resource policy. Never drops a Raw datagram."""
from dataclasses import dataclass

MIB = 1024 * 1024


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
