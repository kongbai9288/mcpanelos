"""NBT 读写与修改（纯标准库实现，支持 gzip / zlib / 未压缩）。

可用于修改 level.dat（种子、难度、游戏规则）、playerdata、结构文件等。
"""

from __future__ import annotations

import gzip
import io
import json
import struct
import zlib
from pathlib import Path

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

TYPE_NAME = {
    0: "end", 1: "byte", 2: "short", 3: "int", 4: "long", 5: "float",
    6: "double", 7: "byte_array", 8: "string", 9: "list", 10: "compound",
    11: "int_array", 12: "long_array",
}

_READ = {
    1: ("b", 1), 2: ("h", 2), 3: ("i", 4), 4: ("q", 8),
    5: ("f", 4), 6: ("d", 8),
}
_PACK = {v[0]: k for k, v in _READ.items()}


class NBTError(Exception):
    pass


class Reader:
    def __init__(self, data: bytes):
        self.b = io.BytesIO(data)

    def read(self, n: int) -> bytes:
        d = self.b.read(n)
        if len(d) != n:
            raise NBTError("数据不足（文件可能损坏）")
        return d

    def u1(self):
        return self.read(1)[0]

    def num(self, fmt: str, size: int):
        return struct.unpack(">" + fmt, self.read(size))[0]

    def string(self) -> str:
        n = self.num("H", 2)
        return self.read(n).decode("utf-8", "replace")


def _guess_and_decompress(raw: bytes) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if raw[:2] in (b"\x78\x9c", b"\x78\x01", b"\x78\xda"):
        return zlib.decompress(raw)
    return raw


class Writer:
    def __init__(self):
        self.b = io.BytesIO()

    def num(self, fmt: str, v):
        self.b.write(struct.pack(">" + fmt, v))

    def string(self, s: str):
        raw = s.encode("utf-8")
        self.num("H", len(raw))
        self.b.write(raw)

    def value(self):
        return self.b.getvalue()


# ---------------------------------------------------------------- 数据结构


class Tag:
    def __init__(self, type_id: int, value=None, name: str = ""):
        self.type = type_id
        self.value = value
        self.name = name

    # ---- 便捷访问
    def __getitem__(self, key):
        if self.type == TAG_COMPOUND:
            return self.value[key]
        if self.type == TAG_LIST and isinstance(key, int):
            return self.value[key]
        raise KeyError(key)

    def __setitem__(self, key, val):
        if self.type == TAG_COMPOUND:
            self.value[key] = val
        elif self.type == TAG_LIST and isinstance(key, int):
            self.value[key] = val
        else:
            raise KeyError(key)

    def get(self, key, default=None):
        if self.type == TAG_COMPOUND:
            return self.value.get(key, default)
        return default

    def keys(self):
        return list(self.value.keys()) if self.type == TAG_COMPOUND else []

    def to_py(self):
        if self.type == TAG_COMPOUND:
            return {k: v.to_py() for k, v in self.value.items()}
        if self.type == TAG_LIST:
            return [v.to_py() for v in self.value]
        if self.type in (TAG_BYTE_ARRAY, TAG_INT_ARRAY, TAG_LONG_ARRAY):
            return {"__type": TYPE_NAME[self.type], "__len": len(self.value)}
        return self.value

    def pretty(self, indent: int = 0) -> str:
        pad = "  " * indent
        t = TYPE_NAME[self.type]
        if self.type == TAG_COMPOUND:
            s = f"{pad}{self.name or '<root>'} : compound ({len(self.value)} 项)\n"
            for k, v in self.value.items():
                s += v.pretty(indent + 1)
            return s
        if self.type == TAG_LIST:
            s = f"{pad}{self.name} : list ({len(self.value)} 项)\n"
            for i, v in enumerate(self.value[:50]):
                v2 = Tag(v.type, v.value, f"[{i}]")
                s += v2.pretty(indent + 1)
            return s
        if self.type in (TAG_BYTE_ARRAY, TAG_INT_ARRAY, TAG_LONG_ARRAY):
            return f"{pad}{self.name} : {t}[{len(self.value)}]\n"
        return f"{pad}{self.name} : {t} = {self.value!r}\n"


