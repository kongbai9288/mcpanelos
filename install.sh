#!/usr/bin/env bash
# MC 服务器面板 · Debian 一键安装器
# 用法: sudo bash install.sh [--with-desktop] [--port 8850]
set -euo pipefail

APP=mcpanel
PORT=8850
WITH_DESKTOP=0
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-desktop) WITH_DESKTOP=1; shift ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "未知参数 $1"; exit 1 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "请用 sudo 运行：sudo bash install.sh"; exit 1; }

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }

say "检查系统"
. /etc/os-release || true
if [[ "${ID:-}" != "debian" && "${ID_LIKE:-}" != *debian* ]]; then
  warn "看起来不是 Debian/Ubuntu，脚本仍会尝试继续"
fi
ok "系统：${PRETTY_NAME:-unknown}"

say "安装依赖（apt）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
PKGS="python3 python3-pip curl wget unzip tar ca-certificates ufw sudo"
PKGS="$PKGS openjdk-21-jre-headless"
if [[ $WITH_DESKTOP -eq 1 ]]; then PKGS="$PKGS python3-tk"; fi
apt-get install -y $PKGS || warn "部分包安装失败，可稍后手动补"
ok "依赖安装完成"

say "安装面板程序"
PREFIX="/opt/$APP"
mkdir -p "$PREFIX"
if [[ -d "$SRC_DIR/mcpanel" ]]; then
  cp -r "$SRC_DIR/mcpanel" "$SRC_DIR/webui" "$PREFIX/"
else
  echo "未找到源码目录 $SRC_DIR/mcpanel"
  exit 1
fi
mkdir -p /srv/minecraft /var/lib/$APP
ok "已安装到 $PREFIX"

# 命令行入口
cat > /usr/local/bin/mcpanel <<EOF
#!/bin/sh
exec python3 -m mcpanel "\$@"
EOF
chmod +x /usr/local/bin/mcpanel

cat > /usr/local/bin/mcpanel-launch <<EOF
#!/bin/sh
export PYTHONPATH="$PREFIX\${PYTHONPATH:+:\$PYTHONPATH}"
export MCPANEL_HOME="/var/lib/$APP"
export MCPANEL_SERVERS="/srv/minecraft"
cd "$PREFIX"
exec python3 -m mcpanel "\$@"
EOF
chmod +x /usr/local/bin/mcpanel-launch

say "创建专用运行用户（可选，建议）"
if ! id -u minecraft >/dev/null 2>&1; then
  useradd --system --home-dir /srv/minecraft --shell /usr/sbin/nologin minecraft || true
  ok "已创建 minecraft 用户"
else
  ok "minecraft 用户已存在"
fi
chown -R minecraft:minecraft /srv/minecraft 2>/dev/null || true

say "配置 sudoers 免密（仅白名单命令）"
cat > /etc/sudoers.d/$APP <<EOF
# mcpanel 自动放行的最小命令集
root ALL=(ALL) NOPASSWD: ALL
%sudo ALL=(ALL) NOPASSWD: /usr/bin/systemctl, /bin/systemctl, /usr/sbin/ufw, /usr/bin/apt-get, /usr/bin/apt, /usr/bin/install, /bin/chmod, /bin/chown, /usr/bin/tee
EOF
chmod 0440 /etc/sudoers.d/$APP
if visudo -c -f /etc/sudoers.d/$APP >/dev/null 2>&1; then ok "sudoers 已写入并通过校验"; else warn "sudoers 校验失败，已回滚"; rm -f /etc/sudoers.d/$APP; fi

say "防火墙放行"
if command -v ufw >/dev/null; then
  ufw allow ${PORT}/tcp comment 'MC Panel' >/dev/null 2>&1 || true
  ufw allow 25565/tcp comment 'Minecraft Java' >/dev/null 2>&1 || true
  ufw allow 19132/udp comment 'Minecraft Bedrock' >/dev/null 2>&1 || true
  ufw --force enable >/dev/null 2>&1 || true
  ok "已放行 8850 / 25565 / 19132"
else
  warn "没有 ufw，跳过"
fi

say "写入 systemd 服务"
cat > /etc/systemd/system/$APP.service <<EOF
[Unit]
Description=MC Server Panel
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PREFIX
Environment=PYTHONPATH=$PREFIX
Environment=MCPANEL_HOME=/var/lib/$APP
Environment=MCPANEL_SERVERS=/srv/minecraft
ExecStart=/usr/bin/python3 -m mcpanel serve --port $PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now $APP.service
ok "面板服务已启动（端口 $PORT）"

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
say "完成"
echo "  网页面板： http://${IP:-127.0.0.1}:${PORT}/"
echo "  命令行：   mcpanel status | mcpanel wizard | mcpanel desktop"
if [[ $WITH_DESKTOP -eq 1 ]]; then echo "  桌面窗口： mcpanel desktop（需要有图形环境）"; fi
echo "  开服向导： mcpanel wizard"
