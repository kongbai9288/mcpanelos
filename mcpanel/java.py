"""Java 运行时检测与安装。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .util import cache_dir, run, which, as_root, is_root, Result

UA = {"User-Agent": "mcpanel/1.0 (+debian mc launcher)"}


def _http_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _http_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- 检测


def _version_from_output(text: str) -> int | None:
    m = re.search(r'version\s+"?(\d+)', text)
    if m:
        return int(m.group(1))
    m = re.search(r"openjdk\s+(\d+)", text)
    return int(m.group(1)) if m else None


def detect_java(bin_path: str | None = None) -> dict | None:
    """返回一个 Java 运行时信息 dict，或 None。"""
    candidates = []
    if bin_path:
        candidates.append(bin_path)
    else:
        for p in ("/usr/bin/java", "/bin/java", "/usr/lib/jvm/default-java/bin/java"):
            if os.path.exists(p):
                candidates.append(p)
        jvm = Path("/usr/lib/jvm")
        if jvm.exists():
            for d in sorted(jvm.iterdir()):
                b = d / "bin" / "java"
                if b.exists():
                    candidates.append(str(b))
        found = which("java")
        if found:
            candidates.append(found)
    for c in candidates:
        try:
            r = run([c, "-version"], timeout=15)
        except Exception:
            continue
        text = (r.out or "") + (r.err or "")
        if "version" in text.lower():
            major = _version_from_output(text)
            return {
                "path": c,
                "major": major,
                "raw": text.strip().splitlines()[0] if text.strip() else "",
                "vendor": "OpenJDK" if "openjdk" in text.lower() else ("Oracle" if "Java(TM)" in text else "未知"),
            }
    return None


def list_installed() -> list[dict]:
    out, seen = [], set()
    for info in [detect_java()]:
        if info and info["path"] not in seen:
            seen.add(info["path"])
            out.append(info)
    jvm = Path("/usr/lib/jvm")
    if jvm.exists():
        for d in sorted(jvm.iterdir()):
            b = d / "bin" / "java"
            if b.exists():
                info = detect_java(str(b))
                if info and info["path"] not in seen:
                    seen.add(info["path"])
                    out.append(info)
    for d in (Path.home() / ".mcpanel" / "jdk", Path("/opt/mcpanel/jdk")):
        if d.exists():
            for sub in sorted(d.iterdir()):
                b = sub / "bin" / "java"
                if b.exists():
                    info = detect_java(str(b))
                    if info and info["path"] not in seen:
                        seen.add(info["path"])
                        info["path"] = str(b)
                        out.append(info)
    return out


def pick_java(required_major: int | None = None, prefer: str | None = None) -> str | None:
    """挑选满足要求的 java 可执行文件路径。"""
    if prefer and os.path.exists(prefer):
        return prefer
    pool = list_installed()
    if not pool:
        return None
    if required_major:
        for j in pool:
            if j.get("major") == required_major:
                return j["path"]
        for j in pool:
            if (j.get("major") or 0) >= required_major:
                return j["path"]
    pool.sort(key=lambda x: (x.get("major") or 0), reverse=True)
    return pool[0]["path"]


# ---------------------------------------------------------------- 安装


def apt_install_jdk(major: int) -> tuple[bool, str]:
    pkg = f"openjdk-{major}-jre-headless"
    r = as_root(["sh", "-c", f"apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y {pkg}"], timeout=900)
    if r.rc == 0:
        return True, f"已安装 {pkg}"
    pkg2 = f"openjdk-{major}-jdk-headless"
    r2 = as_root(["sh", "-c", f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkg2}"], timeout=900)
    return (True, f"已安装 {pkg2}") if r2.rc == 0 else (False, (r.err or r.out)[-500:])


def adoptium_install_jdk(major: int) -> tuple[bool, str]:
    """从 Adoptium 下载 JRE 到 ~/.mcpanel/jdk。"""
    url = (
        f"https://api.adoptium.net/v3/binary/latest/{major}/ga/linux/x64/jre/hotspot/normal/eclipse"
    )
    dest_root = Path.home() / ".mcpanel" / "jdk"
    dest_root.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False, dir=str(cache_dir()))
    try:
        data = _http_bytes(url, timeout=300)
        tmp.write(data)
        tmp.close()
        with tarfile.open(tmp.name, "r:gz") as tf:
            root = tf.getnames()[0].split("/")[0]
            tf.extractall(str(dest_root))
        exe = dest_root / root / "bin" / "java"
        if exe.exists():
            os.chmod(exe, 0o755)
            return True, str(exe)
        return False, "解压后未找到 java"
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def ensure_java(required_major: int | None = None, auto_install: bool = True) -> dict:
    """确保存在可用 Java；必要时自动安装。"""
    cur = pick_java(required_major)
    if cur:
        return {"ok": True, "java": cur, "installed": False, "msg": "已存在可用 Java"}
    if not auto_install:
        return {"ok": False, "java": None, "installed": False, "msg": "未找到 Java 且未开启自动安装"}
    major = required_major or 21
    ok, msg = apt_install_jdk(major)
    if ok:
        cur = pick_java(required_major)
        if cur:
            return {"ok": True, "java": cur, "installed": True, "msg": msg}
    ok2, msg2 = adoptium_install_jdk(major)
    if ok2:
        return {"ok": True, "java": msg2, "installed": True, "msg": "已从 Adoptium 安装 JRE"}
    return {"ok": False, "java": None, "installed": False, "msg": f"apt: {msg} | adoptium: {msg2}"}
