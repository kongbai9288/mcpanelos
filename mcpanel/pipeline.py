"""像 CI 一样的命令解释器：YAML 流水线 + 简单命令脚本，两种都支持。

YAML 示例（pipeline.yml）:
    name: 每日维护
    on: manual | schedule | boot
    env:
      SERVER: survival
    steps:
      - name: 备份世界
        run: backup --instance ${SERVER}
      - name: 清理旧日志
        shell: sh
        run: find ${SERVER}/logs -name '*.log.gz' -mtime +7 -delete
      - name: 重启
        action: restart
        target: ${SERVER}
      - name: 通知
        if: ${STEP_PREV_OK}
        run: echo done

内置 action（无需 shell）：
    backup / start / stop / restart / clean / firewall / nbt / java / notify / sleep / shell

简单脚本（.mcsh）：一行一条命令，# 开头为注释，支持 @var=值 定义变量与 ${var} 展开。
"""

from __future__ import annotations

import os
import re
import shlex
import threading
import time
import uuid
from pathlib import Path

from .util import config, pipelines_dir, runs_dir, run, now_str, instance_dir


# ---------------------------------------------------------------- YAML 极简解析器


class MiniYAML:
    """够用的 YAML 子集解析：映射、列表、标量、块标量（| / >）。"""

    @staticmethod
    def parse(text: str):
        lines = text.replace("\r\n", "\n").split("\n")
        return MiniYAML._parse_block(lines, 0, 0)[0]

    @staticmethod
    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    @staticmethod
    def _strip_comment(line: str) -> str:
        out, quote = [], None
        for ch in line:
            if quote:
                out.append(ch)
                if ch == quote:
                    quote = None
            elif ch in ("'", '"'):
                quote = ch
                out.append(ch)
            elif ch == "#" and not out:
                break
            else:
                out.append(ch)
        return "".join(out).rstrip()

    @classmethod
    def _parse_block(cls, lines, i: int, indent: int):
        # 返回 (value, next_index)
        if i >= len(lines):
            return None, i
        first = lines[i]
        if cls._indent(first) < indent:
            return None, i
        if first.lstrip().startswith("- "):
            return cls._parse_list(lines, i, indent)
        return cls._parse_map(lines, i, indent)

    @classmethod
    def _parse_list(cls, lines, i: int, indent: int):
        items = []
        item_indent = None
        while i < len(lines):
            raw = lines[i]
            if not raw.strip() or raw.strip().startswith("#"):
                i += 1
                continue
            ind = cls._indent(raw)
            if ind < indent:
                break
            s = raw.strip()
            if not s.startswith("- "):
                break
            if item_indent is None:
                item_indent = ind
            elif ind != item_indent:
                break
            body = s[2:]
            i += 1
            if body.strip() == "":
                v, i = cls._parse_block(lines, i, item_indent + 1)
                items.append(v)
                continue
            # 单行 "- key: value" -> 视为映射起点
            if re.match(r"^[\w\-\.]+\s*:(\s|$)", body):
                sub_lines = [" " * (item_indent + 2) + body]
                while i < len(lines):
                    nxt = lines[i]
                    if not nxt.strip():
                        break
                    if cls._indent(nxt) <= item_indent:
                        break
                    sub_lines.append(nxt)
                    i += 1
                v, _ = cls._parse_block(sub_lines, 0, item_indent + 2)
                items.append(v)
                continue
            items.append(cls._scalar(body))
        return items, i

    @classmethod
    def _parse_map(cls, lines, i: int, indent: int):
        data = {}
        while i < len(lines):
            raw = lines[i]
            if not raw.strip() or raw.strip().startswith("#"):
                i += 1
                continue
            ind = cls._indent(raw)
            if ind < indent:
                break
            s = cls._strip_comment(raw).strip()
            m = re.match(r"^([\w\-\.]+)\s*:\s*(.*)$", s)
            if not m:
                i += 1
                continue
            key, val = m.group(1), m.group(2).strip()
            i += 1
            if val in ("|", "|-", ">", ">-"):
                block = []
                while i < len(lines):
                    nxt = lines[i]
                    if not nxt.strip():
                        block.append("")
                        i += 1
                        continue
                    if cls._indent(nxt) <= indent:
                        break
                    block.append(nxt[indent + 2 :] if len(nxt) > indent + 2 else nxt.strip())
                    i += 1
                data[key] = "\n".join(block).rstrip("\n") if val.startswith("|") else " ".join(x.strip() for x in block)
                continue
            if val == "":
                v, i = cls._parse_block(lines, i, indent + 1)
                data[key] = v if v is not None else {}
                continue
            data[key] = cls._scalar(val)
        return data, i

    @staticmethod
    def _scalar(s: str):
        s = s.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            return s[1:-1]
        if s in ("true", "True"):
            return True
        if s in ("false", "False"):
            return False
        if s in ("null", "~", ""):
            return None
        if re.match(r"^-?\d+$", s):
            return int(s)
        if re.match(r"^-?\d+\.\d+$", s):
            return float(s)
        return s


