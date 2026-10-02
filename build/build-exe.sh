#!/usr/bin/env bash
# 把面板打包成「一个可执行程序」（不是一堆 .py）
# 产出：dist/mcpanel  —— 拷到任何同架构 Linux 上 chmod +x 就能跑
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SRC_DIR"
NAME=mcpanel

echo "▶ 检查 PyInstaller"
python3 -m PyInstaller --version >/dev/null 2>&1 || {
  apt-get install -y python3-pip >/dev/null 2>&1 || true
  python3 -m pip install --break-system-packages -q pyinstaller || python3 -m pip install -q pyinstaller
}

echo "▶ 打包中（约 1-3 分钟）"
rm -rf build/exe dist/${NAME} ${NAME}.spec
python3 -m PyInstaller \
  --onefile \
  --name ${NAME} \
  --clean --noconfirm \
  --add-data "webui:webui" \
  --hidden-import mcpanel \
  --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide6 \
  --exclude-module PIL --exclude-module numpy --exclude-module matplotlib \
  --distpath dist --workpath build/exe --specpath . \
  mcpanel/__main__.py

chmod +x "dist/${NAME}"
echo
echo "✔ 完成：$(pwd)/dist/${NAME}  ($(du -h dist/${NAME} | cut -f1))"
echo "  用法："
echo "    sudo ./dist/${NAME} serve            # 网页面板 http://本机IP:8850/"
echo "    sudo ./dist/${NAME} wizard           # 开服向导"
echo "    sudo ./dist/${NAME} desktop          # 桌面窗口（需 python3-tk）"
echo "    sudo cp dist/${NAME} /usr/local/bin/ # 装成系统命令"
