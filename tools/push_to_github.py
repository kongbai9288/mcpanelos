"""通过 GitHub API 把本仓库内容推送到远端（沙盒无法直连 github.com 时用它）。

用法: GITHUB_TOKEN=xxx python3 tools/push_to_github.py [owner/repo] [分支]
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

API = "https://api.github.com"


def api(method: str, path: str, body=None, token: str = ""):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "mcpanel-push",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode("utf-8", "ignore")[:400]}
    except Exception as ex:
        return {"_error": 0, "_body": f"{type(ex).__name__}: {ex}"}


def main():
    token = os.environ.get("GITHUB_TOKEN") or (sys.argv[3] if len(sys.argv) > 3 else "")
    if not token:
        print("缺少 GITHUB_TOKEN")
        return 1
    repo = sys.argv[1] if len(sys.argv) > 1 else "kongbai9288/mcpanelos"
    branch = sys.argv[2] if len(sys.argv) > 2 else "main"
    root = Path(__file__).resolve().parent.parent

    skip_dirs = {".git", "__pycache__", "build/chroot"}
    files = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".git/") or "__pycache__" in rel or rel.endswith(".pyc"):
            continue
        files.append((rel, p))

    print(f"上传 {len(files)} 个文件 → {repo}@{branch}")
    tree = []
    for rel, p in files:
        raw = p.read_bytes()
        blob = api("POST", f"/repos/{repo}/git/blobs", {"content": base64.b64encode(raw).decode(), "encoding": "base64"}, token)
        if "_error" in blob:
            print("  blob 失败", rel, blob["_error"], blob["_body"][:120])
            return 1
        mode = "100755" if (p.stat().st_mode & 0o111) else "100644"
        tree.append({"path": rel, "mode": mode, "type": "blob", "sha": blob["sha"]})
        print("  +", rel, f"{len(raw)}B")

    tr = api("POST", f"/repos/{repo}/git/trees", {"tree": tree}, token)
    if "_error" in tr:
        print("tree 失败:", tr)
        return 1
    parent = api("GET", f"/repos/{repo}/git/ref/heads/{branch}", None, token)
    parents = []
    if "_error" not in parent:
        parents = [parent["object"]["sha"]]
    cm = api("POST", f"/repos/{repo}/git/commits", {"message": "MCPanelOS: MC 服务器图形面板 + 完整 Linux 构建脚本", "tree": tr["sha"], "parents": parents}, token)
    if "_error" in cm:
        print("commit 失败:", cm)
        return 1
    if parents:
        ref = api("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", {"sha": cm["sha"]}, token)
    else:
        ref = api("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": cm["sha"]}, token)
    if "_error" in ref:
        print("ref 失败:", ref)
        return 1
    print("✅ 完成：", cm["sha"][:8], f"https://github.com/{repo}/tree/{branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