def load_yaml(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    data = MiniYAML.parse(text)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- 内置动作


def _inst(name: str):
    from . import instance as imod

    return imod


def action_backup(ctx: dict, target: str = "", **kw) -> tuple[bool, str]:
    from . import instance as imod

    ok, msg = imod.backup(target or ctx.get("env", {}).get("SERVER", ""))
    return ok, msg


def action_start(ctx, target="", **kw):
    from . import instance as imod

    return imod.start(target or ctx["env"].get("SERVER", ""))


def action_stop(ctx, target="", **kw):
    from . import instance as imod

    return imod.stop(target or ctx["env"].get("SERVER", ""))


def action_restart(ctx, target="", **kw):
    from . import instance as imod

    name = target or ctx["env"].get("SERVER", "")
    imod.stop(name)
    time.sleep(3)
    return imod.start(name)


def action_clean(ctx, target="", **kw):
    from . import cleaner

    deleted, freed, errors = cleaner.clean(kw.get("paths", []) or [])
    return True, f"删除 {deleted} 个文件，释放 {freed} 字节"


def action_firewall(ctx, target="", **kw):
    from . import firewall

    if kw.get("action") == "close":
        return firewall.deny_port(int(kw.get("port", 0)), kw.get("proto", "tcp"))
    return firewall.allow_port(int(kw.get("port", 0)), kw.get("proto", "tcp"))


def action_nbt(ctx, target="", **kw):
    from . import nbt

    p = kw.get("path") or target
    if not p:
        return False, "缺少 NBT 文件路径"
    ok, msg = nbt.set_path_file(p, kw.get("set", ""), kw.get("value", ""))
    return ok, msg


def action_sleep(ctx, target="", **kw):
    sec = float(target or kw.get("seconds", 1))
    time.sleep(sec)
    return True, f"等待 {sec}s"


def action_notify(ctx, target="", **kw):
    ctx.setdefault("notices", []).append(target or kw.get("text", ""))
    return True, target or ""


ACTIONS = {
    "backup": action_backup,
    "start": action_start,
    "stop": action_stop,
    "restart": action_restart,
    "clean": action_clean,
    "firewall": action_firewall,
    "nbt": action_nbt,
    "sleep": action_sleep,
    "wait": action_sleep,
    "notify": action_notify,
}


# ---------------------------------------------------------------- 变量与执行


VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def expand(text: str, env: dict) -> str:
    def sub(m):
        key = m.group(1) or m.group(2)
        return str(env.get(key, ""))

    return VAR_RE.sub(sub, text or "")


class Runner:
    def __init__(self, name: str = ""):
        self.id = uuid.uuid4().hex[:8]
        self.name = name
        self.logs: list[str] = []
        self.status = "idle"
        self.steps: list[dict] = []
        self.started = None
        self.finished = None

    def emit(self, text: str):
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        self.logs.append(line)
        if len(self.logs) > 5000:
            del self.logs[:1000]


RUNS: dict[str, Runner] = {}
_LOCK = threading.Lock()


def _record(r: Runner):
    with _LOCK:
        RUNS[r.id] = r
    try:
        (runs_dir() / f"{r.id}.json").write_text(
            __import__("json").dumps(
                {"id": r.id, "name": r.name, "status": r.status, "steps": r.steps, "logs": r.logs, "started": r.started, "finished": r.finished},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def run_pipeline_text(text: str, env_extra: dict | None = None, name: str = "") -> Runner:
    data = MiniYAML.parse(text)
    if not isinstance(data, dict):
        raise ValueError("流水线 YAML 解析失败")
    return _exec(data, env_extra or {}, name or data.get("name", "pipeline"))


def run_pipeline_file(path: str, env_extra: dict | None = None) -> Runner:
    return run_pipeline_text(Path(path).read_text(encoding="utf-8"), env_extra, Path(path).stem)


def run_simple_script(text: str, env_extra: dict | None = None, name: str = "script") -> Runner:
    """把简单命令脚本转成流水线执行。"""
    env = dict(env_extra or {})
    steps = []
    pending_shell = []
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("set ") or s.startswith("@"):
            body = s[1:].strip() if s.startswith("@") else s[4:].strip()
            if "=" in body:
                k, v = body.split("=", 1)
                env[k.strip()] = v.strip()
            continue
        m = re.match(r"^(backup|start|stop|restart|firewall|nbt|sleep|wait|notify)\s+(.*)$", s)
        if m and not s.startswith(("cd ", "ls", "echo", "find", "rm", "cp", "mv", "cat", "grep")):
            if pending_shell:
                steps.append({"name": "shell", "shell": "sh", "run": "\n".join(pending_shell)})
                pending_shell = []
            act, rest = m.group(1), m.group(2)
            step = {"name": f"{act} {rest}"[:40], "action": act}
            if act == "firewall":
                step["port"] = rest.split()[0] if rest.split() else ""
                step["proto"] = rest.split()[1] if len(rest.split()) > 1 else "tcp"
            elif act in ("sleep", "wait"):
                step["seconds"] = rest.split()[0] if rest.split() else "1"
            else:
                step["target"] = rest.strip()
            steps.append(step)
            continue
        pending_shell.append(s)
    if pending_shell:
        steps.append({"name": "shell", "shell": "sh", "run": "\n".join(pending_shell)})
    data = {"name": name, "env": env, "steps": steps}
    return _exec(data, {}, name)


def _exec(data: dict, env_extra: dict, name: str) -> Runner:
    env = {"HOME": os.environ.get("HOME", "/root"), "PANEL_HOME": str(Path.home())}
    env.update({k: str(v) for k, v in (data.get("env") or {}).items()})
    env.update({k: str(v) for k, v in env_extra.items()})
    r = Runner(name)
    r.status = "running"
    r.started = now_str()
    r.emit(f"▶ 开始执行流水线：{name}")
    ok_all = True
    prev_ok = True
    for idx, step in enumerate(data.get("steps") or [], 1):
        if not isinstance(step, dict):
            continue
        title = expand(str(step.get("name") or step.get("action") or step.get("run", "")[:30]), env)
        cond = step.get("if")
        if cond and str(expand(str(cond), env)).lower() not in ("1", "true", "yes"):
            r.steps.append({"n": idx, "name": title, "status": "skipped"})
            r.emit(f"  {idx}. ⏭ 跳过：{title}")
            continue
        r.emit(f"  {idx}. ▶ {title}")
        t0 = time.time()
        env["STEP_PREV_OK"] = "true" if prev_ok else "false"
        try:
            if step.get("action"):
                fn = ACTIONS.get(str(step["action"]).lower())
                if not fn:
                    raise RuntimeError(f"未知 action：{step['action']}")
                kw = {k: expand(str(v), env) if isinstance(v, str) else v for k, v in step.items() if k not in ("name", "action")}
                ok, msg = fn({"env": env}, **kw)
                out = msg
            else:
                script = expand(str(step.get("run", "")), env)
                shell = step.get("shell") or ("cmd" if os.name == "nt" else "sh")
                cwd = expand(str(step.get("cwd") or ""), env) or None
                res = run(script, cwd=cwd, shell=True, timeout=int(step.get("timeout", 600)))
                ok = res.rc == 0
                out = (res.out or "") + (("\n[stderr] " + res.err) if res.err else "")
            for line in str(out).splitlines()[-30:]:
                r.emit("     " + line)
        except Exception as ex:
            ok, out = False, f"{type(ex).__name__}: {ex}"
            r.emit("     " + out)
        dur = time.time() - t0
        status = "ok" if ok else "failed"
        r.steps.append({"n": idx, "name": title, "status": status, "duration": round(dur, 2)})
        r.emit(f"     ✓ 完成（{dur:.1f}s）" if ok else f"     ✗ 失败（{dur:.1f}s）")
        prev_ok = ok
        if not ok:
            ok_all = False
            if step.get("continue_on_error"):
                continue
            break
    r.status = "ok" if ok_all else "failed"
    r.finished = now_str()
    r.emit(f"■ 流水线结束：{r.status}")
    _record(r)
    return r


def run_async(text: str, kind: str = "yaml", env: dict | None = None, name: str = "") -> Runner:
    """后台执行，返回 Runner（可轮询）。"""
    holder = {}

    def _t():
        try:
            holder["r"] = run_pipeline_text(text, env, name) if kind == "yaml" else run_simple_script(text, env, name or "script")
        except Exception as ex:
            rr = Runner(name or "pipeline")
            rr.status = "failed"
            rr.emit(f"解析失败：{ex}")
            holder["r"] = rr

    th = threading.Thread(target=_t, daemon=True)
    th.start()
    # 立刻返回一个占位 runner，稍后由调用方按名字重新取
    ph = Runner(name)
    ph.status = "starting"
    ph.emit("流水线已提交，正在启动…")
    with _LOCK:
        RUNS[ph.id] = ph
    th.__dict__["_ph"] = ph
    return ph


def get_run(rid: str) -> dict | None:
    with _LOCK:
        r = RUNS.get(rid)
    if not r:
        f = runs_dir() / f"{rid}.json"
        if f.exists():
            try:
                return __import__("json").loads(f.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None
    return {"id": r.id, "name": r.name, "status": r.status, "steps": r.steps, "logs": r.logs, "started": r.started, "finished": r.finished}


def list_runs() -> list[dict]:
    with _LOCK:
        return [{"id": r.id, "name": r.name, "status": r.status, "started": r.started} for r in RUNS.values()]


# ---------------------------------------------------------------- 流水线仓库


def list_pipelines() -> list[dict]:
    out = []
    for f in sorted(pipelines_dir().glob("*.yml")) + sorted(pipelines_dir().glob("*.mcsh")):
        out.append({"name": f.stem, "file": f.name, "path": str(f), "kind": "yaml" if f.suffix == ".yml" else "script", "size": f.stat().st_size})
    return out


def save_pipeline(name: str, content: str, kind: str = "yaml") -> str:
    ext = ".yml" if kind == "yaml" else ".mcsh"
    p = pipelines_dir() / (name + ext)
    p.write_text(content, encoding="utf-8")
    return str(p)


def delete_pipeline(name: str) -> bool:
    for ext in (".yml", ".mcsh"):
        p = pipelines_dir() / (name + ext)
        if p.exists():
            p.unlink()
            return True
    return False


TEMPLATES = {
    "每日维护": """# 每天：备份 → 清旧日志 → 健康检查
name: 每日维护
on: schedule
env:
  SERVER: survival
  KEEP_DAYS: "7"
steps:
  - name: 备份世界
    action: backup
    target: ${SERVER}
  - name: 清理 ${KEEP_DAYS} 天前的日志
    shell: sh
    run: find ${SERVER}/logs -name "*.log.gz" -mtime +${KEEP_DAYS} -delete 2>/dev/null || true
    continue_on_error: true
  - name: 清理旧备份
    shell: sh
    run: find backups -name "*.tar.gz" -mtime +14 -delete 2>/dev/null || true
    continue_on_error: true
  - name: 完成提示
    action: notify
    text: 每日维护完成
""",
    "崩溃自动恢复": """# 检测到崩溃后：备份最近存档 → 重启 → 放行端口
name: 崩溃自动恢复
on: manual
env:
  SERVER: survival
  PORT: "25565"
steps:
  - name: 停止服务
    action: stop
    target: ${SERVER}
  - name: 备份存档
    action: backup
    target: ${SERVER}
  - name: 放行端口
    action: firewall
    port: ${PORT}
    proto: tcp
  - name: 启动服务
    action: start
    target: ${SERVER}
  - name: 等待就绪
    action: sleep
    seconds: "20"
""",
    "新服一键部署": """# 装 Java → 下载服务端 → 放行端口 → 设自启 → 启动
name: 新服一键部署
on: manual
env:
  SERVER: survival
  VERSION: "1.21.4"
  MEM: "4G"
steps:
  - name: 安装 Java 21
    shell: sh
    run: apt-get install -y openjdk-21-jre-headless
    continue_on_error: true
  - name: 放行 25565
    action: firewall
    port: "25565"
    proto: tcp
  - name: 同意 EULA
    shell: sh
    run: echo "eula=true" > ${SERVER}/eula.txt
  - name: 启动
    action: start
    target: ${SERVER}
""",
}

SIMPLE_TEMPLATE = """# 简单脚本示例：一行一条命令
@SERVER=survival
backup ${SERVER}
sleep 2
restart ${SERVER}
echo 全部完成
"""
