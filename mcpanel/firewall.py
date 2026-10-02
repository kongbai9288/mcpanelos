"""防火墙面板：ufw / iptables / nftables 的统一封装（自动提权）。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .util import as_root, run, Result

BACKENDS = ("ufw", "firewalld", "nft", "iptables")


def backend() -> str:
    for b in BACKENDS:
        if shutil.which(b):
            return b
    return "none"


def install_ufw() -> tuple[bool, str]:
    r = as_root(["sh", "-c", "DEBIAN_FRONTEND=noninteractive apt-get install -y ufw"], timeout=600)
    return (True, "已安装 ufw") if r.rc == 0 else (False, (r.err or r.out)[-400:])


def status() -> dict:
    b = backend()
    out = {"backend": b, "active": False, "raw": "", "rules": []}
    if b == "ufw":
        r = as_root(["ufw", "status", "verbose"], timeout=20)
        out["raw"] = (r.out or "") + (r.err or "")
        out["active"] = "Status: active" in out["raw"]
        for line in out["raw"].splitlines()[4:]:
            line = line.strip()
            if line:
                out["rules"].append(line)
    elif b == "iptables":
        r = as_root(["iptables", "-L", "-n", "--line-numbers"], timeout=20)
        out["raw"] = (r.out or "") + (r.err or "")
        out["active"] = r.rc == 0 and "Chain INPUT" in out["raw"]
        out["rules"] = [l for l in out["raw"].splitlines() if l.strip().startswith(("Chain", "num", "ACCEPT", "DROP", "REJECT"))]
    elif b == "firewalld":
        r = as_root(["firewall-cmd", "--state"], timeout=20)
        out["active"] = r.rc == 0 and "running" in (r.out or "")
        r2 = as_root(["firewall-cmd", "--list-all"], timeout=20)
        out["raw"] = r2.out or r2.err
        out["rules"] = [l for l in out["raw"].splitlines() if l.strip()]
    elif b == "nft":
        r = as_root(["nft", "list", "ruleset"], timeout=20)
        out["raw"] = r.out or r.err
        out["active"] = r.rc == 0 and bool(out["raw"].strip())
    else:
        out["raw"] = "未检测到防火墙后端，可一键安装 ufw"
    return out


def _ufw_rule(action: str, port: int, proto: str, comment: str = "") -> tuple[bool, str]:
    if port <= 0 or port > 65535:
        return False, "端口范围无效"
    if proto not in ("tcp", "udp", "any"):
        return False, "协议无效"
    spec = str(port) if proto == "any" else f"{port}/{proto}"
    cmd = ["ufw", action, spec]
    r = as_root(cmd, timeout=40)
    return (True, f"{action} {spec} 成功") if r.rc == 0 else (False, (r.err or r.out)[-400:])


def allow_port(port: int, proto: str = "tcp", comment: str = "") -> tuple[bool, str]:
    b = backend()
    if b == "ufw":
        return _ufw_rule("allow", port, proto, comment)
    if b == "firewalld":
        r = as_root(["firewall-cmd", "--permanent", "--add-port", f"{port}/{proto}"], timeout=40)
        r2 = as_root(["firewall-cmd", "--reload"], timeout=40)
        return (True, "已放行") if r.rc == 0 else (False, r.err or r.out)
    if b == "iptables":
        r = as_root(["iptables", "-I", "INPUT", "-p", proto, "--dport", str(port), "-j", "ACCEPT"], timeout=40)
        return (True, "已放行（iptables，重启后需持久化）") if r.rc == 0 else (False, r.err or r.out)
    if b == "nft":
        return False, "nft 请手动添加规则（暂不支持自动）"
    ok, msg = install_ufw()
    if not ok:
        return False, f"无防火墙后端，安装失败：{msg}"
    return _ufw_rule("allow", port, proto, comment)


def deny_port(port: int, proto: str = "tcp") -> tuple[bool, str]:
    b = backend()
    if b == "ufw":
        return _ufw_rule("deny", port, proto)
    if b == "firewalld":
        r = as_root(["firewall-cmd", "--permanent", "--remove-port", f"{port}/{proto}"], timeout=40)
        as_root(["firewall-cmd", "--reload"], timeout=40)
        return (True, "已移除") if r.rc == 0 else (False, r.err or r.out)
    if b == "iptables":
        r = as_root(["iptables", "-D", "INPUT", "-p", proto, "--dport", str(port), "-j", "ACCEPT"], timeout=40)
        return (True, "已移除") if r.rc == 0 else (False, r.err or r.out)
    return False, "当前后端不支持"


def delete_rule(num: str) -> tuple[bool, str]:
    b = backend()
    if b == "ufw":
        r = as_root(["ufw", "--force", "delete", str(num)], timeout=40)
        return (True, "已删除") if r.rc == 0 else (False, (r.err or r.out)[-300:])
    if b == "iptables":
        r = as_root(["iptables", "-D", "INPUT", str(num)], timeout=40)
        return (True, "已删除") if r.rc == 0 else (False, r.err or r.out)
    return False, "当前后端不支持按编号删除"


def enable() -> tuple[bool, str]:
    b = backend()
    if b == "ufw":
        r = as_root(["ufw", "--force", "enable"], timeout=60)
        return (True, "已启用") if r.rc == 0 else (False, (r.err or r.out)[-300:])
    if b == "firewalld":
        r = as_root(["systemctl", "enable", "--now", "firewalld"], timeout=90)
        return (True, "已启用") if r.rc == 0 else (False, r.err or r.out)
    return False, "当前后端不支持一键启用"


def disable() -> tuple[bool, str]:
    b = backend()
    if b == "ufw":
        r = as_root(["ufw", "disable"], timeout=60)
        return (True, "已关闭") if r.rc == 0 else (False, (r.err or r.out)[-300:])
    if b == "firewalld":
        r = as_root(["systemctl", "disable", "--now", "firewalld"], timeout=90)
        return (True, "已关闭") if r.rc == 0 else (False, r.err or r.out)
    return False, "当前后端不支持"


def reset() -> tuple[bool, str]:
    if backend() != "ufw":
        return False, "仅 ufw 支持重置"
    r = as_root(["ufw", "--force", "reset"], timeout=60)
    return (True, "已重置") if r.rc == 0 else (False, r.err or r.out)


def mc_presets() -> dict:
    return {
        "java": {"port": 25565, "proto": "tcp", "desc": "Java 版默认端口"},
        "bedrock": {"port": 19132, "proto": "udp", "desc": "基岩版 IPv4"},
        "bedrock6": {"port": 19133, "proto": "udp", "desc": "基岩版 IPv6"},
        "rcon": {"port": 25575, "proto": "tcp", "desc": "RCON 远程控制台"},
        "query": {"port": 25565, "proto": "udp", "desc": "Query 查询（UDP）"},
        "panel": {"port": 8850, "proto": "tcp", "desc": "面板 Web 端口"},
    }


def open_mc_preset(key: str, port: int | None = None) -> tuple[bool, str]:
    p = mc_presets().get(key)
    if not p:
        return False, "未知预设"
    return allow_port(port or p["port"], p["proto"])


def listening_ports() -> list[dict]:
    """列出正在监听的端口（ss 优先，退回 netstat）。"""
    out = []
    if shutil.which("ss"):
        r = run(["ss", "-ltnup"], timeout=15)
        for line in (r.out or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) < 5:
                continue
            addr = parts[4]
            proto = parts[0]
            m = re.search(r":(\d+)$", addr)
            out.append({"proto": proto, "addr": addr, "port": int(m.group(1)) if m else None, "proc": parts[-1] if len(parts) > 5 else ""})
    elif shutil.which("netstat"):
        r = run(["netstat", "-ltnup"], timeout=15)
        for line in (r.out or "").splitlines()[2:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            m = re.search(r":(\d+)\s", line)
            out.append({"proto": parts[0], "addr": parts[3], "port": int(m.group(1)) if m else None, "proc": parts[-1]})
    return out
