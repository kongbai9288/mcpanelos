"""开机自启动：systemd 单元生成 / 安装 / 管理 + 面板自身服务。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import APP
from .util import as_root, is_root, run, current_user

UNIT_DIR = "/etc/systemd/system"


def unit_name(instance: str) -> str:
    return f"mc-{instance}.service"


def _has_systemd() -> bool:
    return shutil.which("systemctl") is not None and Path("/run/systemd/system").exists()


def generate_unit(meta: dict, user: str | None = None, java: str | None = None) -> str:
    name = meta["name"]
    d = meta["dir"]
    user = user or current_user()
    if meta.get("bedrock"):
        exe = f"{d}/{meta.get('jar') or 'bedrock_server'}"
        exec_start = f'LD_LIBRARY_PATH={d} {exe}'
    else:
        java = java or meta.get("java") or "java"
        mem = meta.get("memory") or "2G"
        exec_start = (
            f"{java} -Xms{mem} -Xmx{mem} -XX:+UseG1GC -XX:MaxGCPauseMillis=200 "
            f"-Dlog4j2.formatMsgNoLookups=true -jar {d}/{meta.get('jar') or 'server.jar'} nogui"
        )
    return f"""[Unit]
Description=Minecraft Server - {name}
After=network.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={d}
ExecStart={exec_start}
Restart=on-failure
RestartSec=10
StandardOutput=append:{d}/console.log
StandardError=append:{d}/console.log
KillSignal=SIGTERM
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
"""


def install(meta: dict, user: str | None = None) -> tuple[bool, str]:
    if not _has_systemd():
        return False, "系统不是 systemd，无法安装服务"
    content = generate_unit(meta, user)
    tmp = Path("/tmp") / unit_name(meta["name"])
    tmp.write_text(content, encoding="utf-8")
    dest = f"{UNIT_DIR}/{unit_name(meta['name'])}"
    r = as_root(["install", "-m", "0644", str(tmp), dest], timeout=30)
    if r.rc != 0:
        return False, (r.err or r.out)[-300:]
    r2 = as_root(["systemctl", "daemon-reload"], timeout=30)
    r3 = as_root(["systemctl", "enable", unit_name(meta["name"])], timeout=30)
    if r3.rc != 0:
        return False, (r3.err or r3.out)[-300:]
    return True, f"已安装并启用 {unit_name(meta['name'])}"


def uninstall(name: str) -> tuple[bool, str]:
    u = unit_name(name)
    as_root(["systemctl", "disable", "--now", u], timeout=40)
    r = as_root(["rm", "-f", f"{UNIT_DIR}/{u}"], timeout=30)
    as_root(["systemctl", "daemon-reload"], timeout=30)
    return (True, "已移除") if r.rc == 0 else (False, r.err or r.out)


def svc_action(name: str, action: str) -> tuple[bool, str]:
    if action not in ("start", "stop", "restart", "status", "enable", "disable"):
        return False, "不支持的操作"
    r = as_root(["systemctl", action, unit_name(name)], timeout=120)
    return (True, (r.out or r.err or "完成").strip()[-800:]) if r.rc == 0 else (False, (r.err or r.out)[-400:])


def svc_status(name: str) -> dict:
    r = as_root(["systemctl", "is-active", unit_name(name)], timeout=20)
    r2 = as_root(["systemctl", "is-enabled", unit_name(name)], timeout=20)
    return {
        "unit": unit_name(name),
        "active": (r.out or "").strip(),
        "enabled": (r2.out or "").strip(),
        "exists": "could not be found" not in (r2.err or "").lower() and "not-found" not in (r2.out or "").lower(),
    }


# ---------------------------------------------------------------- 面板自身


def panel_unit(port: int, exec_path: str, user: str | None = None) -> str:
    user = user or current_user()
    return f"""[Unit]
Description=MC Panel Web Service
After=network.target

[Service]
Type=simple
User={user}
ExecStart={exec_path} serve --port {port}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def install_panel_service(port: int, exec_path: str) -> tuple[bool, str]:
    if not _has_systemd():
        return False, "非 systemd 系统"
    content = panel_unit(port, exec_path)
    tmp = Path("/tmp/mcpanel.service")
    tmp.write_text(content, encoding="utf-8")
    r = as_root(["install", "-m", "0644", str(tmp), f"{UNIT_DIR}/mcpanel.service"], timeout=30)
    if r.rc != 0:
        return False, (r.err or r.out)[-300:]
    as_root(["systemctl", "daemon-reload"], timeout=30)
    r3 = as_root(["systemctl", "enable", "--now", "mcpanel.service"], timeout=60)
    return (True, "面板已设为开机自启并启动") if r3.rc == 0 else (False, (r3.err or r3.out)[-300:])


def uninstall_panel_service() -> tuple[bool, str]:
    as_root(["systemctl", "disable", "--now", "mcpanel.service"], timeout=60)
    r = as_root(["rm", "-f", f"{UNIT_DIR}/mcpanel.service"], timeout=30)
    as_root(["systemctl", "daemon-reload"], timeout=30)
    return (True, "已移除") if r.rc == 0 else (False, r.err or r.out)


# ---------------------------------------------------------------- 备选方案


def cron_line(cmd: str) -> str:
    return f"@reboot {cmd}"


def install_cron(meta: dict) -> tuple[bool, str]:
    """非 systemd 环境的兜底：@reboot 任务。"""
    line = cron_line(f"cd {meta['dir']} && ./start.sh")
    start_sh = Path(meta["dir"]) / "start.sh"
    if not start_sh.exists():
        if meta.get("bedrock"):
            start_sh.write_text(f"#!/bin/sh\ncd {meta['dir']}\nLD_LIBRARY_PATH=. ./{meta.get('jar') or 'bedrock_server'}\n", encoding="utf-8")
        else:
            java = meta.get("java") or "java"
            mem = meta.get("memory") or "2G"
            start_sh.write_text(
                f"#!/bin/sh\ncd {meta['dir']}\nexec {java} -Xms{mem} -Xmx{mem} -jar {meta.get('jar') or 'server.jar'} nogui\n",
                encoding="utf-8",
            )
        start_sh.chmod(0o755)
    r = run(["sh", "-c", f'(crontab -l 2>/dev/null | grep -v "mcpanel-{meta["name"]}"; echo "@reboot # mcpanel-{meta["name"]} {start_sh}") | crontab -'], timeout=30)
    if r.rc != 0:
        return False, (r.err or r.out)[-300:]
    return True, "已加入 @reboot 计划任务"


def environment() -> dict:
    return {
        "systemd": _has_systemd(),
        "root": is_root(),
        "user": current_user(),
        "has_cron": shutil.which("crontab") is not None,
    }
