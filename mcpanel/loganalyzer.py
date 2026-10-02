"""日志分析：错误/崩溃/TPS/玩家/作弊/性能的规则化提取，输出可读报告。"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .util import instance_dir, tail_lines

# ---- 规则：每条 (级别, 名称, 正则)
RULES = [
    ("critical", "崩溃 / 异常退出", re.compile(r"Encountered an unexpected exception|Crash report saved to|This crash report has been saved"), "崩溃后优先看崩溃报告与最近加载的插件/模组"),
    ("critical", "Log4j 相关告警", re.compile(r"log4j|Log4Shell|JNDI lookup", re.I), "若服务端版本较老，请升级到已修复版本（1.18.2+/Paper 新构建）"),
    ("error", "Java 异常堆栈", re.compile(r"Exception in thread|java\.lang\.|java\.io\.|java\.util\.concurrent\."), "堆栈第一行通常指明出错的类"),
    ("error", "插件/模组加载失败", re.compile(r"(?i)(could not load|failed to (load|enable)|Error occurred while enabling|Mod resolution failed|requires fabric-api)"), "检查插件与服务端版本是否匹配、依赖是否缺（如 Fabric API）"),
    ("error", "Watchdog 卡死", re.compile(r"(?i)A single server tick took|watchdog|Server thread dump| --- Snapshot"), "单 tick 超过 60s，通常是实体过多或插件死循环"),
    ("warn", "内存不足", re.compile(r"(?i)OutOfMemoryError|Java heap space|GC overhead limit"), "加大 -Xmx 或安装优化模组（Lithium/FerriteCore）"),
    ("warn", "内存占用偏高", re.compile(r"(?i)Can't keep up! Is the server overloaded\?"), "降低 view-distance / simulation-distance，或减少实体"),
    ("warn", "区块/实体过载", re.compile(r"(?i)Can't keep up|Server thread.*overloaded|Too many entities"), "清理实体、限制刷怪、降低视距"),
    ("warn", "端口/网络问题", re.compile(r"(?i)address already in use|Failed to bind to port|connection reset|timed out"), "检查端口占用与防火墙放行"),
    ("warn", "世界/存档问题", re.compile(r"(?i)corrupt|failed to read chunk|recovering|Region file.*error"), "恢复最近一次备份，检查磁盘健康"),
    ("warn", "EULA 未同意", re.compile(r"(?i)You need to agree to the EULA"), "把 eula.txt 设为 eula=true"),
    ("warn", "RCON/权限告警", re.compile(r"(?i)rcon|not whitelisted|Failed to verify"), "确认 RCON 密码与白名单设置"),
    ("info", "玩家加入", re.compile(r"(?i)(joined the game|logged in with entity id|\[Async Chat Thread|UUID of player)"), ""),
    ("info", "玩家退出", re.compile(r"(?i)(left the game|lost connection|Disconnecting)"), ""),
    ("info", "服务器就绪", re.compile(r'(?i)Done \(.*\)! For help, type "help"'), ""),
    ("info", "聊天记录", re.compile(r"(?i)<[^>]+>|\bchat\b.*: "), ""),
]

PLAYER_RE = re.compile(r"(?i)(?:UUID of player )([A-Za-z0-9_]{3,16})|([A-Za-z0-9_]{3,16}) (?:joined|left) the game|lost connection:? (?:Disconnected|)? ?([A-Za-z0-9_]{3,16})")
CHAT_RE = re.compile(r"\[(\d{2}:\d{2}:\d{2})\].*?(?:\[Async Chat Thread\]/INFO|<)?\s*<([^>]{1,16})>\s*(.*)")


def instance_logs(name: str) -> list[dict]:
    d = instance_dir(name)
    cands = []
    for rel in ("logs/latest.log", "logs", "console.log", "."):
        p = d / rel
        if p.is_file():
            cands.append({"name": rel, "path": str(p), "size": p.stat().st_size})
        elif p.is_dir():
            for f in sorted(p.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
                cands.append({"name": f.relative_to(d).as_posix(), "path": str(f), "size": f.stat().st_size})
    return cands


def _read_lines(path: Path, limit: int) -> list[str]:
    return tail_lines(path, limit)


def analyze(path: str, limit: int = 3000) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"error": "文件不存在"}
    lines = _read_lines(p, limit)
    counts = Counter()
    samples: dict[str, list[dict]] = {}
    for i, line in enumerate(lines):
        for level, label, rx, advice in RULES:
            if rx.search(line):
                counts[label] += 1
                if len(samples.setdefault(label, [])) < 5:
                    samples[label].append({"line": i + 1, "text": line.strip()[:400]})
                break

    players = Counter()
    chat = []
    for line in lines:
        m = PLAYER_RE.search(line)
        if m:
            who = next((g for g in m.groups() if g), None)
            if who:
                players[who] += 1
        m2 = CHAT_RE.search(line)
        if m2 and len(chat) < 200:
            chat.append({"time": m2.group(1), "who": m2.group(2), "text": m2.group(3)[:200]})

    tps_hints = [l.strip() for l in lines if re.search(r"(?i)Can't keep up", l)][:5]

    findings = []
    for level, label, rx, advice in RULES:
        if counts.get(label):
            findings.append(
                {
                    "level": level,
                    "label": label,
                    "count": counts[label],
                    "samples": samples.get(label, []),
                    "advice": advice,
                }
            )
    order = {"critical": 0, "error": 1, "warn": 2, "info": 3}
    findings.sort(key=lambda x: (order.get(x["level"], 9), -x["count"]))

    summary = {
        "file": str(p),
        "lines": len(lines),
        "total_findings": sum(counts.values()),
        "critical": sum(v for k, v in counts.items() if any(k == r[1] and r[0] == "critical" for r in RULES)),
        "error": sum(v for k, v in counts.items() if any(k == r[1] and r[0] == "error" for r in RULES)),
        "warn": sum(v for k, v in counts.items() if any(k == r[1] and r[0] == "warn" for r in RULES)),
    }

    verdict = "🟢 未发现明显异常"
    if summary["critical"]:
        verdict = "🔴 存在致命问题，建议立即处理（优先看崩溃报告）"
    elif summary["error"]:
        verdict = "🟠 存在错误，多半是插件/模组或配置不匹配"
    elif summary["warn"]:
        verdict = "🟡 有告警，性能或配置可优化"

    return {
        "summary": summary,
        "verdict": verdict,
        "findings": findings,
        "players": players.most_common(30),
        "chat": chat[-100:],
        "tail": lines[-200:],
    }


def quick_health(name: str) -> dict:
    """面板首页用的快速健康度：读 lastest.log 或 console.log 尾部。"""
    from . import instance as inst

    meta = inst.get_instance(name)
    if not meta:
        return {"error": "实例不存在"}
    d = instance_dir(name)
    cands = [d / "logs" / "latest.log", d / "console.log"]
    for c in cands:
        if c.is_file():
            r = analyze(str(c), limit=800)
            if "error" not in r:
                return r
    return {"summary": {"lines": 0}, "verdict": "暂无日志", "findings": [], "players": [], "chat": [], "tail": []}
