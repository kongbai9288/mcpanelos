"""内置下载器：断点信息、进度回调、校验，以及 Fabric/Forge/Bedrock 的安装器执行。"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import catalog
from .util import cache_dir, run

UA = {"User-Agent": "mcpanel/1.0 (debian minecraft server launcher)"}


class DownloadManager:
    """管理下载任务（内存态，面板重启后清空）。"""

    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, url: str, dest: str | Path, sha1: str | None = None, sha256: str | None = None, label: str = "") -> str:
        tid = hashlib.md5(f"{url}{time.time()}".encode()).hexdigest()[:10]
        task = {
            "id": tid,
            "url": url,
            "dest": str(dest),
            "label": label or os.path.basename(str(dest)),
            "total": 0,
            "done": 0,
            "status": "running",
            "error": "",
            "sha1": sha1,
            "sha256": sha256,
            "started": time.time(),
            "speed": "",
        }
        with self._lock:
            self.tasks[tid] = task
        t = threading.Thread(target=self._worker, args=(tid,), daemon=True)
        t.start()
        return tid

    def _worker(self, tid: str):
        task = self.tasks[tid]
        dest = Path(task["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(task["url"], headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                task["total"] = int(r.headers.get("Content-Length") or 0)
                got = 0
                t0 = time.time()
                while True:
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    task["done"] = got
                    dt = time.time() - t0
                    if dt > 0.2:
                        task["speed"] = f"{got / dt / 1024 / 1024:.1f} MB/s"
            os.replace(tmp, dest)
            # 校验
            if task.get("sha1"):
                h = hashlib.sha1(dest.read_bytes()).hexdigest()
                if h.lower() != task["sha1"].lower():
                    task["status"] = "error"
                    task["error"] = f"SHA1 不一致：{h} != {task['sha1']}"
                    return
            if task.get("sha256"):
                h = hashlib.sha256(dest.read_bytes()).hexdigest()
                if h.lower() != task["sha256"].lower():
                    task["status"] = "error"
                    task["error"] = f"SHA256 不一致：{h} != {task['sha256']}"
                    return
            task["status"] = "done"
        except Exception as ex:
            task["status"] = "error"
            task["error"] = f"{type(ex).__name__}: {ex}"
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def get(self, tid: str) -> dict | None:
        with self._lock:
            return dict(self.tasks.get(tid, {})) if tid in self.tasks else None

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self.tasks.values()]

    def cancel(self, tid: str) -> bool:
        with self._lock:
            t = self.tasks.get(tid)
            if t and t["status"] == "running":
                t["status"] = "canceled"
                return True
        return False


DM = DownloadManager()


def download_sync(url: str, dest: str | Path, timeout: int = 600) -> tuple[bool, str]:
    """同步下载（用于安装流程）。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 262144)
        os.replace(tmp, dest)
        return True, str(dest)
    except Exception as ex:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False, f"{type(ex).__name__}: {ex}"


def _needs(tools: list[str]) -> list[str]:
    return [t for t in tools if not shutil.which(t)]


def install_fabric(cwd: Path, mcversion: str, java: str, loader: str | None = None) -> tuple[bool, str]:
    ins = catalog.fabric_installer_url()
    if not ins.get("ok"):
        return False, ins.get("error", "获取 Fabric 安装器失败")
    jar = cache_dir() / "fabric-installer.jar"
    ok, msg = download_sync(ins["url"], jar)
    if not ok:
        return False, f"下载安装器失败：{msg}"
    cmd = [java, "-jar", str(jar), "server", "-mcversion", mcversion, "-downloadMinecraft"]
    if loader:
        cmd += ["-loader", loader]
    r = run(cmd, cwd=str(cwd), timeout=900)
    if r.rc != 0:
        return False, (r.err or r.out)[-800:]
    # 产物：fabric-server-launch.jar 或 fabric-server-launcher.jar
    for cand in ("fabric-server-launch.jar", "fabric-server-launcher.jar", "server.jar"):
        if (cwd / cand).exists():
            return True, cand
    return False, "安装完成但没找到启动 jar（请查看目录）"


def install_forge(cwd: Path, mcversion: str, java: str, forgeversion: str | None = None) -> tuple[bool, str]:
    info = catalog.forge_installer_url(mcversion, forgeversion)
    if not info.get("ok"):
        return False, info.get("error", "获取 Forge 安装器失败")
    jar = cache_dir() / f"forge-installer-{mcversion}.jar"
    ok, msg = download_sync(info["url"], jar)
    if not ok:
        return False, f"下载安装器失败：{msg}"
    r = run([java, "-jar", str(jar), "--installServer"], cwd=str(cwd), timeout=1800)
    if r.rc != 0 and "already installed" not in (r.out or ""):
        return False, (r.err or r.out)[-800:]
    if (cwd / "run.sh").exists():
        os.chmod(cwd / "run.sh", 0o755)
        return True, "run.sh"
    for f in cwd.glob(f"forge-{mcversion}-*.jar"):
        return True, f.name
    return True, "run.sh"


def install_bedrock(cwd: Path, url: str | None = None, version: str | None = None) -> tuple[bool, str]:
    if not url:
        r = catalog.bedrock_latest()
        if not r.get("ok"):
            return False, r.get("error", "获取基岩版地址失败")
        url = r["data"]["url"]
    missing = _needs(["unzip"])
    if missing:
        return False, f"缺少命令 {','.join(missing)}，请先 apt install unzip"
    zpath = Path(cwd) / "bedrock-server.zip"
    ok, msg = download_sync(url, zpath)
    if not ok:
        return False, f"下载失败：{msg}"
    try:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(str(cwd))
    except Exception as ex:
        return False, f"解压失败：{ex}"
    try:
        zpath.unlink()
    except OSError:
        pass
    exe = Path(cwd) / "bedrock_server"
    if exe.exists():
        os.chmod(exe, 0o755)
        return True, "bedrock_server"
    return False, "解压后未找到 bedrock_server"


def install_core(cwd: Path, kind: str, version: str, java: str | None = None, extra: dict | None = None) -> tuple[bool, str]:
    """把服务端装到 cwd，返回 (ok, 启动文件名/说明)。"""
    extra = extra or {}
    cwd.mkdir(parents=True, exist_ok=True)
    kind = kind.lower()

    if kind == "bedrock":
        return install_bedrock(cwd, extra.get("url"), version)

    if kind == "fabric":
        return install_fabric(cwd, version, java or "java", extra.get("loader"))

    if kind in ("forge", "neoforge"):
        return install_forge(cwd, version, java or "java", extra.get("forge_version"))

    info = catalog.resolve(kind, version)
    if not info.get("ok"):
        return False, info.get("error", "解析下载地址失败")
    jar_name = extra.get("jar_name") or ("server.jar" if kind == "vanilla" else info.get("name") or "server.jar")
    dest = cwd / jar_name
    if dest.exists() and not extra.get("force"):
        # 备份旧 jar
        shutil.move(str(dest), str(dest.with_suffix(".jar.old")))
    ok, msg = download_sync(info["url"], dest, timeout=1800)
    if not ok:
        return False, f"下载失败：{msg}"
    return True, jar_name