def _read_payload(r: Reader, tid: int):
    if tid == TAG_BYTE:
        return r.num("b", 1)
    if tid == TAG_SHORT:
        return r.num("h", 2)
    if tid == TAG_INT:
        return r.num("i", 4)
    if tid == TAG_LONG:
        return r.num("q", 8)
    if tid == TAG_FLOAT:
        return r.num("f", 4)
    if tid == TAG_DOUBLE:
        return r.num("d", 8)
    if tid == TAG_BYTE_ARRAY:
        n = r.num("i", 4)
        return list(r.read(n))
    if tid == TAG_STRING:
        return r.string()
    if tid == TAG_LIST:
        et = r.u1()
        n = r.num("i", 4)
        items = []
        for _ in range(n):
            items.append(Tag(et, _read_payload(r, et)))
        return items
    if tid == TAG_COMPOUND:
        d = {}
        while True:
            ct = r.u1()
            if ct == TAG_END:
                break
            nm = r.string()
            d[nm] = Tag(ct, _read_payload(r, ct), nm)
        return d
    if tid == TAG_INT_ARRAY:
        n = r.num("i", 4)
        return list(struct.unpack(f">{n}i", r.read(4 * n)))
    if tid == TAG_LONG_ARRAY:
        n = r.num("i", 4)
        return list(struct.unpack(f">{n}q", r.read(8 * n)))
    raise NBTError(f"未知标签类型 {tid}")


def _write_payload(w: Writer, tag: Tag):
    t = tag.type
    if t in _READ:
        w.num(_READ[t][0], tag.value)
    elif t == TAG_BYTE_ARRAY:
        w.num("i", len(tag.value))
        w.b.write(bytes(tag.value))
    elif t == TAG_STRING:
        w.string(tag.value)
    elif t == TAG_LIST:
        et = tag.value[0].type if tag.value else TAG_END
        w.b.write(bytes([et]))
        w.num("i", len(tag.value))
        for item in tag.value:
            _write_payload(w, item)
    elif t == TAG_COMPOUND:
        for k, v in tag.value.items():
            w.b.write(bytes([v.type]))
            w.string(k)
            _write_payload(w, v)
        w.b.write(b"\x00")
    elif t == TAG_INT_ARRAY:
        w.num("i", len(tag.value))
        w.b.write(struct.pack(f">{len(tag.value)}i", *tag.value))
    elif t == TAG_LONG_ARRAY:
        w.num("i", len(tag.value))
        w.b.write(struct.pack(f">{len(tag.value)}q", *tag.value))
    else:
        raise NBTError(f"不支持写入类型 {t}")


def load(path: str | Path) -> Tag:
    raw = Path(path).read_bytes()
    return loads(raw)


def loads(raw: bytes) -> Tag:
    body = _guess_and_decompress(raw)
    r = Reader(body)
    tid = r.u1()
    if tid == TAG_END:
        return Tag(TAG_COMPOUND, {})
    name = r.string()
    val = _read_payload(r, tid)
    return Tag(tid, val, name)


def dumps(root: Tag, compress: str = "gzip") -> bytes:
    w = Writer()
    w.b.write(bytes([root.type]))
    w.string(root.name)
    _write_payload(w, root)
    body = w.value()
    if compress == "gzip":
        return gzip.compress(body, 6)
    if compress == "zlib":
        return zlib.compress(body, 6)
    return body


def save(root: Tag, path: str | Path, compress: str = "gzip") -> bool:
    p = Path(path)
    if p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        try:
            bak.write_bytes(p.read_bytes())
        except OSError:
            pass
    p.write_bytes(dumps(root, compress))
    return True


# ---------------------------------------------------------------- 路径与编辑


def _split_path(expr: str) -> list[str]:
    return [x for x in expr.replace("/", ".").split(".") if x != ""]


def get_path(root: Tag, expr: str):
    cur = root
    for part in _split_path(expr):
        if cur.type == TAG_COMPOUND:
            cur = cur.value[part]
        elif cur.type == TAG_LIST:
            cur = cur.value[int(part)]
        else:
            raise NBTError(f"路径 {expr} 无法在 {TYPE_NAME[cur.type]} 上继续")
    return cur


def coerce(type_id: int, raw: str):
    if type_id == TAG_STRING:
        return raw
    if type_id == TAG_BYTE:
        return int(raw) & 0xFF if raw.lower() != "true" else 1
    if type_id in (TAG_SHORT, TAG_INT, TAG_LONG):
        return int(raw)
    if type_id in (TAG_FLOAT, TAG_DOUBLE):
        return float(raw)
    if type_id == TAG_BYTE_ARRAY:
        return [int(x) for x in raw.replace("[", "").replace("]", "").split(",") if x.strip()]
    raise NBTError(f"暂不支持设置类型 {TYPE_NAME[type_id]}")


