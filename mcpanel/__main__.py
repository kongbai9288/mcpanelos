"""命令行入口：python3 -m mcpanel <命令>

常用：
  python3 -m mcpanel serve          启动网页面板（后台常驻）
  python3 -m mcpanel desktop        打开桌面窗口
  python3 -m mcpanel wizard         小白向导：一步步建服
  python3 -m mcpanel deps           安装 Debian 依赖（自动提权）
  python3 -m mcpanel status         看状态
  python3 -m mcpanel pipeline x.yml 运行流水线
  python3 -m mcpanel nbt <file>     查看/修改 NBT
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 兼容三种运行方式：python3 -m mcpanel / python3 mcpanel/__main__.py / PyInstaller 单文件
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcpanel import __version__  # noqa: E402


def cmd_serve(args):
    from mcpanel import server, util

    if args.daemon:
        pid = os.fork()
        if pid > 0:
            print(f"面板已在后台运行，PID {pid}")
            return 0
        os.setsid()
    server.serve(port=args.port, bind=args.bind, open_browser=args.open)
    return 0


def cmd_desktop(args):
    from mcpanel import desktop

    desktop.main()
    return 0


def cmd_deps(args):
    from mcpanel.util import as_root, is_root

    pkgs = ["openjdk-21-jre-headless", "curl", "wget", "unzip", "tar", "ufw", "screen", "python3-tk", "jq"]
    if args.minimal:
        pkgs = ["openjdk-21-jre-headless", "curl", "unzip", "ufw"]
    print("将安装：", " ".join(pkgs))
    r = as_root(["sh", "-c", f"apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(pkgs)}"], timeout=1800)
    print(r.out[-2000:] if r.out else "")
    if r.rc != 0:
        print("安装失败：", r.err[-2000:])
        print("如果提示要密码，请先运行：python3 -m mcpanel sudo")
        return 1
    print("✅ 依赖安装完成")
    return 0


def cmd_sudo(args):
    from mcpanel import secret
    import getpass

    st = secret.sudoers_status()
    print(f"当前用户：{st['user']}  是否 root：{st['root']}  免密：{st['nopasswd']}")
    if st["root"]:
        print("已是 root，无需密码")
        return 0
    if st["nopasswd"]:
        print("sudo 免密可用")
        return 0
    print("1) 写入免密 sudoers（推荐，只放行白名单命令）")
    print("2) 保存 sudo 密码（加密存放，用于自动输入）")
    choice = input("选择 [1/2]：").strip() or "1"
    if choice == "1":
        ok, msg = secret.install_sudoers()
        print(("✅ " if ok else "❌ ") + msg)
        return 0 if ok else 1
    pw = getpass.getpass("sudo 密码：")
    ok, msg = secret.test_password(pw)
    if ok:
        secret.set_sudo_password(pw)
        print("✅ " + msg + "，已加密保存")
        return 0
    print("❌ " + msg)
    return 1


def cmd_status(args):
    from mcpanel import instance as inst, java as javamod, firewall, util, catalog

    print(f"MC 面板 v{__version__}  用户 {util.current_user()}  root={util.is_root()}")
    print(f"Java: {javamod.pick_java() or '未安装'}")
    print(f"防火墙: {firewall.backend()}")
    print("实例：")
    for m in inst.list_instances():
        print(f"  - {m['name']:<14} {m['kind']:<9} {str(m.get('version','')):<10} {'运行中' if m['running'] else '停止'}")
    return 0


def cmd_wizard(args):
    """小白向导：交互式建服。"""
    from mcpanel import instance as ins, catalog, java as javamod, firewall, autostart, util

    print("=== MC 开服向导 ===")
    kinds = ["paper", "vanilla", "purpur", "fabric", "forge", "bedrock"]
    print("可选服务端：" + " / ".join(kinds))
    kind = input("选一个（默认 paper）：").strip() or "paper"
    if kind not in kinds:
        print("不支持的类型")
        return 1
    if kind == "bedrock":
        version = input("版本号（留空取最新）：").strip() or ""
        port = int(input("端口（默认 19132）：").strip() or "19132")
        mem = "N/A"
    else:
        try:
            r = catalog.list_core(kind)
            arr = (r.get("data") or {}).get("versions") or []
            if arr:
                print("最新几个版本：" + ", ".join(arr[:8]))
        except Exception:
            pass
        version = input("版本号（留空取最新）：").strip() or ""
        port = int(input("端口（默认 25565）：").strip() or "25565")
        mem = input("内存（默认 2G）：").strip() or "2G"
    name = input("实例名（默认 survival）：").strip() or "survival"

    print("\n[1/5] 准备 Java…")
    if kind != "bedrock":
        j = javamod.ensure_java(21)
        print("  " + ("✅ " if j["ok"] else "❌ ") + str(j.get("java") or j.get("msg")))
    print("[2/5] 下载并安装服务端（几分钟）…")
    ok, msg, meta = ins.create_instance(name, kind, version, mem, None, port)
    print("  " + ("✅ " if ok else "❌ ") + msg)
    if not ok:
        return 1
    print("[3/5] 放行防火墙端口…")
    ok2, msg2 = firewall.allow_port(port, "udp" if kind == "bedrock" else "tcp")
    print("  " + ("✅ " if ok2 else "⚠️ ") + msg2)
    print("[4/5] 设置开机自启（systemd）…")
    ok3, msg3 = autostart.install(meta)
    print("  " + ("✅ " if ok3 else "⚠️ ") + msg3)
    yn = input("[5/5] 现在启动？[Y/n]：").strip().lower()
    if yn != "n":
        ok4, msg4 = ins.start(name)
        print("  " + ("✅ " if ok4 else "❌ ") + msg4)
    print("\n完成！控制面板：python3 -m mcpanel serve")
    print(f"游戏内连接地址：{util.local_ip()}:{port}")
    return 0


def cmd_pipeline(args):
    from mcpanel import pipeline

    p = Path(args.file)
    if not p.exists():
        print("文件不存在")
        return 1
    text = p.read_text(encoding="utf-8")
    kind = "script" if p.suffix == ".mcsh" else "yaml"
    r = pipeline.run_pipeline_text(text, {}, p.stem) if kind == "yaml" else pipeline.run_simple_script(text, {}, p.stem)
    for line in r.logs:
        print(line)
    print("状态：", r.status)
    return 0 if r.status == "ok" else 1


def cmd_nbt(args):
    from mcpanel import nbt

    p = Path(args.file)
    if not p.exists():
        print("文件不存在")
        return 1
    if args.set:
        expr, value = args.set.split("=", 1)
        ok, msg = nbt.set_path_file(p, expr, value)
        print(("✅ " if ok else "❌ ") + msg)
        return 0
    root = nbt.load(p)
    print(root.pretty()[:4000])
    return 0


def cmd_log(args):
    from mcpanel import loganalyzer

    r = loganalyzer.analyze(args.file, args.limit)
    if "error" in r:
        print(r["error"])
        return 1
    print(r["verdict"])
    for f in r["findings"]:
        print(f"  [{f['level']}] {f['label']} × {f['count']}")
        if f["advice"]:
            print("      💡 " + f["advice"])
        for s in f["samples"][:2]:
            print("      L%s: %s" % (s["line"], s["text"][:140]))
    if r["players"]:
        print("玩家：", ", ".join(f"{n}({c})" for n, c in r["players"]))
    return 0


def cmd_install_service(args):
    from mcpanel import autostart, util

    exe = args.exec or (sys.executable + " -m mcpanel")
    ok, msg = autostart.install_panel_service(args.port, exe)
    print(("✅ " if ok else "❌ ") + msg)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mcpanel", description="MC 服务器启动器面板（Debian）")
    ap.add_argument("-v", "--version", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="启动网页面板")
    p.add_argument("--port", type=int, default=8850)
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--open", action="store_true")
    p.add_argument("--daemon", action="store_true")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("desktop", help="打开桌面窗口")
    p.set_defaults(fn=cmd_desktop)

    p = sub.add_parser("deps", help="安装系统依赖")
    p.add_argument("--minimal", action="store_true")
    p.set_defaults(fn=cmd_deps)

    p = sub.add_parser("sudo", help="配置自动提权密码")
    p.set_defaults(fn=cmd_sudo)

    p = sub.add_parser("status", help="查看状态")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("wizard", help="交互式开服向导")
    p.set_defaults(fn=cmd_wizard)

    p = sub.add_parser("pipeline", help="运行流水线文件")
    p.add_argument("file")
    p.set_defaults(fn=cmd_pipeline)

    p = sub.add_parser("nbt", help="查看/修改 NBT")
    p.add_argument("file")
    p.add_argument("--set", help="如 Data.LevelName=新名字")
    p.set_defaults(fn=cmd_nbt)

    p = sub.add_parser("log", help="分析日志")
    p.add_argument("file")
    p.add_argument("--limit", type=int, default=3000)
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("service", help="把面板装成 systemd 服务")
    p.add_argument("--port", type=int, default=8850)
    p.add_argument("--exec")
    p.set_defaults(fn=cmd_install_service)

    args = ap.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
