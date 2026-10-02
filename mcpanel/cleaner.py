"""清理器：日志 / 崩溃报告 / 旧备份 / 缓存 / 无用世界数据，带预览（dry-run）。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .util import config, dir_size, humansize, instance_dir


def list_instances_safe() -> list[dict]:
    from . import instance as inst

    return inst.list_instances()

DAY = 86400


def _age_days(p: Path) -> float:
    return (time.time() - p.stat().st_mtime) / DAY


def _scan_dir(d: Path, older_than_days: float, pattern_suffix: str = "") -> list[dict]:
    out = []
    if not d.exists():
        return out
    for f in d.rglob("*"):
        try:
            if not f.is_file():
                continue
            if pattern_suffix and not f.name.endswith(pattern_suffix):
                continue
            age = _age_days(f)
            if age >= older_than_days:
                out.append({"path": str(f), "size": f.stat().st_size, "age": round(age, 1), "size_h": humansize(f.stat().st_size)})
        except OSError:
            continue
    return out


def targets(instance: str | None = None) -> list[dict]:
    """返回可清理项清单（只扫描，不删除）。"""
    cfg = config().get("cleanup", {}) or {}
    keep_logs = float(cfg.get("keep_logs_days", 7))
    keep_crash = float(cfg.get("keep_crash_days", 30))
    keep_bak = float(cfg.get("keep_backups_days", 14))

    names = [instance] if instance else [(m or {}).get("name") for m in list_instances_safe()]
    res = []
    for name in names:
        if not name:
            continue
        d = instance_dir(name)
        if not d.exists():
            continue
        logs = _scan_dir(d / "logs", keep_logs, ".log.gz") + _scan_dir(d / "logs", keep_logs, ".log")
        crash = _scan_dir(d / "crash-reports", keep_crash)
        backups = _scan_dir(d.parent / "backups", keep_bak, ".tar.gz")
        old_jars = _scan_dir(d, 0, ".jar.old")
        res.append(
            {
                "instance": name,
                "groups": [
                    {"key": "logs", "label": f"日志（>{keep_logs:.0f} 天）", "items": logs},
                    {"key": "crash", "label": f"崩溃报告（>{keep_crash:.0f} 天）", "items": crash},
                    {"key": "backup", "label": f"旧备份（>{keep_bak:.0f} 天）", "items": backups},
                    {"key": "oldjar", "label": "旧版 jar 备份", "items": old_jars},
                ],
            }
        )
    return res


def panel_cache() -> list[dict]:
    from .util import cache_dir, panel_log_dir

    items = []
    for d in (cache_dir(), panel_log_dir()):
        for f in d.rglob("*"):
            try:
                if f.is_file():
                    items.append({"path": str(f), "size": f.stat().st_size, "size_h": humansize(f.stat().st_size), "age": round(_age_days(f), 1)})
            except OSError:
                pass
    return items


def clean(paths: list[str]) -> tuple[int, int, list[str]]:
    deleted, freed, errors = 0, 0, []
    for p in paths:
        try:
            f = Path(p)
            if f.is_file():
                freed += f.stat().st_size
                f.unlink()
                deleted += 1
        except Exception as ex:
            errors.append(f"{p}: {ex}")
    return deleted, freed, errors


def apt_clean() -> tuple[bool, str]:
    from .util import as_root

    r = as_root(["sh", "-c", "apt-get clean && apt-get autoremove -y"], timeout=600)
    return (True, "已清理 apt 缓存与无用依赖") if r.rc == 0 else (False, (r.err or r.out)[-300:])


def journal_clean(keep: str = "300M") -> tuple[bool, str]:
    from .util import as_root

    r = as_root(["journalctl", "--vacuum-size", keep], timeout=300)
    if r.rc != 0:
        return False, (r.err or r.out)[-300:]
    r2 = as_root(["journalctl", "--vacuum-time", "14d"], timeout=300)
    return True, "已回收 journal 日志"


def disk_usage() -> list[dict]:
    out = []
    seen = set()
    for part in run_df():
        if part["mount"] in seen:
            continue
        seen.add(part["mount"])
        out.append(part)
    return out


def run_df() -> list[dict]:
    import subprocess

    try:
        p = subprocess.run(["df", "-h", "-x", "tmpfs", "-x", "devtmpfs"], capture_output=True, text=True, timeout=15)
        lines = (p.stdout or "").splitlines()[1:]
    except Exception:
        return []
    res = []
    for l in lines:
        parts = l.split()
        if len(parts) >= 6:
            res.append(
                {
                    "fs": parts[0],
                    "size": parts[1],
                    "used": parts[2],
                    "avail": parts[3],
                    "use": parts[4],
                    "mount": parts[5],
                }
            )
    return res


def instance_disk(name: str) -> dict:
    d = instance_dir(name)
    total, count = dir_size(d)
    sub = {}
    for p in sorted(d.iterdir()):
        if p.is_dir():
            s, c = dir_size(p)
            sub[p.name] = {"size": s, "size_h": humansize(s), "files": c}
    return {"total": total, "total_h": humansize(total), "files": count, "parts": sub}
