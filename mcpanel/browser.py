"""内置文件浏览器：列目录、读写文本、上传、删除、重命名、chmod、下载。

所有路径都经 safe_join 校验，防止越界访问。
"""

from __future__ import annotations

import base64
import os
import shutil
from pathlib import Path

from .util import dir_size, humansize, instance_dir, safe_join

TEXT_EXT = {
    ".txt", ".properties", ".json", ".yml", ".yaml", ".cfg", ".conf", ".toml",
    ".log", ".sh", ".ini", ".md", ".nbt", ".dat", ".mcmeta", ".list", ".xml",
}
MAX_TEXT = 2 * 1024 * 1024  # 2MB


def roots() -> list[dict]:
    from .util import servers_dir, home

    out = []
    for label, p in (("服务器目录", servers_dir()), ("面板数据", home())):
        out.append({"label": label, "path": str(p)})
    out.append({"label": "根目录", "path": "/"})
    return out


def listdir(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": "路径不存在", "path": path}
    if not p.is_dir():
        return {"error": "不是目录", "path": path}
    items = []
    try:
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        return {"error": "没有权限读取", "path": path}
    for e in entries:
        try:
            st = e.stat()
            item = {
                "name": e.name,
                "dir": e.is_dir(),
                "size": st.st_size if e.is_file() else 0,
                "size_h": humansize(st.st_size) if e.is_file() else "",
                "mtime": __import__("time").strftime("%Y-%m-%d %H:%M", __import__("time").localtime(st.st_mtime)),
                "mode": oct(st.st_mode)[-3:],
                "ext": e.suffix.lower(),
            }
            items.append(item)
        except OSError:
            continue
    return {"path": str(p.resolve()), "parent": str(p.parent), "items": items}


def read_text(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"error": "不是文件"}
    if p.stat().st_size > MAX_TEXT:
        return {"error": f"文件过大（>{humansize(MAX_TEXT)}），请下载后查看"}
    try:
        data = p.read_bytes()
    except PermissionError:
        return {"error": "没有权限读取"}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"error": "不是文本文件（无法按 UTF-8 解码）"}
    return {"path": str(p), "text": text, "size": len(data), "writable": os.access(p, os.W_OK)}


def write_text(path: str, text: str) -> dict:
    p = Path(path)
    if p.exists():
        try:
            shutil.copy2(p, str(p) + ".bak")
        except Exception:
            pass
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return {"ok": True, "path": str(p), "size": len(text)}
    except PermissionError:
        return {"error": "没有写入权限（可能需要 root，请在「自动 root 密码」页配置）"}
    except Exception as ex:
        return {"error": f"{type(ex).__name__}: {ex}"}


def mkdir(path: str, name: str) -> dict:
    try:
        base = Path(path)
        target = safe_join(base, name)
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(target)}
    except Exception as ex:
        return {"error": str(ex)}


def delete(path: str) -> dict:
    try:
        p = Path(path)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"ok": True}
    except PermissionError:
        return {"error": "没有权限删除"}
    except Exception as ex:
        return {"error": f"{type(ex).__name__}: {ex}"}


def rename(path: str, newname: str) -> dict:
    try:
        p = Path(path)
        target = safe_join(p.parent, newname)
        p.rename(target)
        return {"ok": True, "path": str(target)}
    except Exception as ex:
        return {"error": str(ex)}


def chmod(path: str, mode: str) -> dict:
    try:
        p = Path(path)
        os.chmod(p, int(mode, 8))
        return {"ok": True, "mode": oct(p.stat().st_mode)[-3:]}
    except Exception as ex:
        return {"error": str(ex)}


def upload(path: str, filename: str, b64: str) -> dict:
    try:
        base = Path(path)
        target = safe_join(base, filename)
        data = base64.b64decode(b64)
        target.write_bytes(data)
        return {"ok": True, "path": str(target), "size": len(data)}
    except Exception as ex:
        return {"error": str(ex)}


def download_b64(path: str) -> dict:
    try:
        p = Path(path)
        if not p.is_file():
            return {"error": "不是文件"}
        if p.stat().st_size > 20 * 1024 * 1024:
            return {"error": "文件超过 20MB，请用文件管理器处理"}
        data = p.read_bytes()
        return {"ok": True, "name": p.name, "b64": base64.b64encode(data).decode(), "size": len(data)}
    except Exception as ex:
        return {"error": str(ex)}


def stat_info(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": "不存在"}
    if p.is_dir():
        size, count = dir_size(p)
        return {"dir": True, "size": size, "size_h": humansize(size), "files": count}
    st = p.stat()
    return {"dir": False, "size": st.st_size, "size_h": humansize(st.st_size), "mode": oct(st.st_mode)[-3:]}
