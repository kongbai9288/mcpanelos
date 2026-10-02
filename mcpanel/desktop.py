"""桌面原生窗口（Tkinter，标准库自带，Debian 需 apt install python3-tk）。

功能：实例启停 / 控制台 / 一键下载 / 防火墙放行 / NBT 修改 / 流水线运行。
与 Web 面板共用同一套后端模块，数据互通。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    _TkBase = tk.Tk
    HAS_TK = True
except Exception:  # 没装 python3-tk 时不拖垮其它功能
    tk = None
    ttk = None
    _TkBase = object
    HAS_TK = False

from . import (
    autostart,
    catalog,
    cleaner,
    firewall,
    instance as inst,
    java as javamod,
    nbt,
    pipeline,
    secret,
    util,
)
from . import __version__, APP


class App(_TkBase):
    def __init__(self):
        super().__init__()
        self.title(f"MC 服务器面板 {__version__}")
        self.geometry("1060x700")
        self.minsize(900, 620)
        self.cur = None
        self._build()
        self.after(300, self.refresh)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------ UI
    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="刷新", command=self.refresh).pack(side="left")
        self.lbl_status = ttk.Label(top, text="")
        self.lbl_status.pack(side="left", padx=10)
        ttk.Button(top, text="打开网页面板", command=self.open_web).pack(side="right")
        ttk.Button(top, text="设置 sudo 密码", command=self.set_pw).pack(side="right", padx=4)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(paned, width=240)
        paned.add(left, weight=0)
        ttk.Label(left, text="实例").pack(anchor="w")
        self.listbox = tk.Listbox(left, height=18)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        btns = ttk.Frame(left)
        btns.pack(fill="x")
        ttk.Button(btns, text="新建", command=self.new_instance).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text="启动", command=lambda: self.act("start")).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text="停止", command=lambda: self.act("stop")).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text="备份", command=lambda: self.act("backup")).pack(side="left", fill="x", expand=True)

        self.nb = ttk.Notebook(paned)
        paned.add(self.nb, weight=1)

        # 控制台
        f1 = ttk.Frame(self.nb)
        self.nb.add(f1, text="控制台")
        self.txt = scrolledtext.ScrolledText(f1, height=22, font=("Consolas", 11), bg="#0b0f14", fg="#e6edf3", insertbackground="#e6edf3")
        self.txt.pack(fill="both", expand=True, padx=4, pady=4)
        bar = ttk.Frame(f1)
        bar.pack(fill="x", padx=4, pady=2)
        self.cmd = ttk.Entry(bar)
        self.cmd.pack(side="left", fill="x", expand=True)
        self.cmd.bind("<Return>", lambda e: self.send())
        ttk.Button(bar, text="发送", command=self.send).pack(side="left")
        ttk.Button(bar, text="清屏", command=lambda: self.txt.delete("1.0", "end")).pack(side="left")

        # 下载器
        f2 = ttk.Frame(self.nb)
        self.nb.add(f2, text="下载/新建")
        self._build_download(f2)

        # 防火墙
        f3 = ttk.Frame(self.nb)
        self.nb.add(f3, text="防火墙")
        self._build_fw(f3)

        # NBT
        f4 = ttk.Frame(self.nb)
        self.nb.add(f4, text="NBT")
        self._build_nbt(f4)

        # 联机
        f7 = ttk.Frame(self.nb)
        self.nb.add(f7, text="联机/映射")
        self._build_net(f7)

        # 流水线
        f5 = ttk.Frame(self.nb)
        self.nb.add(f5, text="流水线")
        self._build_pipe(f5)

        # 清理/系统
        f6 = ttk.Frame(self.nb)
        self.nb.add(f6, text="清理/系统")
        self._build_clean(f6)

        self.statusbar = ttk.Label(self, text="", anchor="w")
        self.statusbar.pack(fill="x", padx=8, pady=2)

    def _build_download(self, f):
        g = ttk.LabelFrame(f, text="新建实例（自动下载服务端）")
        g.pack(fill="x", padx=6, pady=6)
        rows = [
            ("实例名", "name", "survival"),
            ("类型", "kind", "paper"),
            ("版本", "version", "1.21.4"),
            ("内存", "memory", "2G"),
            ("端口", "port", "25565"),
        ]
        self.vars = {}
        for i, (label, key, default) in enumerate(rows):
            ttk.Label(g, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            v = tk.StringVar(value=default)
            if key == "kind":
                w = ttk.Combobox(g, textvariable=v, values=["vanilla", "paper", "purpur", "fabric", "forge", "neoforge", "bedrock"], width=22)
            else:
                w = ttk.Entry(g, textvariable=v, width=26)
            w.grid(row=i, column=1, sticky="w", padx=4, pady=2)
            self.vars[key] = v
        ttk.Button(g, text="获取最新版号", command=self.fetch_versions).grid(row=1, column=2, padx=6)
        ttk.Button(g, text="创建并下载", command=self.do_create).grid(row=5, column=1, pady=8, sticky="w")
        self.dl_log = scrolledtext.ScrolledText(f, height=10, font=("Consolas", 10))
        self.dl_log.pack(fill="both", expand=True, padx=6, pady=4)

    def _build_fw(self, f):
        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=6, pady=6)
        for label, port, proto in (("Java 25565", 25565, "tcp"), ("基岩 19132", 19132, "udp"), ("基岩 IPv6 19133", 19133, "udp"), ("RCON 25575", 25575, "tcp")):
            ttk.Button(bar, text=f"放行 {label}", command=lambda p=port, pr=proto: self.fw_allow(p, pr)).pack(side="left", padx=4)
        ttk.Button(bar, text="状态", command=self.fw_status).pack(side="left", padx=4)
        ttk.Button(bar, text="启用", command=lambda: self.fw_toggle("enable")).pack(side="left", padx=4)
        ttk.Button(bar, text="停用", command=lambda: self.fw_toggle("disable")).pack(side="left", padx=4)
        self.fw_txt = scrolledtext.ScrolledText(f, height=18, font=("Consolas", 10))
        self.fw_txt.pack(fill="both", expand=True, padx=6, pady=4)

    def _build_nbt(self, f):
        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=6, pady=6)
        ttk.Label(bar, text="文件").pack(side="left")
        self.nbt_path = tk.StringVar()
        ttk.Entry(bar, textvariable=self.nbt_path, width=60).pack(side="left", padx=4)
        ttk.Button(bar, text="选择", command=self.nbt_pick).pack(side="left")
        ttk.Button(bar, text="读取", command=self.nbt_read).pack(side="left", padx=4)
        form = ttk.Frame(f)
        form.pack(fill="x", padx=6)
        ttk.Label(form, text="路径").grid(row=0, column=0)
        self.nbt_expr = tk.StringVar(value="Data.LevelName")
        ttk.Entry(form, textvariable=self.nbt_expr, width=30).grid(row=0, column=1)
        ttk.Label(form, text="新值").grid(row=0, column=2)
        self.nbt_val = tk.StringVar()
        ttk.Entry(form, textvariable=self.nbt_val, width=24).grid(row=0, column=3)
        ttk.Button(form, text="写入", command=self.nbt_write).grid(row=0, column=4, padx=6)
        self.nbt_txt = scrolledtext.ScrolledText(f, height=18, font=("Consolas", 10))
        self.nbt_txt.pack(fill="both", expand=True, padx=6, pady=4)

    def _build_net(self, f):
        import tkinter.messagebox as mb

        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=6, pady=6)
        ttk.Button(bar, text="刷新连接信息", command=self.net_info).pack(side="left", padx=3)
        ttk.Label(bar, text="端口").pack(side="left")
        self.net_port = tk.StringVar(value="25565")
        ttk.Entry(bar, textvariable=self.net_port, width=8).pack(side="left")
        self.net_proto = tk.StringVar(value="TCP")
        ttk.Combobox(bar, textvariable=self.net_proto, values=["TCP", "UDP"], width=6).pack(side="left")
        ttk.Button(bar, text="UPnP 自动映射", command=self.net_upnp_add).pack(side="left", padx=3)
        ttk.Button(bar, text="删除映射", command=self.net_upnp_del).pack(side="left", padx=3)
        ttk.Button(bar, text="安装 Tailscale", command=lambda: self.net_ts("install")).pack(side="left", padx=3)
        ttk.Button(bar, text="tailscale up", command=lambda: self.net_ts("up")).pack(side="left", padx=3)
        self.net_txt = scrolledtext.ScrolledText(f, height=20, font=("Consolas", 10))
        self.net_txt.pack(fill="both", expand=True, padx=6, pady=4)
        self.net_info()

    def net_info(self):
        from . import net as netmod
        from . import instance as _im

        info = netmod.connection_info(_im.list_instances())
        lines = [f"局域网 IP: {info['lan_ip']}   公网 IP: {info['public_ip']}   Tailscale: {info['tailscale_ip']}", ""]
        for s in info["servers"]:
            lines.append(f"{s['name']}: {s['proto']} {s['port']}  局域网 {s['lan']}" + (f"  虚拟网 {s['tailscale']}" if s["tailscale"] else ""))
        lines.append("")
        lines.append("另一台电脑在游戏里添加服务器，填上面的地址即可。")
        self.net_txt.delete("1.0", "end")
        self.net_txt.insert("end", "\n".join(lines))

    def net_upnp_add(self):
        from . import net as netmod
        import tkinter.messagebox as mb

        ok, msg = netmod.upnp_add(int(self.net_port.get() or 25565), self.net_proto.get())
        mb.showinfo("结果", msg)

    def net_upnp_del(self):
        from . import net as netmod
        import tkinter.messagebox as mb

        ok, msg = netmod.upnp_remove(int(self.net_port.get() or 25565), self.net_proto.get())
        mb.showinfo("结果", msg)

    def net_ts(self, a):
        from . import net as netmod
        import tkinter.messagebox as mb

        ok, msg = netmod.tailscale_install() if a == "install" else netmod.tailscale_up()
        mb.showinfo("结果", msg)
        self.net_info()

    def _build_pipe(self, f):
        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=6, pady=6)
        self.pipe_name = tk.StringVar(value="每日维护")
        ttk.Combobox(bar, textvariable=self.pipe_name, values=list(pipeline.TEMPLATES.keys()), width=20).pack(side="left")
        ttk.Button(bar, text="载入模板", command=self.pipe_load).pack(side="left", padx=4)
        ttk.Button(bar, text="运行 YAML", command=lambda: self.pipe_run("yaml")).pack(side="left", padx=4)
        ttk.Button(bar, text="运行脚本", command=lambda: self.pipe_run("script")).pack(side="left", padx=4)
        ttk.Button(bar, text="保存", command=self.pipe_save).pack(side="left", padx=4)
        self.pipe_txt = scrolledtext.ScrolledText(f, height=14, font=("Consolas", 10))
        self.pipe_txt.pack(fill="both", expand=True, padx=6, pady=4)
        self.pipe_out = scrolledtext.ScrolledText(f, height=10, font=("Consolas", 10), bg="#0b0f14", fg="#7ee787")
        self.pipe_out.pack(fill="both", expand=True, padx=6, pady=4)

    def _build_clean(self, f):
        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=6, pady=6)
        ttk.Button(bar, text="扫描可清理", command=self.clean_scan).pack(side="left", padx=4)
        ttk.Button(bar, text="清理选中", command=self.clean_do).pack(side="left", padx=4)
        ttk.Button(bar, text="apt 缓存", command=self.clean_apt).pack(side="left", padx=4)
        self.clean_paths = []
        self.clean_box = tk.Listbox(f, selectmode="multiple", height=10)
        self.clean_box.pack(fill="x", padx=6)
        self.clean_txt = scrolledtext.ScrolledText(f, height=12, font=("Consolas", 10))
        self.clean_txt.pack(fill="both", expand=True, padx=6, pady=4)

    # ------------------------------------------------ 逻辑
    def say(self, text):
        self.statusbar.config(text=text)
        self.update_idletasks()

    def refresh(self):
        items = inst.list_instances()
        self.listbox.delete(0, "end")
        for m in items:
            self.listbox.insert("end", f"{m['name']}  [{'运行' if m['running'] else '停止'}] {m['kind']} {m.get('version','')}")
        st = f"用户 {util.current_user()}{'(root)' if util.is_root() else ''} · 防火墙 {firewall.backend()} · Java {javamod.pick_java() or '无'}"
        self.lbl_status.config(text=st)
        self.after(5000, self.refresh)
        if self.cur:
            self.tail()

    def on_select(self, e):
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.listbox.get(sel[0]).split("  ")[0]
        self.cur = name

    def act(self, action):
        if not self.cur:
            return messagebox.showinfo("提示", "先选一个实例")
        fn = {"start": inst.start, "stop": inst.stop, "backup": inst.backup}[action]
        ok, msg = fn(self.cur)
        messagebox.showinfo("结果", msg) if not ok else self.say(msg)
        self.refresh()

    def send(self):
        if not self.cur:
            return
        cmd = self.cmd.get().strip()
        if not cmd:
            return
        inst.send_command(self.cur, cmd)
        self.cmd.delete(0, "end")

    def tail(self):
        if not self.cur:
            return
        lines = inst.console_tail(self.cur, 200)
        self.txt.delete("1.0", "end")
        self.txt.insert("end", "\n".join(lines))
        self.txt.see("end")
        self.after(2500, self.tail)

    def new_instance(self):
        self.nb.select(1)

    def fetch_versions(self):
        def _t():
            try:
                r = catalog.list_core(self.vars["kind"].get())
                arr = (r.get("data") or {}).get("versions") or []
                if arr:
                    self.vars["version"].set(arr[0])
                    self.say("最新版本：" + str(arr[0]))
            except Exception as ex:
                self.say(str(ex))

        threading.Thread(target=_t, daemon=True).start()

    def do_create(self):
        def _t():
            self.say("正在创建并下载服务端…")
            ok, msg, meta = inst.create_instance(
                self.vars["name"].get().strip(),
                self.vars["kind"].get(),
                self.vars["version"].get().strip(),
                self.vars["memory"].get().strip(),
                None,
                int(self.vars["port"].get() or 25565),
            )
            self.dl_log.insert("end", f"{'✅' if ok else '❌'} {msg}\n")
            self.dl_log.see("end")
            self.say(msg)
            self.refresh()

        if not self.vars["name"].get().strip():
            return messagebox.showinfo("提示", "请填写实例名")
        threading.Thread(target=_t, daemon=True).start()

    def fw_allow(self, port, proto):
        ok, msg = firewall.allow_port(port, proto)
        messagebox.showinfo("结果", msg)
        self.fw_status()

    def fw_toggle(self, a):
        ok, msg = firewall.enable() if a == "enable" else firewall.disable()
        messagebox.showinfo("结果", msg)
        self.fw_status()

    def fw_status(self):
        s = firewall.status()
        self.fw_txt.delete("1.0", "end")
        self.fw_txt.insert("end", f"后端：{s['backend']}  启用：{s['active']}\n\n{s['raw']}")

    def nbt_pick(self):
        p = filedialog.askopenfilename(title="选择 NBT 文件", filetypes=[("NBT/level.dat", "*.dat *.nbt"), ("所有", "*.*")])
        if p:
            self.nbt_path.set(p)
            self.nbt_read()

    def nbt_read(self):
        p = self.nbt_path.get().strip()
        if not p:
            return
        try:
            root = nbt.load(p)
            self.nbt_txt.delete("1.0", "end")
            self.nbt_txt.insert("end", root.pretty()[:20000])
        except Exception as ex:
            messagebox.showerror("读取失败", str(ex))

    def nbt_write(self):
        p = self.nbt_path.get().strip()
        try:
            ok, msg = nbt.set_path_file(p, self.nbt_expr.get().strip(), self.nbt_val.get())
            messagebox.showinfo("结果", msg)
            self.nbt_read()
        except Exception as ex:
            messagebox.showerror("写入失败", str(ex))

    def pipe_load(self):
        k = self.pipe_name.get()
        self.pipe_txt.delete("1.0", "end")
        self.pipe_txt.insert("end", pipeline.TEMPLATES.get(k, pipeline.TEMPLATES["每日维护"]))

    def pipe_save(self):
        name = self.pipe_name.get().strip() or "pipeline"
        try:
            p = pipeline.save_pipeline(name, self.pipe_txt.get("1.0", "end"))
            messagebox.showinfo("已保存", p)
        except Exception as ex:
            messagebox.showerror("失败", str(ex))

    def pipe_run(self, kind):
        content = self.pipe_txt.get("1.0", "end")

        def _t():
            try:
                r = pipeline.run_pipeline_text(content, {}, self.pipe_name.get()) if kind == "yaml" else pipeline.run_simple_script(content, {}, self.pipe_name.get())
                self.pipe_out.delete("1.0", "end")
                self.pipe_out.insert("end", "\n".join(r.logs))
            except Exception as ex:
                self.pipe_out.insert("end", f"失败：{ex}\n")

        threading.Thread(target=_t, daemon=True).start()

    def clean_scan(self):
        self.clean_box.delete(0, "end")
        self.clean_paths = []
        for grp in cleaner.targets():
            for g in grp["groups"]:
                for it in g["items"]:
                    self.clean_paths.append(it["path"])
                    self.clean_box.insert("end", f"{it['size_h']}  {it['path']}")

    def clean_do(self):
        sel = [self.clean_paths[i] for i in self.clean_box.curselection()]
        if not sel:
            return
        if not messagebox.askyesno("确认", f"删除 {len(sel)} 个文件？"):
            return
        d, f, err = cleaner.clean(sel)
        self.clean_txt.insert("end", f"已删除 {d} 个，释放 {util.humansize(f)}\n")
        self.clean_scan()

    def clean_apt(self):
        ok, msg = cleaner.apt_clean()
        messagebox.showinfo("结果", msg)

    def set_pw(self):
        win = tk.Toplevel(self)
        win.title("自动 root 密码")
        win.geometry("420x200")
        ttk.Label(win, text="输入当前用户的 sudo 密码（仅本机加密保存）").pack(pady=8)
        e = ttk.Entry(win, show="*", width=36)
        e.pack()
        e.focus()

        def save():
            ok, msg = secret.test_password(e.get())
            if ok:
                secret.set_sudo_password(e.get())
                messagebox.showinfo("成功", msg)
                win.destroy()
            else:
                messagebox.showerror("失败", msg)

        ttk.Button(win, text="验证并保存", command=save).pack(pady=6)
        ttk.Button(win, text="写入免密 sudoers（更安全）", command=lambda: [messagebox.showinfo("结果", secret.install_sudoers()[1]), win.destroy()]).pack()

    def open_web(self):
        from . import server

        def _t():
            try:
                server.serve(port=util.config().get("port", 8850), open_browser=True, quiet=True)
            except Exception as ex:
                self.say(f"启动网页面板失败：{ex}")

        threading.Thread(target=_t, daemon=True).start()
        self.say(f"网页面板已在后台启动：http://127.0.0.1:{util.config().get('port', 8850)}/")

    def on_close(self):
        self.destroy()


def main():
    if not HAS_TK:
        print("未检测到图形库 tkinter。Debian 请先执行：sudo apt install python3-tk")
        print("或者直接用网页面板：python3 -m mcpanel serve")
        return
    try:
        App().mainloop()
    except Exception as ex:
        print("无法打开图形窗口：", ex)
        print("提示：Debian 上请先执行 sudo apt install python3-tk，或使用网页面板：python3 -m mcpanel serve")


if __name__ == "__main__":
    main()
