"""联机 / 端口映射：UPnP 自动映射、Tailscale 组网、frp 穿透、局域网连接信息。

目标：让另一台电脑（或朋友）能连到这台物理机上的 MC 服务器，尽量零配置。
"""

from __future__ import annotations

import json
import re
import socket
import struct
import time
import urllib.request
import urllib.error
from pathlib import Path
from xml.etree import ElementTree as ET

from .util import as_root, humansize, local_ip, run

UA = {"User-Agent": "mcpanel/1.0"}


# ---------------------------------------------------------------- UPnP


def _ssdp_search(timeout: float = 3.0) -> list[str]:
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n"
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    try:
        sock.sendto(msg.encode(), ("239.255.255.250", 1900))
    except OSError:
        return []
    urls, t0 = [], time.time()
    while time.time() - t0 < timeout:
        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            break
        except OSError:
            break
        text = data.decode("utf-8", "ignore")
        for line in text.splitlines():
            if line.lower().startswith("location:"):
                u = line.split(":", 1)[1].strip()
                if u not in urls:
                    urls.append(u)
    sock.close()
    return urls


def _igd_control_url(location: str) -> str | None:
    try:
        req = urllib.request.Request(location, headers=UA)
        xml = urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        return None
    try:
        root = ET.fromstring(xml)
    except Exception:
        return None
    ns = "{urn:schemas-upnp-org:device-1-0}"
    for st in root.iter(f"{ns}serviceType"):
        if "WANIPConnection" in (st.text or "") or "WANPPPConnection" in (st.text or ""):
            parent = st.getparent() if hasattr(st, "getparent") else None
            if parent is not None:
                ctrl = parent.find(f"{ns}controlURL")
                if ctrl is not None:
                    return _join_url(location, ctrl.text)
    return None


def _join_url(base: str, rel: str | None) -> str:
    if not rel:
        return base
    if rel.startswith("http"):
        return rel
    proto, rest = base.split("://", 1)
    host = rest.split("/", 1)[0]
    return f"{proto}://{host}{rel if rel.startswith('/') else '/' + rel}"


def _soap(control: str, service: str, action: str, args: dict) -> tuple[bool, str]:
    body_args = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    envelope = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f"<s:Body><u:{action} xmlns:u=\"{service}\">{body_args}</u:{action}></s:Body></s:Envelope>"
    )
    data = envelope.encode("utf-8")
    req = urllib.request.Request(
        control,
        data=data,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{service}#{action}"',
            "Content-Length": str(len(data)),
        },
        method="POST",
    )
    try:
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as ex:
        return False, f"HTTP {ex.code}: " + (ex.read().decode("utf-8", "ignore")[:300])
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"
    if "errorDescription" in raw:
        m = re.search(r"<errorDescription>(.*?)</errorDescription>", raw, re.S)
        return False, m.group(1) if m else "路由器返回错误"
    return True, raw


def upnp_discover() -> dict:
    locs = _ssdp_search()
    if not locs:
        return {"ok": False, "error": "未发现支持 UPnP 的路由器（可能未开启 UPnP）"}
    ctrl = None
    for l in locs:
        ctrl = _igd_control_url(l)
        if ctrl:
            break
    if not ctrl:
        return {"ok": False, "error": "发现设备但无法解析控制地址"}
    return {"ok": True, "control": ctrl, "locations": locs}


def upnp_external_ip() -> dict:
    d = upnp_discover()
    if not d.get("ok"):
        return d
    svc = "urn:schemas-upnp-org:service:WANIPConnection:1"
    ok, msg = _soap(d["control"], svc, "GetExternalIPAddress", {})
    if not ok:
        svc = "urn:schemas-upnp-org:service:WANPPPConnection:1"
        ok, msg = _soap(d["control"], svc, "GetExternalIPAddress", {})
        if not ok:
            return {"ok": False, "error": msg}
    m = re.search(r"<NewExternalIPAddress>(.*?)</NewExternalIPAddress>", msg)
    return {"ok": True, "ip": m.group(1) if m else "未知"}


def upnp_add(port: int, proto: str = "TCP", desc: str = "MC Server", internal: str | None = None, lease: int = 0) -> tuple[bool, str]:
    d = upnp_discover()
    if not d.get("ok"):
        return False, d["error"]
    client = internal or local_ip()
    args = {
        "NewRemoteHost": "",
        "NewExternalPort": str(port),
        "NewProtocol": proto.upper(),
        "NewInternalPort": str(port),
        "NewInternalClient": client,
        "NewEnabled": "1",
        "NewPortMappingDescription": desc,
        "NewLeaseDuration": str(lease),
    }
    for svc in ("urn:schemas-upnp-org:service:WANIPConnection:1", "urn:schemas-upnp-org:service:WANPPPConnection:1"):
        ok, msg = _soap(d["control"], svc, "AddPortMapping", args)
        if ok:
            return True, f"已映射 {proto.upper()} {port} → {client}:{port}"
    return False, msg


