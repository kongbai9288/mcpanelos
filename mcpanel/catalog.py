"""服务端版本清单：Vanilla / Paper / Purpur / Fabric / Forge / NeoForge / Bedrock。

所有接口都只依赖标准库；外部接口不可达时返回友好的错误信息。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from .util import cache_dir

UA = {"User-Agent": "mcpanel/1.0 (debian minecraft server launcher)"}
CACHE_TTL = 1800  # 30 分钟


def _get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(url: str, timeout: int = 25):
    return json.loads(_get(url, timeout).decode("utf-8", "ignore"))


def _cached(key: str, ttl: int = CACHE_TTL):
    f = cache_dir() / f"{key}.json"
    if f.exists() and time.time() - f.stat().st_mtime < ttl:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _save_cache(key: str, data):
    try:
        (cache_dir() / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _err(ex: Exception) -> dict:
    return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}


# ---------------------------------------------------------------- Vanilla


def vanilla_versions(force: bool = False) -> dict:
    key = "vanilla_versions"
    if not force:
        c = _cached(key)
        if c:
            return {"ok": True, "data": c, "cached": True}
    try:
        m = _get_json("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")
        rel = [v["id"] for v in m.get("versions", []) if v.get("type") == "release"]
        snap = [v["id"] for v in m.get("versions", []) if v.get("type") == "snapshot"]
        data = {"latest": m.get("latest", {}), "release": rel, "snapshot": snap[:30]}
        _save_cache(key, data)
        return {"ok": True, "data": data}
    except Exception as ex:
        c = _cached(key, ttl=86400 * 30)
        if c:
            return {"ok": True, "data": c, "cached": True, "warn": "网络不可用，使用缓存"}
        return _err(ex)


def vanilla_jar_url(version: str) -> dict:
    try:
        m = _get_json("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")
        url = None
        for v in m.get("versions", []):
            if v.get("id") == version:
                url = v.get("url")
                break
        if not url:
            return {"ok": False, "error": f"未找到版本 {version}"}
        meta = _get_json(url)
        d = meta.get("downloads", {}).get("server")
        if not d:
            return {"ok": False, "error": f"{version} 没有服务端下载"}
        return {
            "ok": True,
            "url": d["url"],
            "sha1": d.get("sha1"),
            "size": d.get("size"),
            "java": (meta.get("javaVersion") or {}).get("majorVersion"),
        }
    except Exception as ex:
        return _err(ex)


# ---------------------------------------------------------------- Paper


def paper_versions(force: bool = False) -> dict:
    key = "paper_versions"
    if not force:
        c = _cached(key)
        if c:
            return {"ok": True, "data": c, "cached": True}
    try:
        data = _get_json("https://fill.papermc.io/v3/projects/paper")
        vers = [v for v in data.get("versions", [])]
        vers.reverse()
        out = {"latest": vers[0] if vers else None, "versions": vers}
        _save_cache(key, out)
        return {"ok": True, "data": out}
    except Exception:
        try:
            data = _get_json("https://api.papermc.io/v2/projects/paper")
            vers = data.get("versions", [])
            vers.reverse()
            out = {"latest": vers[0] if vers else None, "versions": vers}
            _save_cache(key, out)
            return {"ok": True, "data": out, "warn": "使用 v2 备用接口"}
        except Exception as ex:
            c = _cached(key, ttl=86400 * 30)
            if c:
                return {"ok": True, "data": c, "cached": True, "warn": "网络不可用，使用缓存"}
            return _err(ex)


def paper_build(version: str) -> dict:
    """返回该版本最新稳定 build 的下载地址。"""
    try:
        data = _get_json(f"https://fill.papermc.io/v3/projects/paper/versions/{version}/builds")
        builds = data.get("builds", [])
        stable = [b for b in builds if b.get("channel") == "default"] or builds
        if not stable:
            return {"ok": False, "error": "没有可用 build"}
        b = stable[-1]
        dl = (b.get("downloads") or {}).get("server:default") or {}
        return {
            "ok": True,
            "build": b.get("id") or b.get("build"),
            "name": dl.get("name"),
            "url": f"https://fill.papermc.io/v3/projects/paper/versions/{version}/builds/{b.get('id') or b.get('build')}/downloads/{dl.get('name')}",
            "sha256": dl.get("sha256"),
            "java": 21,
        }
    except Exception:
        try:
            data = _get_json(f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds")
            builds = data.get("builds", [])
            stable = [b for b in builds if b.get("channel") == "default"] or builds
            if not stable:
                return {"ok": False, "error": "没有可用 build"}
            b = stable[-1]
            jar = b.get("downloads", {}).get("application", {}).get("name")
            return {
                "ok": True,
                "build": b.get("build"),
                "name": jar,
                "url": f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{b['build']}/downloads/{jar}",
                "java": 21,
            }
        except Exception as ex:
            return _err(ex)


# ---------------------------------------------------------------- Purpur


def purpur_versions(force: bool = False) -> dict:
    key = "purpur_versions"
    if not force:
        c = _cached(key)
        if c:
            return {"ok": True, "data": c, "cached": True}
    try:
        data = _get_json("https://api.purpurmc.org/v2/purpur")
        vers = list(data.get("versions", []))
        vers.reverse()
        out = {"latest": vers[0] if vers else None, "versions": vers}
        _save_cache(key, out)
        return {"ok": True, "data": out}
    except Exception as ex:
        c = _cached(key, ttl=86400 * 30)
        if c:
            return {"ok": True, "data": c, "cached": True, "warn": "网络不可用，使用缓存"}
        return _err(ex)


def purpur_build(version: str) -> dict:
    try:
        data = _get_json(f"https://api.purpurmc.org/v2/purpur/{version}")
        build = data.get("builds", {}).get("latest")
        return {
            "ok": True,
            "build": build,
            "name": f"purpur-{version}-{build}.jar",
            "url": f"https://api.purpurmc.org/v2/purpur/{version}/{build}/download",
            "java": 21,
        }
    except Exception as ex:
        return _err(ex)


# ---------------------------------------------------------------- Fabric


def fabric_installer_url() -> dict:
    try:
        xml = _get("https://maven.fabricmc.net/net/fabricmc/fabric-installer/maven-metadata.xml", timeout=25)
        root = ET.fromstring(xml)
        latest = root.findtext(".//versioning/release") or root.findtext(".//versioning/latest")
        if not latest:
            return {"ok": False, "error": "解析失败"}
        return {
            "ok": True,
            "version": latest,
            "url": f"https://maven.fabricmc.net/net/fabricmc/fabric-installer/{latest}/fabric-installer-{latest}.jar",
        }
    except Exception as ex:
        return _err(ex)


def fabric_game_versions() -> dict:
    key = "fabric_game"
    c = _cached(key)
    if c:
        return {"ok": True, "data": c, "cached": True}
    try:
        data = _get_json("https://meta.fabricmc.net/v2/versions/game")
        vers = [v.get("version") for v in data if v.get("stable")]
        out = {"versions": vers, "latest": vers[0] if vers else None}
        _save_cache(key, out)
        return {"ok": True, "data": out}
    except Exception as ex:
        return _err(ex)


# ---------------------------------------------------------------- Forge / NeoForge


def forge_versions() -> dict:
    key = "forge_promos"
    if not _cached:  # pragma: no cover
        pass
    c = _cached(key)
    if c:
        return {"ok": True, "data": c, "cached": True}
    try:
        data = _get_json("https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json")
        promos = data.get("promos", {})
        out = {}
        for k, v in promos.items():
            if k.endswith("-latest") or k.endswith("-recommended"):
                mc = k.rsplit("-", 1)[0]
                out.setdefault(mc, {})[k.rsplit("-", 1)[1]] = v
        _save_cache(key, out)
        return {"ok": True, "data": out}
    except Exception as ex:
        return _err(ex)


def forge_installer_url(mcversion: str, forgeversion: str | None = None) -> dict:
    try:
        if not forgeversion:
            r = forge_versions()
            if not r.get("ok"):
                return r
            entry = r["data"].get(mcversion) or {}
            forgeversion = entry.get("recommended") or entry.get("latest")
            if not forgeversion:
                return {"ok": False, "error": f"{mcversion} 没有可用的 Forge"}
        full = f"{mcversion}-{forgeversion}"
        return {
            "ok": True,
            "forge": forgeversion,
            "url": f"https://maven.minecraftforge.net/net/minecraftforge/forge/{full}/forge-{full}-installer.jar",
            "java": 17 if mcversion.split(".")[1] < "20" else 21,
        }
    except Exception as ex:
        return _err(ex)


def neoforge_installer_url(mcversion: str) -> dict:
    try:
        xml = _get("https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml", timeout=25)
        root = ET.fromstring(xml)
        vers = [v.text for v in root.findall(".//versioning/versions/version") if v.text]
        cand = [v for v in vers if v.startswith(mcversion.split(".")[1] if False else mcversion)]
        if not cand:
            cand = [v for v in vers if v.startswith(mcversion)]
        if not cand:
            return {"ok": False, "error": f"未找到 {mcversion} 的 NeoForge"}
        v = cand[-1]
        return {
            "ok": True,
            "version": v,
            "url": f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{v}/neoforge-{v}-installer.jar",
            "java": 21,
        }
    except Exception as ex:
        return _err(ex)


# ---------------------------------------------------------------- Bedrock

BEDROCK_PAGE = "https://www.minecraft.net/en-us/download/server/bedrock"
BEDROCK_RE = re.compile(r"https://minecraft\.azureedge\.net/bin-linux/bedrock-server-[\d.]+\.zip")


def bedrock_latest(force: bool = False) -> dict:
    key = "bedrock_latest"
    if not force:
        c = _cached(key)
        if c:
            return {"ok": True, "data": c, "cached": True}
    try:
        html = _get(BEDROCK_PAGE, timeout=25).decode("utf-8", "ignore")
        m = BEDROCK_RE.search(html)
        if not m:
            return {"ok": False, "error": "未能从官网解析下载地址（页面结构可能变化）"}
        url = m.group(0)
        ver = url.rsplit("bedrock-server-", 1)[-1].replace(".zip", "")
        data = {"url": url, "version": ver}
        _save_cache(key, data)
        return {"ok": True, "data": data}
    except Exception as ex:
        c = _cached(key, ttl=86400 * 30)
        if c:
            return {"ok": True, "data": c, "cached": True, "warn": "网络不可用，使用缓存"}
        return {
            "ok": False,
            "error": f"{type(ex).__name__}: {ex}；可手动填写下载地址：https://minecraft.net/download/server/bedrock",
        }


# ---------------------------------------------------------------- 汇总


def list_core(kind: str, force: bool = False) -> dict:
    kind = (kind or "").lower()
    if kind == "vanilla":
        return vanilla_versions(force)
    if kind == "paper":
        return paper_versions(force)
    if kind == "purpur":
        return purpur_versions(force)
    if kind == "fabric":
        return fabric_game_versions()
    if kind == "forge":
        return forge_versions()
    if kind == "bedrock":
        return bedrock_latest(force)
    return {"ok": False, "error": f"未知类型 {kind}"}


def resolve(kind: str, version: str) -> dict:
    """解析出某个核心 + 版本的下载地址与所需 Java 版本。"""
    kind = (kind or "").lower()
    if kind == "vanilla":
        return vanilla_jar_url(version)
    if kind == "paper":
        return paper_build(version)
    if kind == "purpur":
        return purpur_build(version)
    if kind == "fabric":
        ins = fabric_installer_url()
        if not ins.get("ok"):
            return ins
        return {"ok": True, "url": ins["url"], "installer": True, "kind": "fabric", "game": version, "java": 21}
    if kind == "forge":
        return forge_installer_url(version)
    if kind == "neoforge":
        return neoforge_installer_url(version)
    if kind == "bedrock":
        r = bedrock_latest()
        if r.get("ok"):
            r.update({"url": r["data"]["url"], "version": r["data"]["version"], "java": None})
        return r
    return {"ok": False, "error": f"未知类型 {kind}"}
