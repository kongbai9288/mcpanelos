"""零依赖 HTTP 服务：JSON API + 静态前端。

安全要点：
- 默认只在本机/局域网提供，开启密码后所有写操作需带 token；
- 文件浏览器所有路径经 safe_join 校验，禁止越界；
- 提权动作（防火墙/systemd/apt）走 as_root，密码保存在本地加密文件，不落日志。
"""

from __future__ import annotations

import base64
import cgi
import hashlib
import html
import http.server
import json
import os
import socketserver
import ssl
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from . import (
    APP,
    __version__,
    autostart,
    browser,
    catalog,
    cleaner,
    firewall,
    instance as inst,
    java as javamod,
    loganalyzer,
    nbt,
    net,
    pipeline,
    secret,
    util,
)
from .downloader import DM

WEBUI_DIR = Path(__file__).resolve().parent.parent / "webui"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}


def _json(handler, data, code=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _ok(handler, data=None):
    _json(handler, {"ok": True, "data": data or {}})


def _fail(handler, msg, code=400):
    _json(handler, {"ok": False, "error": str(msg)}, code)


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = f"{APP}/{__version__}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------ 基础
    def log_message(self, fmt, *args):
        try:
            msg = fmt % args
        except Exception:
            msg = str(args)
        try:
            (util.panel_log_dir() / "access.log").open("a", encoding="utf-8").write(
                f"{util.now_str()} {self.address_string()} {msg}\n"
            )
        except Exception:
            pass

    def _send_file(self, path: Path):
        data = path.read_bytes()
        ctype = MIME.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            return self._multipart(raw, ctype)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            q = urllib.parse.parse_qs(raw.decode("utf-8", "ignore"))
            return {k: v[0] for k, v in q.items()}

    def _multipart(self, raw: bytes, ctype: str):
        out = {}
        try:
            boundary = ctype.split("boundary=")[1].strip().strip('"').encode()
            parts = raw.split(b"--" + boundary)
            for part in parts:
                if b"Content-Disposition" not in part:
                    continue
                head, _, body = part.partition(b"\r\n\r\n")
                head_s = head.decode("utf-8", "ignore")
                name = None
                fname = None
                for seg in head_s.split(";"):
                    seg = seg.strip()
                    if seg.startswith('name='):
                        name = seg.split("=", 1)[1].strip('"')
                    elif seg.startswith("filename="):
                        fname = seg.split("=", 1)[1].strip('"')
                body = body.rstrip(b"\r\n-")
                if fname:
                    out["filename"] = fname
                    out["b64"] = base64.b64encode(body).decode()
                elif name:
                    out[name] = body.decode("utf-8", "ignore")
        except Exception as ex:
            out["_error"] = str(ex)
        return out

    def _auth_ok(self) -> bool:
        cfg = util.config()
        if not cfg.get("auth_enabled"):
            return True
        token = self.headers.get("X-Token") or ""
        cookie = self.headers.get("Cookie") or ""
        if "token=" in cookie:
            token = token or cookie.split("token=")[1].split(";")[0]
        want = cfg.get("auth_session") or ""
        return bool(want) and token == want

    # ------------------------------------------------ GET
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if path.startswith("/api/"):
            return self.api_get(path[5:], q)

        if path == "/" or path == "":
            idx = WEBUI_DIR / "index.html"
            if idx.exists():
                return self._send_file(idx)
            return _fail(self, "前端文件缺失", 500)
        rel = path.lstrip("/")
        if rel.startswith("api/"):
            return _fail(self, "not found", 404)
        f = (WEBUI_DIR / rel).resolve()
        try:
            if str(f).startswith(str(WEBUI_DIR.resolve())) and f.is_file():
                return self._send_file(f)
        except Exception:
            pass
        return _fail(self, "not found", 404)

    def api_get(self, route: str, q: dict):
        g = lambda k, d=None: (q.get(k, [d])[0] if k in q else d)
        try:
            if route == "state":
                return _ok(self, self.state())
            if route == "system/info":
                return _ok(self, system_info())
            if route == "instances":
                return _ok(self, {"items": inst.list_instances()})
            if route.startswith("instances/") and route.endswith("/status"):
                name = route[len("instances/") : -len("/status")]
                return _ok(self, inst.status(urllib.parse.unquote(name)))
            if route.startswith("instances/") and route.endswith("/console"):
                name = urllib.parse.unquote(route[len("instances/") : -len("/console")])
                return _ok(self, {"lines": inst.console_tail(name, int(g("n", 300))), "running": inst.is_running(name)})
            if route == "catalog/kinds":
                return _ok(self, {"items": KINDS})
            if route.startswith("catalog/"):
                kind = route[len("catalog/") :]
                return _json(self, catalog.list_core(kind, g("force") == "1"))
            if route == "java/list":
                return _ok(self, {"items": javamod.list_installed(), "current": javamod.pick_java()})
            if route == "firewall/status":
                return _ok(self, firewall.status())
            if route == "firewall/ports":
                return _ok(self, {"items": firewall.listening_ports(), "presets": firewall.mc_presets()})
            if route == "cleaner/scan":
                return _ok(self, {"items": cleaner.targets(g("instance")), "cache": cleaner.panel_cache()})
            if route == "cleaner/disk":
                return _ok(self, {"items": cleaner.disk_usage()})
            if route.startswith("cleaner/instance/"):
                return _ok(self, cleaner.instance_disk(urllib.parse.unquote(route[len("cleaner/instance/") :])))
            if route == "logs/list":
                return _ok(self, {"items": loganalyzer.instance_logs(g("instance", ""))})
            if route == "logs/analyze":
                return _json(self, loganalyzer.analyze(g("path", ""), int(g("limit", 3000))))
            if route == "browser/list":
                return _json(self, browser.listdir(g("path", "/")))
            if route == "browser/roots":
                return _ok(self, {"items": browser.roots()})
            if route == "browser/read":
                return _json(self, browser.read_text(g("path", "")))
            if route == "browser/download":
                return _json(self, browser.download_b64(g("path", "")))
            if route == "browser/stat":
                return _json(self, browser.stat_info(g("path", "")))
            if route == "pipeline/list":
                return _ok(self, {"items": pipeline.list_pipelines(), "templates": pipeline.TEMPLATES, "simple": pipeline.SIMPLE_TEMPLATE})
            if route == "pipeline/get":
                p = Path(g("path", ""))
                if not p.is_file():
                    return _fail(self, "文件不存在")
                return _ok(self, {"content": p.read_text(encoding="utf-8")})
            if route == "pipeline/status":
                return _json(self, pipeline.get_run(g("id", "")) or {"error": "没有该运行记录"})
            if route == "pipeline/runs":
                return _ok(self, {"items": pipeline.list_runs()})
            if route == "dl/list":
                return _ok(self, {"items": DM.list()})
            if route == "dl/status":
                return _json(self, DM.get(g("id", "")) or {"error": "没有该任务"})
            if route == "secret/status":
                return _ok(self, secret.sudoers_status())
            if route == "net/info":
                from . import instance as _im

                return _ok(self, net.connection_info(_im.list_instances()))
            if route == "net/upnp/list":
                return _json(self, net.upnp_list())
            if route == "net/upnp/ip":
                return _json(self, net.upnp_external_ip())
            if route == "net/recommend":
                return _ok(self, {"items": net.recommend()})
            if route == "net/tailscale":
                return _ok(self, net.tailscale_status())
            if route == "net/portcheck":
                return _ok(self, net.port_check(int(g("port", 25565)), g("host", "127.0.0.1")))
            if route == "net/mcstatus":
                return _ok(self, net.mc_status(g("host", "127.0.0.1"), int(g("port", 25565))))
            if route == "autostart/env":
                return _ok(self, autostart.environment())
            if route.startswith("autostart/status/"):
                return _ok(self, autostart.svc_status(urllib.parse.unquote(route[len("autostart/status/") :])))
            if route == "nbt/info":
                return _json(self, nbt.level_dat_info(g("path", "")))
            if route == "nbt/gamerules":
                return _json(self, {"items": nbt.list_gamerules(g("path", ""))})
            if route == "nbt/read":
                p = Path(g("path", ""))
                if not p.is_file():
                    return _fail(self, "文件不存在")
                try:
                    root = nbt.load(p)
                except Exception as ex:
                    return _fail(self, f"解析失败：{ex}")
                return _ok(self, {"tree": root.pretty(), "json": root.to_py(), "name": root.name})
            if route == "nbt/list":
                base = Path(g("path", ""))
                items = []
                if base.is_dir():
                    for f in sorted(base.rglob("*")):
                        if f.is_file() and f.suffix in (".dat", ".nbt"):
                            items.append({"path": str(f), "name": f.name, "size": f.stat().st_size})
                return _ok(self, {"items": items[:200]})
            if route == "backups":
                return _ok(self, {"items": inst.list_backups(g("instance"))})
            if route == "logout":
                self.send_response(204)
                self.end_headers()
                return
        except Exception as ex:
            return _fail(self, f"{type(ex).__name__}: {ex}", 500)
        return _fail(self, f"未知接口 {route}", 404)

    def state(self):
        cfg = util.config()
        items = inst.list_instances()
        return {
            "app": APP,
            "version": __version__,
            "user": util.current_user(),
            "root": util.is_root(),
            "ip": util.local_ip(),
            "port": cfg.get("port"),
            "instances": items,
            "firewall": firewall.backend(),
            "java": javamod.pick_java(),
            "auth_enabled": cfg.get("auth_enabled", False),
            "systemd": autostart.environment()["systemd"],
        }

    # ------------------------------------------------ POST
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            return _fail(self, "not found", 404)
        route = path[5:]
        data = self._body()
        f = lambda k, d=None: data.get(k, d)

        if route == "auth/login":
            cfg = util.config()
            pw = f("password", "")
            h = hashlib.sha256(pw.encode()).hexdigest()
            if h == cfg.get("auth_password_hash"):
                tok = hashlib.sha256((pw + APP + str(time.time())).encode()).hexdigest()
                cfg.set("auth_session", tok)
                return _ok(self, {"token": tok})
            return _fail(self, "密码错误", 401)

        if not self._auth_ok():
            return _fail(self, "未授权（请先在「设置」开启并登录，或关闭密码）", 401)

        try:
            # ---- 实例
            if route == "instances/create":
                ok, msg, meta = inst.create_instance(
                    f("name", ""),
                    f("kind", "paper"),
                    f("version", ""),
                    f("memory", "2G"),
                    f("java") or None,
                    int(f("port", 25565) or 25565),
                    f("extra_flags", ""),
                    bool(f("eula", True)),
                    bool(f("bedrock", False)),
                    bool(f("install", True)),
                )
                return _ok(self, {"msg": msg, "meta": meta}) if ok else _fail(self, msg)

            for act in ("start", "stop", "restart", "kill"):
                if route == f"instances/{act}":
                    name = f("name", "")
                    if act == "restart":
                        inst.stop(name)
                        time.sleep(2)
                        fn = inst.start
                    else:
                        fn = {"start": inst.start, "stop": inst.stop, "kill": inst.kill}[act]
                    ok, msg = fn(name)
                    return _ok(self, {"msg": msg}) if ok else _fail(self, msg)

            if route == "instances/send":
                ok, msg = inst.send_command(f("name", ""), f("cmd", ""))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)

            if route == "instances/backup":
                ok, msg = inst.backup(f("name", ""), f("dir") or None)
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)

            if route == "instances/delete":
                ok, msg = inst.remove_instance(f("name", ""), bool(f("files", False)))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)

            if route == "instances/update":
                name = f("name", "")
                meta = inst.get_instance(name)
                if not meta:
                    return _fail(self, "实例不存在")
                for k in ("memory", "extra_flags", "java", "jar", "port", "auto_restart", "autostart", "version", "kind"):
                    if k in data:
                        meta[k] = data[k]
                inst.save_instance(meta)
                return _ok(self, {"msg": "已保存"})

            # ---- Java
            if route == "java/ensure":
                r = javamod.ensure_java(int(f("major") or 0) or None, bool(f("install", True)))
                return _json(self, r)

            # ---- 下载器
            if route == "dl/start":
                tid = DM.start(f("url", ""), f("dest", ""), f("sha1") or None, f("sha256") or None, f("label", ""))
                return _ok(self, {"id": tid})
            if route == "dl/cancel":
                return _ok(self, {"ok": DM.cancel(f("id", ""))})

            # ---- 防火墙
            if route == "firewall/allow":
                ok, msg = firewall.allow_port(int(f("port")), f("proto", "tcp"))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "firewall/deny":
                ok, msg = firewall.deny_port(int(f("port")), f("proto", "tcp"))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "firewall/delete":
                ok, msg = firewall.delete_rule(str(f("num", "")))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route in ("firewall/enable", "firewall/disable"):
                fn = firewall.enable if route.endswith("enable") else firewall.disable
                ok, msg = fn()
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "firewall/install":
                ok, msg = firewall.install_ufw()
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)

            # ---- 自启动
            if route == "autostart/install":
                meta = inst.get_instance(f("name", ""))
                if not meta:
                    return _fail(self, "实例不存在")
                ok, msg = autostart.install(meta, f("user") or None)
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "autostart/uninstall":
                ok, msg = autostart.uninstall(f("name", ""))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "autostart/action":
                ok, msg = autostart.svc_action(f("name", ""), f("action", "status"))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "autostart/cron":
                meta = inst.get_instance(f("name", ""))
                if not meta:
                    return _fail(self, "实例不存在")
                ok, msg = autostart.install_cron(meta)
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "autostart/panel":
                ok, msg = autostart.install_panel_service(int(f("port", 8850)), f("exec", sys.executable + " -m mcpanel"))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)

            # ---- 清理
            if route == "cleaner/clean":
                deleted, freed, errors = cleaner.clean(list(f("paths", []) or []))
                return _ok(self, {"deleted": deleted, "freed": freed, "errors": errors})
            if route == "cleaner/apt":
                ok, msg = cleaner.apt_clean()
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "cleaner/journal":
                ok, msg = cleaner.journal_clean(f("keep", "300M"))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)

            # ---- NBT
            if route == "nbt/write":
                p = Path(f("path", ""))
                if not p.is_file():
                    return _fail(self, "文件不存在")
                try:
                    root = nbt.load(p)
                    nbt.set_path(root, f("expr", ""), f("value", ""))
                    nbt.save(root, p, f("compress", "gzip"))
                    return _ok(self, {"msg": "已写入"})
                except Exception as ex:
                    return _fail(self, f"{type(ex).__name__}: {ex}")
            if route == "nbt/gamerule":
                ok, msg = nbt.set_gamerule(f("path", ""), f("rule", ""), f("value", ""))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "nbt/seed":
                ok, msg = nbt.set_seed(f("path", ""), f("seed", 0))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "nbt/instance-level":
                name = f("name", "")
                meta = inst.get_instance(name)
                if not meta:
                    return _fail(self, "实例不存在")
                d = Path(meta["dir"])
                cands = []
                for w in ("world", "world_nether", "world_the_end", "Bedrock level"):
                    if (d / w / "level.dat").exists():
                        cands.append({"label": w, "path": str(d / w / "level.dat")})
                return _ok(self, {"items": cands})

            # ---- 文件浏览器
            if route == "browser/write":
                return _json(self, browser.write_text(f("path", ""), f("text", "")))
            if route == "browser/mkdir":
                return _json(self, browser.mkdir(f("path", ""), f("name", "")))
            if route == "browser/delete":
                return _json(self, browser.delete(f("path", "")))
            if route == "browser/rename":
                return _json(self, browser.rename(f("path", ""), f("newname", "")))
            if route == "browser/chmod":
                return _json(self, browser.chmod(f("path", ""), str(f("mode", "644"))))
            if route == "browser/upload":
                return _json(self, browser.upload(f("path", ""), f("filename", "upload.bin"), f("b64", "")))

            # ---- 流水线
            if route == "pipeline/save":
                p = pipeline.save_pipeline(f("name", "pipeline"), f("content", ""), f("kind", "yaml"))
                return _ok(self, {"path": p})
            if route == "pipeline/delete":
                return _ok(self, {"ok": pipeline.delete_pipeline(f("name", ""))})
            if route == "pipeline/run":
                content = f("content", "")
                kind = f("kind", "yaml")
                env = f("env") or {}
                name = f("name", "") or "临时流水线"
                try:
                    r = pipeline.run_pipeline_text(content, env, name) if kind == "yaml" else pipeline.run_simple_script(content, env, name)
                except Exception as ex:
                    return _fail(self, f"执行失败：{ex}")
                return _ok(self, {"id": r.id, "status": r.status, "steps": r.steps, "logs": r.logs[-200:]})
            if route == "pipeline/runfile":
                try:
                    r = pipeline.run_pipeline_file(f("path", ""), f("env") or {})
                except Exception as ex:
                    return _fail(self, f"执行失败：{ex}")
                return _ok(self, {"id": r.id, "status": r.status, "steps": r.steps, "logs": r.logs[-200:]})

            # ---- 密码 / sudoers
            if route == "secret/set":
                pw = f("password", "")
                if not pw:
                    return _fail(self, "密码为空")
                ok, msg = secret.test_password(pw)
                if not ok:
                    return _fail(self, msg)
                secret.set_sudo_password(pw)
                return _ok(self, {"msg": "密码已验证并加密保存"})
            if route == "secret/test":
                ok, msg = secret.test_password(f("password", ""))
                return _ok(self, {"ok": ok, "msg": msg})
            if route == "secret/clear":
                secret.clear_password()
                return _ok(self, {"msg": "已清除"})
            if route == "secret/sudoers":
                ok, msg = secret.install_sudoers(f("user") or None)
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "secret/sudoers/remove":
                ok, msg = secret.remove_sudoers()
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)

            # ---- 联机 / 端口映射
            if route == "net/upnp/add":
                ok, msg = net.upnp_add(int(f("port")), f("proto", "TCP"), f("desc", "MC Server"))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "net/upnp/remove":
                ok, msg = net.upnp_remove(int(f("port")), f("proto", "TCP"))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "net/frp":
                cfg = net.frp_config(f("server", ""), int(f("sport", 7000)), int(f("local", 25565)), int(f("remote", 25565)), f("token", ""), f("name", "mc"), f("ptype", "tcp"))
                ok, msg = net.frp_install(cfg)
                return _ok(self, {"msg": msg, "config": cfg}) if ok else _fail(self, msg)
            if route == "net/frp/preview":
                return _ok(self, {"config": net.frp_config(f("server", ""), int(f("sport", 7000)), int(f("local", 25565)), int(f("remote", 25565)), f("token", ""), f("name", "mc"), f("ptype", "tcp"))})
            if route == "net/tailscale/install":
                ok, msg = net.tailscale_install()
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)
            if route == "net/tailscale/up":
                ok, msg = net.tailscale_up(bool(f("ssh", False)), f("routes", ""))
                return _ok(self, {"msg": msg}) if ok else _fail(self, msg)

            # ---- 设置
            if route == "settings":
                cfg = util.config()
                if "port" in data:
                    cfg.set("port", int(data["port"]))
                if "auth_enabled" in data:
                    cfg.set("auth_enabled", bool(data["auth_enabled"]))
                if "auth_password" in data:
                    pw = str(data["auth_password"])
                    cfg.set("auth_password_hash", hashlib.sha256(pw.encode()).hexdigest())
                    cfg.set("auth_enabled", True)
                if "java_auto_install" in data:
                    cfg.set("java_auto_install", bool(data["java_auto_install"]))
                if "cleanup" in data:
                    cfg.set("cleanup", data["cleanup"])
                return _ok(self, {"msg": "已保存"})
        except Exception as ex:
            return _fail(self, f"{type(ex).__name__}: {ex}", 500)

        return _fail(self, f"未知接口 {route}", 404)