def upnp_remove(port: int, proto: str = "TCP") -> tuple[bool, str]:
    d = upnp_discover()
    if not d.get("ok"):
        return False, d["error"]
    args = {"NewRemoteHost": "", "NewExternalPort": str(port), "NewProtocol": proto.upper()}
    for svc in ("urn:schemas-upnp-org:service:WANIPConnection:1", "urn:schemas-upnp-org:service:WANPPPConnection:1"):
        ok, msg = _soap(d["control"], svc, "DeletePortMapping", args)
        if ok:
            return True, f"已删除映射 {proto.upper()} {port}"
    return False, msg


def upnp_list() -> dict:
    """部分路由器不支持枚举，失败时给出提示即可。"""
    d = upnp_discover()
    if not d.get("ok"):
        return d
    items = []
    idx = 0
    svc = "urn:schemas-upnp-org:service:WANIPConnection:1"
    while idx < 64:
        ok, msg = _soap(d["control"], svc, "GetGenericPortMappingEntry", {"NewPortMappingIndex": str(idx)})
        if not ok:
            break
        ext = re.search(r"<NewExternalPort>(.*?)</NewExternalPort>", msg)
        proto = re.search(r"<NewProtocol>(.*?)</NewProtocol>", msg)
        cli = re.search(r"<NewInternalClient>(.*?)</NewInternalClient>", msg)
        desc = re.search(r"<NewPortMappingDescription>(.*?)</NewPortMappingDescription>", msg)
        if not ext:
            break
        items.append(
            {
                "external": ext.group(1) if ext else "",
                "proto": proto.group(1) if proto else "",
                "client": cli.group(1) if cli else "",
                "desc": desc.group(1) if desc else "",
            }
        )
        idx += 1
    return {"ok": True, "items": items}


# ---------------------------------------------------------------- Tailscale


def tailscale_install() -> tuple[bool, str]:
    r = as_root(
        ["sh", "-c", "curl -fsSL https://tailscale.com/install.sh | sh"],
        timeout=900,
    )
    return (True, "Tailscale 已安装") if r.rc == 0 else (False, (r.err or r.out)[-400:])


def tailscale_up(ssh: bool = False, advertise: str = "") -> tuple[bool, str]:
    cmd = ["tailscale", "up"]
    if ssh:
        cmd.append("--ssh")
    if advertise:
        cmd += ["--advertise-routes", advertise]
    r = as_root(cmd, timeout=180)
    out = (r.out or "") + (r.err or "")
    if r.rc == 0 or "Success" in out or "https://login.tailscale.com" in out:
        login = re.search(r"https://login\.tailscale\.com/\S+", out)
        return True, login.group(0) if login else "已启动（如需登录请运行 sudo tailscale up）"
    return False, out[-400:]


def tailscale_status() -> dict:
    r = run(["tailscale", "status", "--json"], timeout=20)
    if r.rc != 0:
        return {"installed": False, "raw": (r.err or r.out)[:400]}
    try:
        d = json.loads(r.out)
    except Exception:
        return {"installed": True, "raw": r.out[:400]}
    return {
        "installed": True,
        "backend": d.get("BackendState"),
        "self": (d.get("Self") or {}).get("TailscaleIPs"),
        "peers": [
            {"name": (v.get("HostName") or ""), "ip": (v.get("TailscaleIPs") or [None])[0], "online": v.get("Online")}
            for v in (d.get("Peer") or {}).values()
        ],
    }


# ---------------------------------------------------------------- frp 穿透


FRPC_TPL = """# mcpanel 生成的 frpc 配置
serverAddr = "{server}"
serverPort = {port}
{auth}

[[proxies]]
name = "{name}"
type = "{ptype}"
localIP = "127.0.0.1"
localPort = {local}
remotePort = {remote}
"""


def frp_config(server: str, port: int, local: int, remote: int, token: str = "", name: str = "mc", ptype: str = "tcp") -> str:
    auth = f'auth.token = "{token}"' if token else ""
    return FRPC_TPL.format(server=server, port=port, local=local, remote=remote, auth=auth, name=name, ptype=ptype)


