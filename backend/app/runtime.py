"""What hardware are we actually running on?

`os.cpu_count()` reports the host's cores, not the share a container is
allowed to use. On a 0.5-vCPU instance that is wrong by an order of
magnitude — and a measurement whose hardware is unknown is a measurement you
cannot compare to anything later.

This reads the cgroup quota, which is the number that actually governs how
fast the CPU stages run, and `/api/health` reports it so every measurement
records the machine it came from.
"""

from __future__ import annotations

import os
from pathlib import Path

CGROUP_V2 = Path("/sys/fs/cgroup/cpu.max")
CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _from_cgroup_v2() -> float | None:
    raw = _read(CGROUP_V2)
    if not raw:
        return None
    parts = raw.split()
    # "max 100000" means no quota; "50000 100000" means half a CPU.
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return quota / period if period > 0 else None


def _from_cgroup_v1() -> float | None:
    quota_raw, period_raw = _read(CGROUP_V1_QUOTA), _read(CGROUP_V1_PERIOD)
    if not quota_raw or not period_raw:
        return None
    try:
        quota, period = int(quota_raw), int(period_raw)
    except ValueError:
        return None
    if quota <= 0 or period <= 0:  # -1 means unlimited
        return None
    return quota / period


def cpu_info() -> dict[str, object]:
    """Cores visible, cores actually allowed, and where that came from."""
    limit = _from_cgroup_v2()
    source = "cgroup v2"
    if limit is None:
        limit = _from_cgroup_v1()
        source = "cgroup v1"
    if limit is None:
        source = "no quota (unlimited or not a container)"

    return {
        "cpu_count": os.cpu_count(),
        "cpu_limit": round(limit, 3) if limit is not None else None,
        "cpu_limit_source": source,
        "workers": int(os.environ.get("WEB_CONCURRENCY", "1") or 1),
    }


def describe() -> str:
    """One line for a measurement header."""
    info = cpu_info()
    limit = info["cpu_limit"]
    allowed = f"{limit} vCPU" if limit is not None else "no quota"
    return (
        f"{allowed} (host reports {info['cpu_count']} cores, "
        f"{info['cpu_limit_source']}), {info['workers']} worker(s)"
    )