KINDS = [
    {"key": "vanilla", "label": "官方原版 Vanilla", "java": True},
    {"key": "paper", "label": "Paper（推荐·插件服）", "java": True},
    {"key": "purpur", "label": "Purpur（Paper 增强）", "java": True},
    {"key": "fabric", "label": "Fabric（轻量模组）", "java": True},
    {"key": "forge", "label": "Forge（大型模组）", "java": True},
    {"key": "neoforge", "label": "NeoForge", "java": True},
    {"key": "bedrock", "label": "基岩版 Bedrock", "java": False},
]


def system_info() -> dict:
    import platform

    un = os.uname() if hasattr(os, "uname") else None
    mem = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:") or line.startswith("MemAvailable:"):
                    k, v = line.split(":")
                    mem[k.strip()] = int(v.split()[0]) * 1024
    except Exception:
        pass
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    return {
        "os": platform.platform(),
        "kernel": un.release if un else "",
        "arch": un.machine if un else "",
        "python": sys.version.split()[0],
        "cpu": os.cpu_count(),
        "mem_total": util.humansize(mem.get("MemTotal", 0)),
        "mem_avail": util.humansize(mem.get("MemAvailable", 0)),
        "load": [round(x, 2) for x in load],
        "uptime": uptime_str(),
        "ip": util.local_ip(),
    }


def uptime_str() -> str:
    try:
        with open("/proc/uptime") as f:
            sec = float(f.read().split()[0])
        h, m = int(sec // 3600), int(sec % 3600 // 60)
        return f"{h} 小时 {m} 分"
    except Exception:
        return "-"


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port: int = 8850, bind: str = "0.0.0.0", open_browser: bool = False, quiet: bool = False):
    util.config().set("port", port)
    srv = ThreadedHTTPServer((bind, port), Handler)
    url = f"http://{'127.0.0.1' if bind in ('0.0.0.0', '::') else bind}:{port}/"
    if not quiet:
        print(f"{APP} {__version__} 已启动：{url}")
        print(f"局域网地址：http://{util.local_ip()}:{port}/")
        print("按 Ctrl+C 停止")
    if open_browser:
        try:
            import webbrowser

            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return srv