def frp_install(cfg: str, dest: str = "/etc/frp/frpc.toml") -> tuple[bool, str]:
    Path("/tmp/frpc.toml").write_text(cfg, encoding="utf-8")
    r = as_root(["sh", "-c", f"mkdir -p /etc/frp && install -m 0600 /tmp/frpc.toml {dest}"], timeout=60)
    if r.rc != 0:
        return False, (r.err or r.out)[-300:]
    svc = f"""[Unit]
Description=frpc for Minecraft
After=network.target
[Service]
ExecStart=/usr/bin/frpc -c {dest}
Restart=always
[Install]
WantedBy=multi-user.target
"""
    Path("/tmp/frpc.service").write_text(svc, encoding="utf-8")
    r2 = as_root(["sh", "-c", "install -m 0644 /tmp/frpc.service /etc/systemd/system/frpc.service && systemctl daemon-reload && systemctl enable --now frpc.service"], timeout=120)
    return (True, f"配置已写入 {dest}，服务已启动") if r2.rc == 0 else (False, (r2.err or r2.out)[-300:])


def frp_install_binary() -> tuple[bool, str]:
    """下载 frpc 到 /usr/bin（需要网络与 root）。"""
    url = "https://github.com/fatedier/frp/releases/latest"
    return False, f"请手动下载 frpc 放到 /usr/bin：{url}"


# ---------------------------------------------------------------- 连接信息


def connection_info(instances: list[dict]) -> dict:
    lan = local_ip()
    ext = None
    try:
        req = urllib.request.Request("https://api.ipify.org?format=json", headers=UA)
        ext = json.loads(urllib.request.urlopen(req, timeout=6).read())["ip"]
    except Exception:
        pass
    ts = tailscale_status()
    ts_ip = (ts.get("self") or [None])[0] if ts.get("installed") else None
    out = []
    for m in instances:
        port = m.get("port")
        bedrock = m.get("bedrock") or m.get("kind") == "bedrock"
        out.append(
            {
                "name": m["name"],
                "kind": m.get("kind"),
                "bedrock": bedrock,
                "port": port,
                "proto": "UDP" if bedrock else "TCP",
                "lan": f"{lan}:{port}",
                "tailscale": f"{ts_ip}:{port}" if ts_ip else None,
                "public": f"{ext}:{port}" if ext else None,
                "running": m.get("running", False),
            }
        )
    return {"lan_ip": lan, "public_ip": ext, "tailscale_ip": ts_ip, "servers": out}


def port_check(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> dict:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return {"open": True, "host": host, "port": port}
    except Exception as ex:
        return {"open": False, "host": host, "port": port, "error": str(ex)}


def mc_status(host: str, port: int = 25565, timeout: float = 3.0) -> dict:
    """Java 版 Server List Ping（1.7+），拿到 MOTD/人数/版本。"""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        host_b = host.encode()
        handshake = b"\x00" + bytes([0xFE]) + bytes([len(host_b)]) + host_b + struct.pack(">HH", port & 0xFFFF, 1) + b"\x01"
        s.sendall(handshake)
        s.sendall(b"\x01\x00")
        data = s.recv(4096)
        s.close()
        text = data.decode("utf-16-be", "ignore")
        parts = text.split("\x00")
        if len(parts) >= 6:
            return {"ok": True, "motd": parts[3], "players": parts[4], "max": parts[5]}
        return {"ok": True, "raw": text}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def recommend() -> list[dict]:
    return [
        {"key": "lan", "title": "同一 Wi-Fi / 局域网（最简单）", "desc": "另一台电脑在游戏里直接填 局域网IP:端口，无需任何配置。", "steps": ["确认两台机器在同一网段", "服务器上放行端口（防火墙页）", "客户端填 IP:端口"]},
        {"key": "upnp", "title": "UPnP 自动映射（家里路由器）", "desc": "路由器支持 UPnP 时，点一下就能把端口映射到公网。", "steps": ["路由器后台开启 UPnP", "面板「联机」页点自动映射", "用公网IP:端口连接"]},
        {"key": "tailscale", "title": "Tailscale 虚拟组网（跨网络、最稳）", "desc": "两台机器装 Tailscale 登录同一账号，直接用虚拟 IP 连。", "steps": ["面板一键安装 Tailscale", "sudo tailscale up 登录", "另一台电脑也装并登录", "用 Tailscale IP 连接"]},
        {"key": "frp", "title": "frp 内网穿透（有云服务器时）", "desc": "需要一台有公网 IP 的服务器跑 frps。", "steps": ["云服务器装 frps", "面板填服务器地址与 token", "生成配置并启动 frpc"]},
        {"key": "playit", "title": "playit.gg 免费穿透", "desc": "免公网 IP，装个客户端即可。", "steps": ["apt 安装或下载 playit", "注册并绑定隧道", "用分配的域名连接"]},
    ]
