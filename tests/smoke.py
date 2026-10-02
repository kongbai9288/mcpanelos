"""冒烟测试：在一个进程内起服务，用 urllib 打所有主要接口，检查是否 200/ok。"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcpanel import server  # noqa: E402

BASE = "http://127.0.0.1:8931"

GETS = [
    "state", "system/info", "instances", "catalog/kinds", "java/list",
    "firewall/status", "firewall/ports", "cleaner/scan", "cleaner/disk",
    "logs/list?instance=", "pipeline/list", "pipeline/runs", "dl/list",
    "secret/status", "autostart/env", "browser/roots", "backups",
    "browser/read?path=/etc/hostname", "browser/list?path=/etc",
    "browser/stat?path=/etc/hostname",
    "net/info", "net/recommend", "net/tailscale", "net/portcheck?port=22&host=127.0.0.1",
]

POSTS = [
    ("pipeline/run", {"content": "name: t\nsteps:\n  - name: a\n    run: echo hi\n", "kind": "yaml", "name": "t"}),
    ("pipeline/run", {"content": "@X=1\necho $X-ok\n", "kind": "script", "name": "s"}),
    ("browser/write", {"path": "/tmp/mcpanel-selftest.txt", "text": "hello"}),
    ("settings", {"port": 8931, "java_auto_install": True}),
    ("cleaner/clean", {"paths": ["/tmp/mcpanel-selftest.txt"]}),
    ("net/frp/preview", {"server": "1.2.3.4", "sport": 7000, "local": 25565, "remote": 25565}),
]


def req(path, data=None):
    url = f"{BASE}/api/{path}"
    if data is None:
        r = urllib.request.Request(url)
    else:
        r = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return e.code, {"ok": False, "error": e.read().decode("utf-8", "ignore")[:200]}
    except Exception as ex:
        return 0, {"ok": False, "error": f"{type(ex).__name__}: {ex}"}


def main():
    t = threading.Thread(target=lambda: server.serve(port=8931, bind="127.0.0.1", quiet=True), daemon=True)
    t.start()
    time.sleep(1.5)
    fails = []
    for g in GETS:
        code, j = req(g)
        status = "OK " if code == 200 else "FAIL"
        if code != 200:
            fails.append((g, code, str(j)[:160]))
        print(f"{status} GET  {g:<42} {code}")
    for path, body in POSTS:
        code, j = req(path, body)
        okk = code == 200 and j.get("ok")
        if not okk:
            fails.append((path, code, str(j)[:160]))
        print(f"{'OK ' if okk else 'FAIL'} POST {path:<42} {code} {str(j)[:90]}")
    print("\n静态资源：")
    for f in ("/", "/app.js", "/style.css", "/favicon.svg"):
        try:
            with urllib.request.urlopen(BASE + f, timeout=10) as resp:
                print(f"OK  {f:<14} {resp.status} {len(resp.read())}B")
        except Exception as ex:
            fails.append((f, 0, str(ex)))
            print(f"FAIL {f} {ex}")
    print("\n失败项：", len(fails))
    for f in fails:
        print("  ", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
