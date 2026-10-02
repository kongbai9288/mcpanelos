"""通用工具：路径、命令执行、提权、JSON 存储、文件小工具。"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from . import APP

# ---------------------------------------------------------------- 路径


def _base() -> Path:
    env = os.environ.get("MCPANEL_HOME")
    if env:
        p = Path(env)
    elif os.name == "posix" and _writable("/var/lib"):
        p = Path("/var/lib") / APP
    else:
        p = Path.home() / f".{APP}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _writable(path: str) -> bool:
    try:
        return os.access(path, os.W_OK)
    except Exception:
        return False


def home() -> Path:
    return _base()


def servers_dir() -> Path:
    env = os.environ.get("MCPANEL_SERVERS")
    if env:
        p = Path(env)
    elif _writable("/srv"):
        p = Path("/srv/minecraft")
    else:
        p = home() / "servers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sub(name: str) -> Path:
    p = home() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def runs_dir() -> Path:
    return sub("runs")


def pipelines_dir() -> Path:
    return sub("pipelines")


def cache_dir() -> Path:
    return sub("cache")


def panel_log_dir() -> Path:
    return sub("logs")


def instance_dir(name: str) -> Path:
    p = servers_dir() / name
    return p


# ---------------------------------------------------------------- 命令执行


class Result:
    __slots__ = ("rc", "out", "err", "cmd", "duration")

    def __init__(self, rc: int, out: str, err: str, cmd: str = "", duration: float = 0.0):
        self.rc, self.out, self.err, self.cmd, self.duration = rc, out, err, cmd, duration

    def ok(self) -> bool:
        return self.rc == 0

    def as_dict(self) -> dict:
        return {
            "rc": self.rc,
            "stdout": self.out,
            "stderr": self.err,
            "cmd": self.cmd,
            "duration": round(self.duration, 3),
        }


def run(cmd, cwd=None, timeout=None, env=None, input_text=None, shell=False, text=True):
    """执行命令，返回 Result。cmd 可为 list 或字符串（shell=True）。"""
    t0 = time.time()
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=e,
            input=input_text if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=shell,
            text=text,
        )
        return Result(p.returncode, p.stdout or "", p.stderr or "", str(cmd), time.time() - t0)
    except FileNotFoundError as ex:
        return Result(127, "", f"命令不存在: {ex}", str(cmd), time.time() - t0)
    except subprocess.TimeoutExpired as ex:
        out = ex.stdout or ""
        err = ex.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "ignore")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "ignore")
        return Result(124, out, err + "\n[超时]", str(cmd), time.time() - t0)
    except Exception as ex:  # noqa
        return Result(1, "", f"{type(ex).__name__}: {ex}", str(cmd), time.time() - t0)


def which(name: str) -> str | None:
    return shutil.which(name)


# ---------------------------------------------------------------- 提权（自动 root 密码）


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def sudo_prefix() -> list[str]:
    """返回用于提权的前缀命令列表。"""
    if is_root():
        return []
    from .secret import get_sudo_password  # 延迟导入避免循环

    pw = get_sudo_password()
    if pw is not None:
        return ["sudo", "-S", "-p", "", "-k"]
    return ["sudo", "-n"]


def as_root(cmd, cwd=None, timeout=None, password: str | None = None):
    """以 root 身份执行命令。优先免密（root/sudoers），否则用保存的密码自动输入。"""
    if is_root():
        return run(cmd, cwd=cwd, timeout=timeout)

    if password is None:
        if sudo_nopasswd_ok():
            password = None
            full = ["sudo", "-n"] + (cmd if isinstance(cmd, list) else ["sh", "-c", cmd])
            return run(full, cwd=cwd, timeout=timeout)
        from .secret import get_sudo_password

        password = get_sudo_password()

    if isinstance(cmd, str):
        full = ["sudo", "-S", "-p", "", "sh", "-c", cmd]
    else:
        full = ["sudo", "-S", "-p", ""] + list(cmd)
    if password is None:  # 没有密码就尝试免密
        full = ["sudo", "-n"] + (["sh", "-c", cmd] if isinstance(cmd, str) else list(cmd))
        return run(full, cwd=cwd, timeout=timeout)
    return run(full, cwd=cwd, timeout=timeout, input_text=password + "\n")


def sudo_nopasswd_ok() -> bool:
    if is_root():
        return True
    r = run(["sudo", "-n", "true"], timeout=8)
    return r.rc == 0


def current_user() -> str:
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return os.environ.get("USER", "unknown")


# ---------------------------------------------------------------- JSON 存储


class Store:
    """线程安全的 JSON 键值存储（落盘）。"""

    def __init__(self, path: Path, default: dict | None = None):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        if default:
            for k, v in default.items():
                self.data.setdefault(k, v)
        self.save()

    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, default)

    def set(self, key, value):
        with self._lock:
            self.data[key] = value
            self.save()

    def update(self, **kv):
        with self._lock:
            self.data.update(kv)
            self.save()

    def save(self):
        with self._lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)


_config: Store | None = None


def config() -> Store:
    global _config
    if _config is None:
        _config = Store(
            home() / "config.json",
            {
                "port": 8850,
                "bind": "0.0.0.0",
                "auth_enabled": False,
                "auth_password_hash": "",
                "java_auto_install": True,
                "instances": {},
                "pipelines": {},
                "schedules": {},
                "cleanup": {"keep_backups_days": 14, "keep_logs_days": 7, "keep_crash_days": 30},
                "ui_lang": "zh",
            },
        )
    return _config


# ---------------------------------------------------------------- 文件工具


def humansize(n: float) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < step:
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= step
    return f"{n:.1f}PB"


def tail_lines(path: Path, n: int = 200) -> list[str]:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return []
    except Exception:
        return []
    try:
        text = data.decode("utf-8", "ignore")
    except Exception:
        text = ""
    lines = text.splitlines()
    # 反查最后 n 行（按字节近似，够用）
    return lines[-n:]


def dir_size(path: Path) -> tuple[int, int]:
    total, count = 0, 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
                count += 1
            except OSError:
                pass
    return total, count


def safe_join(base: Path, rel: str) -> Path:
    """防止路径穿越。"""
    base = Path(base).resolve()
    target = (base / rel).resolve()
    if base == target or str(target).startswith(str(base) + os.sep):
        return target
    raise ValueError("非法路径（越界）")


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fa5]+", "-", text.strip())
    return s.strip("-") or "server"
