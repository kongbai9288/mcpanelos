"""服务器实例：创建 / 启动 / 停止 / 控制台 / 状态。

进程用 pty 伪终端承载，保证能像交互式终端一样向服务端 stdin 写命令（stop、op、whitelist）。
支持 '自动 root 密码输入'：启动前若需要提权（如绑定 25565 或写 systemd），自动喂 sudo 密码。
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

from .util import (
    Result,
    config,
    humansize,
    instance_dir,
    run,
    servers_dir,
    slug,
    tail_lines,
    now_str,
)
from . import java as javamod
from . import downloader

PROCS: dict[str, dict] = {}
_LOCK = threading.Lock()


# ---------------------------------------------------------------- 元数据


def _cfg_instances() -> dict:
    return config().get("instances", {}) or {}


def save_instance(meta: dict):
    cfg = config()
    inst = cfg.get("instances", {}) or {}
    inst[meta["name"]] = meta
    cfg.set("instances", inst)


def get_instance(name: str) -> dict | None:
    return (_cfg_instances()).get(name)


def list_instances() -> list[dict]:
    out = []
    for name, meta in (_cfg_instances()).items():
        m = dict(meta)
        m["running"] = is_running(name)
        out.append(m)
    return out


def remove_instance(name: str, delete_files: bool = False) -> tuple[bool, str]:
    if is_running(name):
        return False, "请先停止实例"
    cfg = config()
    inst = cfg.get("instances", {}) or {}
    inst.pop(name, None)
    cfg.set("instances", inst)
    if delete_files:
        d = instance_dir(name)
        if d.exists():
            import shutil

            shutil.rmtree(d, ignore_errors=True)
    return True, "已删除"


def create_instance(
    name: str,
    kind: str,
    version: str,
    memory: str = "2G",
    java_path: str | None = None,
    port: int = 25565,
    extra_flags: str = "",
    eula: bool = True,
    bedrock: bool = False,
    install: bool = True,
) -> tuple[bool, str, dict | None]:
    """创建实例目录并（可选）下载安装服务端。"""
    name = slug(name)
    if get_instance(name):
        return False, "同名实例已存在", None
    d = instance_dir(name)
    d.mkdir(parents=True, exist_ok=True)

    java_bin = java_path
    meta = {
        "name": name,
        "kind": kind,
        "version": version,
        "memory": memory,
        "extra_flags": extra_flags,
        "port": port,
        "dir": str(d),
        "bedrock": bedrock or kind == "bedrock",
        "created": now_str(),
        "jar": "bedrock_server" if (bedrock or kind == "bedrock") else "server.jar",
        "java": java_bin or "",
        "auto_restart": False,
        "autostart": False,
    }

    if install:
        if not meta["bedrock"]:
            req = _required_java(kind, version)
            pick = javamod.pick_java(req, java_bin)
            if not pick:
                r = javamod.ensure_java(req, auto_install=config().get("java_auto_install", True))
                if not r.get("ok"):
                    return False, f"Java 准备失败：{r.get('msg')}", None
                pick = r["java"]
            meta["java"] = pick
            ok, jar = downloader.install_core(d, kind, version, pick)
            if not ok:
                return False, f"服务端安装失败：{jar}", None
            meta["jar"] = jar
        else:
            ok, jar = downloader.install_core(d, "bedrock", version)
            if not ok:
                return False, f"基岩版安装失败：{jar}", None
            meta["jar"] = jar

    if eula and not meta["bedrock"]:
        (d / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    _write_properties(d, port, meta["bedrock"])
    save_instance(meta)
    return True, "创建成功", meta


def _required_java(kind: str, version: str) -> int:
    kind = kind.lower()
    if kind in ("forge", "neoforge"):
        return 17
    try:
        parts = version.split(".")
        minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if minor >= 21:
            return 21
        if minor >= 18:
            return 17
        return 17 if minor >= 17 else 8
    except Exception:
        return 21


def _write_properties(d: Path, port: int, bedrock: bool):
    p = d / "server.properties"
    if bedrock:
        default = {
            "server-name": "MC Panel Bedrock",
            "gamemode": "survival",
            "difficulty": "normal",
            "allow-cheats": "false",
            "max-players": "10",
            "server-port": str(port or 19132),
            "server-portv6": str((port or 19132) + 1),
            "online-mode": "true",
            "white-list": "false",
            "level-name": "Bedrock level",
            "view-distance": "10",
        }
    else:
        default = {
            "motd": "A MC Panel Server",
            "server-port": str(port or 25565),
            "max-players": "20",
            "view-distance": "10",
            "simulation-distance": "10",
            "online-mode": "true",
            "white-list": "false",
            "enable-command-block": "false",
            "level-name": "world",
            "gamemode": "survival",
            "difficulty": "normal",
        }
    if p.exists():
        return
    p.write_text("\n".join(f"{k}={v}" for k, v in default.items()) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 进程


def _build_cmd(meta: dict) -> list[str]:
    d = Path(meta["dir"])
    if meta.get("bedrock"):
        exe = d / (meta.get("jar") or "bedrock_server")
        return [str(exe)]
    java = meta.get("java") or javamod.pick_java() or "java"
    jar = meta.get("jar") or "server.jar"
    mem = meta.get("memory") or "2G"
    flags = [
        f"-Xms{mem}",
        f"-Xmx{mem}",
        "-XX:+UseG1GC",
        "-XX:+ParallelRefProcEnabled",
        "-XX:MaxGCPauseMillis=200",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:+DisableExplicitGC",
        "-XX:+AlwaysPreTouch",
        "-Dlog4j2.formatMsgNoLookups=true",  # 缓解 log4shell 类问题
    ]
    extra = shlex.split(meta.get("extra_flags") or "")
    return [java] + flags + extra + ["-jar", jar, "nogui"]


def is_running(name: str) -> bool:
    with _LOCK:
        p = PROCS.get(name)
    if not p:
        return False
    return p["proc"].poll() is None


def start(name: str) -> tuple[bool, str]:
    meta = get_instance(name)
    if not meta:
        return False, "实例不存在"
    if is_running(name):
        return False, "已在运行"
    d = Path(meta["dir"])
    if not d.exists():
        return False, "目录不存在"
    if meta.get("bedrock"):
        exe = d / (meta.get("jar") or "bedrock_server")
        if not exe.exists():
            return False, f"找不到 {exe.name}"
    else:
        if not (d / (meta.get("jar") or "server.jar")).exists():
            return False, "找不到服务端 jar"
        if not (d / "eula.txt").exists() or "true" not in (d / "eula.txt").read_text(encoding="utf-8", errors="ignore"):
            (d / "eula.txt").write_text("eula=true\n", encoding="utf-8")

    cmd = _build_cmd(meta)
    master, slave = pty.openpty()
    logfile = open(d / "console.log", "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(d),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=os.setsid,
            close_fds=True,
        )
    except Exception as ex:
        os.close(master)
        os.close(slave)
        logfile.close()
        return False, f"启动失败：{ex}"
    os.close(slave)

    rec = {
        "proc": proc,
        "master": master,
        "log": logfile,
        "meta": meta,
        "started": time.time(),
        "auto_restart": bool(meta.get("auto_restart")),
        "pid": proc.pid,
    }
    with _LOCK:
        PROCS[name] = rec

    t = threading.Thread(target=_reader, args=(name,), daemon=True)
    t.start()
    return True, f"已启动 pid={proc.pid}"


def _reader(name: str):
    with _LOCK:
        rec = PROCS.get(name)
    if not rec:
        return
    master, log, proc = rec["master"], rec["log"], rec["proc"]
    while True:
        try:
            r, _, _ = select.select([master], [], [], 0.5)
        except Exception:
            break
        if r:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            try:
                log.write(data)
            except Exception:
                pass
            try:
                bus_append(name, data.decode("utf-8", "ignore"))
            except Exception:
                pass
        if proc.poll() is not None:
            # 排空残留
            try:
                while True:
                    rr, _, _ = select.select([master], [], [], 0.2)
                    if not rr:
                        break
                    data = os.read(master, 65536)
                    if not data:
                        break
                    log.write(data)
                    bus_append(name, data.decode("utf-8", "ignore"))
            except Exception:
                pass
            break
    try:
        log.close()
    except Exception:
        pass
    try:
        os.close(master)
    except Exception:
        pass
    if rec.get("auto_restart") and not rec.get("_stopping"):
        time.sleep(3)
        try:
            start(name)
        except Exception:
            pass


def send(name: str, line: str) -> tuple[bool, str]:
    with _LOCK:
        rec = PROCS.get(name)
    if not rec:
        return False, "未运行"
    try:
        os.write(rec["master"], (line.rstrip("\n") + "\n").encode("utf-8"))
        return True, "已发送"
    except OSError as ex:
        return False, str(ex)


def stop(name: str, force: bool = False, timeout: int = 30) -> tuple[bool, str]:
    with _LOCK:
        rec = PROCS.get(name)
    if not rec:
        return False, "未运行"
    rec["_stopping"] = True
    proc = rec["proc"]
    meta = get_instance(name) or rec["meta"]
    try:
        if not force and not meta.get("bedrock"):
            os.write(rec["master"], b"stop\n")
            for _ in range(timeout * 2):
                if proc.poll() is not None:
                    break
                time.sleep(0.5)
    except OSError:
        pass
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        for _ in range(10):
            if proc.poll() is not None:
                break
            time.sleep(0.5)
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
    with _LOCK:
        PROCS.pop(name, None)
    return True, "已停止"


def kill(name: str) -> tuple[bool, str]:
    return stop(name, force=True)


def status(name: str) -> dict:
    meta = get_instance(name)
    if not meta:
        return {"error": "实例不存在"}
    running = is_running(name)
    pid = None
    cpu = mem = None
    if running:
        with _LOCK:
            rec = PROCS.get(name)
        if rec:
            pid = rec["proc"].pid
            try:
                r = run(["ps", "-o", "%cpu=", "-o", "rss=", "-p", str(pid)], timeout=5)
                parts = r.out.split()
                if len(parts) >= 2:
                    cpu = float(parts[0])
                    mem = humansize(int(parts[1]) * 1024)
            except Exception:
                pass
    return {
        "name": name,
        "running": running,
        "pid": pid,
        "cpu": cpu,
        "mem": mem,
        "kind": meta.get("kind"),
        "version": meta.get("version"),
        "dir": meta.get("dir"),
        "port": meta.get("port"),
        "uptime": int(time.time() - PROCS[name]["started"]) if running and name in PROCS else 0,
    }


# ---------------------------------------------------------------- 控制台缓冲


BUS: dict[str, list[str]] = {}
BUS_MAX = 2000


def bus_append(name: str, text: str):
    with _LOCK:
        buf = BUS.setdefault(name, [])
        for line in text.splitlines():
            buf.append(line)
        if len(buf) > BUS_MAX:
            del buf[: len(buf) - BUS_MAX]


def console_tail(name: str, n: int = 300) -> list[str]:
    with _LOCK:
        buf = BUS.get(name, [])
    if buf:
        return buf[-n:]
    d = instance_dir(name)
    f = d / "console.log"
    if f.exists():
        return tail_lines(f, n)
    return []


def send_command(name: str, cmd: str) -> tuple[bool, str]:
    """发送一条服务器命令（自动去掉前导 /）。"""
    cmd = cmd.strip()
    if cmd.startswith("/"):
        cmd = cmd[1:]
    return send(name, cmd)


# ---------------------------------------------------------------- 备份


def backup(name: str, keep_dir: str | None = None) -> tuple[bool, str]:
    import tarfile

    meta = get_instance(name)
    if not meta:
        return False, "实例不存在"
    d = Path(meta["dir"])
    keep = Path(keep_dir) if keep_dir else Path(meta["dir"]).parent / "backups"
    keep.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = keep / f"{name}-{stamp}.tar.gz"
    world_dirs = [p for p in ("world", "world_nether", "world_the_end") if (d / p).exists()]
    if not world_dirs:
        world_dirs = [p.name for p in d.iterdir() if p.is_dir() and p.name not in ("logs", "plugins", "mods", "libraries", "config")]
    try:
        was_running = is_running(name)
        if was_running:
            send(name, "save-off")
            send(name, "save-all")
            time.sleep(3)
        with tarfile.open(dest, "w:gz") as tf:
            for w in world_dirs:
                tf.add(d / w, arcname=w)
            for extra in ("server.properties", "ops.json", "whitelist.json", "banned-players.json"):
                if (d / extra).exists():
                    tf.add(d / extra, arcname=extra)
        if was_running:
            send(name, "save-on")
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"
    return True, str(dest)


def list_backups(name: str | None = None) -> list[dict]:
    root = servers_dir().parent / "backups"
    outs = []
    for base in (servers_dir() / "backups", root):
        if not base.exists():
            continue
        for f in sorted(base.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
            if name and not f.name.startswith(name + "-"):
                continue
            outs.append(
                {
                    "name": f.name,
                    "path": str(f),
                    "size": humansize(f.stat().st_size),
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                }
            )
    return outs