def set_path(root: Tag, expr: str, value) -> Tag:
    """按路径设置值，保持原有类型（自动转换）。"""
    parts = _split_path(expr)
    if not parts:
        raise NBTError("空路径")
    cur = root
    for part in parts[:-1]:
        if cur.type == TAG_COMPOUND:
            cur = cur.value[part]
        elif cur.type == TAG_LIST:
            cur = cur.value[int(part)]
        else:
            raise NBTError("路径中段类型不支持")
    last = parts[-1]
    if cur.type == TAG_COMPOUND:
        old = cur.value.get(last)
        if old is None:
            cur.value[last] = Tag(TAG_STRING, str(value), last)
        else:
            if isinstance(value, str) and old.type != TAG_STRING:
                cur.value[last].value = coerce(old.type, value)
            else:
                cur.value[last].value = value
        return cur.value[last]
    if cur.type == TAG_LIST:
        idx = int(last)
        cur.value[idx].value = coerce(cur.value[idx].type, value) if isinstance(value, str) else value
        return cur.value[idx]
    raise NBTError("目标容器类型不支持写入")


def set_path_file(path: str | Path, expr: str, value) -> tuple[bool, str]:
    """直接对文件按路径写值（流水线/面板用）。"""
    root = load(path)
    tag = set_path(root, expr, value)
    save(root, path)
    return True, f"{expr} = {tag.value}"


# ---------------------------------------------------------------- 常用预设


def level_dat_info(path: str | Path) -> dict:
    root = load(path)
    data = root.value.get("Data", root)
    out = {}
    for key in (
        "LevelName", "Difficulty", "DifficultyLocked", "hardcore", "initialized",
        "SpawnX", "SpawnY", "SpawnZ", "Time", "DayTime", "raining", "thundering",
        "GameType", "MapFeatures", "allowCommands", "DataVersion", "Version",
    ):
        v = data.value.get(key)
        out[key] = v.value if v is not None else None
    seed = data.value.get("WorldGenSettings")
    if seed and seed.value.get("seed"):
        out["seed"] = seed.value["seed"].value
    if data.value.get("RandomSeed") is not None:
        out["seed"] = data.value["RandomSeed"].value
    return out


GAMERULES = [
    "doFireTick", "doDaylightCycle", "doMobSpawning", "keepInventory",
    "mobGriefing", "naturalRegeneration", "doWeatherCycle", "commandBlockOutput",
    "randomTickSpeed", "doImmediateRespawn", "showDeathMessages", "announceAdvancements",
    "disableRaids", "doInsomnia", "doPatrolSpawning", "doTraderSpawning",
    "forgiveDeadPlayers", "universalAnger", "maxEntityCramming", "playersSleepingPercentage",
]


def list_gamerules(path: str | Path) -> dict:
    root = load(path)
    data = root.value.get("Data", root)
    gr = data.value.get("GameRules")
    out = {}
    if gr:
        for k, v in gr.value.items():
            out[k] = v.value
    return out


def set_gamerule(path: str | Path, rule: str, value: str) -> tuple[bool, str]:
    root = load(path)
    data = root.value.get("Data", root)
    gr = data.value.get("GameRules")
    if gr is None:
        gr = Tag(TAG_COMPOUND, {}, "GameRules")
        data.value["GameRules"] = gr
    old = gr.value.get(rule)
    if old is not None:
        old.value = coerce(old.type, value)
    elif value.lower() in ("true", "false"):
        gr.value[rule] = Tag(TAG_STRING, value.lower(), rule)
    else:
        gr.value[rule] = Tag(TAG_STRING, value, rule)
    save(root, path)
    return True, f"{rule} = {gr.value[rule].value}"


def set_seed(path: str | Path, seed) -> tuple[bool, str]:
    root = load(path)
    data = root.value.get("Data", root)
    if isinstance(seed, str) and not seed.lstrip("-").isdigit():
        h = 0
        for ch in seed:
            h = (31 * h + ord(ch)) & 0xFFFFFFFFFFFFFFFF
        seed = h - (1 << 64) if h >= (1 << 63) else h
    seed = int(seed)
    if data.value.get("RandomSeed") is not None:
        data.value["RandomSeed"].value = seed
    wgs = data.value.get("WorldGenSettings")
    if wgs and wgs.value.get("seed") is not None:
        wgs.value["seed"].value = seed
    save(root, path)
    return True, f"seed = {seed}"
