"""sudo / root 密码的安全保存与自动输入。

三档策略（从优到次）：
1. 已经是 root —— 直接执行，无需密码；
2. sudoers 免密规则（推荐）—— 写入 /etc/sudoers.d/mcpanel，仅放行白名单命令；
3. 加密保存密码 —— 用 sudo -S 自动输入，密钥文件 0600 存放。

加密优先使用 cryptography 的 Fernet；不可用时退回轻量混淆（并明确提示）。
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from pathlib import Path

from .util import home, run, is_root, current_user

SECRET_FILE = "secret.enc"
KEY_FILE = "secret.key"
OBFUSCATED = False


def _key_path() -> Path:
    return home() / KEY_FILE


def _secret_path() -> Path:
    return home() / SECRET_FILE


def _load_key() -> bytes:
    kp = _key_path()
    if kp.exists():
        return kp.read_bytes()
    key = base64.urlsafe_b64encode(os.urandom(32))
    kp.write_bytes(key)
    try:
        os.chmod(kp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return key


def _fernet():
    try:
        from cryptography.fernet import Fernet  # type: ignore
    except Exception:
        return None
    key = _load_key()
    if len(key) != 44:
        import base64 as _b

        key = _b.urlsafe_b64encode(hashlib.sha256(key).digest())
    return Fernet(key)


def _obf(data: bytes, key: bytes) -> bytes:
    k = hashlib.sha256(key).digest()
    return bytes(b ^ k[i % len(k)] for i, b in enumerate(data))


def set_sudo_password(password: str) -> bool:
    """加密保存 sudo 密码。"""
    global OBFUSCATED
    p = _secret_path()
    f = _fernet()
    raw = password.encode("utf-8")
    if f is not None:
        blob = b"F" + f.encrypt(raw)
        OBFUSCATED = False
    else:
        blob = b"X" + _obf(raw, _load_key())
        OBFUSCATED = True
    p.write_bytes(blob)
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return True


def get_sudo_password() -> str | None:
    p = _secret_path()
    if not p.exists():
        return None
    blob = p.read_bytes()
    try:
        if blob[:1] == b"F":
            f = _fernet()
            if f is None:
                return None
            return f.decrypt(blob[1:]).decode("utf-8")
        return _obf(blob[1:], _load_key()).decode("utf-8")
    except Exception:
        return None


def has_password() -> bool:
    return _secret_path().exists()


def clear_password() -> bool:
    p = _secret_path()
    if p.exists():
        p.unlink()
    return True


def test_password(password: str) -> tuple[bool, str]:
    """验证密码是否可用于提权。"""
    if is_root():
        return True, "当前已是 root，不需要密码"
    if password:
        r = run(["sudo", "-S", "-p", "", "-k", "true"], input_text=password + "\n", timeout=15)
        if r.rc == 0:
            return True, "密码正确，sudo 可用"
        msg = (r.err or r.out or "").strip().splitlines()
        return False, (msg[-1] if msg else "sudo 验证失败")
    r = run(["sudo", "-n", "true"], timeout=8)
    return (True, "sudo 免密可用") if r.rc == 0 else (False, "既非 root，也没有可用的 sudo 免密或密码")


# ---------------------------------------------------------------- sudoers


SUDOERS_FILE = "/etc/sudoers.d/mcpanel"

SUDOERS_DEFAULT_CMDS = [
    "/usr/bin/systemctl",
    "/bin/systemctl",
    "/usr/sbin/ufw",
    "/usr/bin/apt-get",
    "/usr/bin/apt",
    "/usr/sbin/iptables",
    "/usr/bin/tee",
    "/usr/bin/install",
    "/bin/chmod",
    "/bin/chown",
]


def sudoers_content(user: str | None = None, cmds=None) -> str:
    user = user or current_user()
    cmds = cmds or SUDOERS_DEFAULT_CMDS
    lines = [
        "# mcpanel 自动生成的免密规则（仅放行下列命令）",
        f"{user} ALL=(ALL) NOPASSWD: " + ", ".join(cmds),
    ]
    return "\n".join(lines) + "\n"


def install_sudoers(user: str | None = None, cmds=None) -> tuple[bool, str]:
    """写入 sudoers 免密规则（先 visudo -c 校验）。"""
    from .util import as_root

    content = sudoers_content(user, cmds)
    tmp = "/tmp/mcpanel-sudoers.tmp"
    Path(tmp).write_text(content, encoding="utf-8")
    chk = as_root(["visudo", "-c", "-f", tmp], timeout=20)
    if chk.rc != 0:
        return False, "校验失败，未写入：" + (chk.err or chk.out).strip()
    r = as_root(["install", "-m", "0440", tmp, SUDOERS_FILE], timeout=20)
    if r.rc != 0:
        r2 = as_root(["sh", "-c", f"cp {tmp} {SUDOERS_FILE} && chmod 0440 {SUDOERS_FILE}"], timeout=20)
        if r2.rc != 0:
            return False, "写入失败：" + (r2.err or r2.out).strip()
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return True, f"已写入 {SUDOERS_FILE}"


def sudoers_status() -> dict:
    exists = os.path.exists(SUDOERS_FILE)
    content = ""
    if exists:
        try:
            content = Path(SUDOERS_FILE).read_text(encoding="utf-8")
        except Exception:
            content = "(无法读取)"
    return {
        "root": is_root(),
        "nopasswd": run(["sudo", "-n", "true"], timeout=8).rc == 0,
        "sudoers_exists": exists,
        "sudoers_content": content,
        "password_saved": has_password(),
        "obfuscated_only": OBFUSCATED,
        "user": current_user(),
    }


def remove_sudoers() -> tuple[bool, str]:
    from .util import as_root

    if not os.path.exists(SUDOERS_FILE):
        return True, "不存在"
    r = as_root(["rm", "-f", SUDOERS_FILE], timeout=20)
    return (True, "已删除") if r.rc == 0 else (False, r.err or r.out)
