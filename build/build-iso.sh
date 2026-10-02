#!/usr/bin/env bash
# 构建 MCPanelOS —— 内置 MC 服务器面板的 Debian Live 系统（ISO）
#
# 产出：mcpanelos-<版本>-amd64.hybrid.iso（BIOS + UEFI 双启动，可刻录 U 盘）
# 用法：sudo bash build/build-iso.sh [--suite bookworm] [--no-iso] [--with-desktop]
#
# 说明：必须在 Debian/Ubuntu 机器上以 root 运行（需要 debootstrap）。
set -euo pipefail

SUITE="${SUITE:-bookworm}"
ARCH=amd64
MAKE_ISO=1
WITH_DESKTOP=0
OUT_NAME="mcpanelos"
WORK="${WORK:-/tmp/mcpanelos-build}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --suite) SUITE="$2"; shift 2 ;;
    --no-iso) MAKE_ISO=0; shift ;;
    --with-desktop) WITH_DESKTOP=1; shift ;;
    --work) WORK="$2"; shift 2 ;;
    --password) ROOT_PW="$2"; shift 2 ;;
    --no-password) NO_PW=1; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

# 安全策略：绝不把任何密码写进代码。
#   --password <pw>   构建者自己指定（注意别提交到仓库）
#   --no-password     直接锁定 root 密码，只用控制台自动登录 + SSH 密钥
#   默认             随机生成一次，仅在构建结束时打印，不落盘、不进仓库
ROOT_PW="${ROOT_PW:-}"
NO_PW="${NO_PW:-0}"

[[ $EUID -eq 0 ]] || { echo "请用 root 运行：sudo bash build/build-iso.sh"; exit 1; }

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✘ %s\033[0m\n' "$*"; exit 1; }

ROOT="$WORK/chroot"
ISO_DIR="$WORK/iso"
SQUASH="$ISO_DIR/live/filesystem.squashfs"

say "安装构建工具"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y debootstrap squashfs-tools xorriso \
  grub-pc-bin grub-efi-amd64-bin grub-common mtools dosfstools rsync || die "构建工具安装失败"
ok "工具就绪"

say "清理工作目录"
rm -rf "$WORK"
mkdir -p "$ROOT" "$ISO_DIR/live" "$ISO_DIR/boot/grub"

say "debootstrap $SUITE/$ARCH（约几分钟）"
debootstrap --arch="$ARCH" --variant=minbase \
  --include="systemd,systemd-sysv,linux-image-$ARCH,linux-headers-$ARCH,grub-common,live-boot,live-boot-initramfs-tools,initramfs-tools,sudo,curl,wget,unzip,tar,zip,ca-certificates,ufw,openssh-server,network-manager,iproute2,procps,less,vim,nano,htop,tmux,screen,python3,python3-pip,python3-venv,locales,console-setup,bash-completion,jq,git" \
  "$SUITE" "$ROOT" http://deb.debian.org/debian || die "debootstrap 失败"
ok "基础系统完成"

say "写入软件源与主机名"
cat > "$ROOT/etc/apt/sources.list" <<EOF
deb http://deb.debian.org/debian $SUITE main contrib non-free non-free-firmware
deb http://security.debian.org/debian-security $SUITE-security main contrib non-free non-free-firmware
EOF
echo "mcpanelos" > "$ROOT/etc/hostname"
cat > "$ROOT/etc/hosts" <<EOF
127.0.0.1 localhost
127.0.1.1 mcpanelos
::1 localhost ip6-localhost ip6-loopback
EOF

say "挂载 chroot 所需文件系统"
mount --bind /dev "$ROOT/dev" 2>/dev/null || true
mount -t proc proc "$ROOT/proc"
mount -t sysfs sys "$ROOT/sys"
mount -t devpts devpts "$ROOT/dev/pts" 2>/dev/null || true

cleanup_mounts() {
  umount -lf "$ROOT/dev/pts" 2>/dev/null || true
  umount -lf "$ROOT/dev" 2>/dev/null || true
  umount -lf "$ROOT/proc" 2>/dev/null || true
  umount -lf "$ROOT/sys" 2>/dev/null || true
}
trap cleanup_mounts EXIT

say "在 chroot 内安装运行时组件"
CHROOT_PKGS="openjdk-21-jre-headless openjdk-17-jre-headless curl wget unzip zip ufw python3 python3-pip ca-certificates xz-utils"
if [[ $WITH_DESKTOP -eq 1 ]]; then
  CHROOT_PKGS="$CHROOT_PKGS task-xfce-desktop python3-tk lightdm firefox-esr"
fi
chroot "$ROOT" /bin/bash -c "
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y $CHROOT_PKGS
apt-get install -y console-setup locales || true
sed -i 's/^# *\(en_US.UTF-8\|zh_CN.UTF-8\)/\1/' /etc/locale.gen || true
locale-gen en_US.UTF-8 zh_CN.UTF-8 || true
update-alternatives --set editor /usr/bin/nano || true
apt-get clean
rm -rf /var/lib/apt/lists/*
" || warn "chroot 内安装部分失败（可重试）"
ok "运行时组件安装完成"

say "复制面板程序到 /opt/mcpanel"
mkdir -p "$ROOT/opt/mcpanel"
rsync -a "$SRC_DIR/mcpanel" "$SRC_DIR/webui" "$ROOT/opt/mcpanel/"
mkdir -p "$ROOT/srv/minecraft" "$ROOT/var/lib/mcpanel"
ok "面板已内置"

say "应用 overlay 配置"
if command -v rsync >/dev/null; then
  rsync -a "$SRC_DIR/build/overlay/" "$ROOT/"
else
  cp -r "$SRC_DIR/build/overlay/"* "$ROOT/"
fi
chmod +x "$ROOT/usr/local/bin/mcpanel" 2>/dev/null || true

say "配置系统服务与默认行为"
# 决定 root 口令：随机生成 / 指定 / 锁定。任何情况下都不写进代码或仓库。
if [[ "$NO_PW" == "1" ]]; then
  PW_MODE=lock
elif [[ -n "$ROOT_PW" ]]; then
  PW_MODE=set
else
  ROOT_PW="$(head -c 24 /dev/urandom | base64 | tr -d '/+=O0lI1' | head -c 20)"
  PW_MODE=random
fi
export PW_MODE ROOT_PW

chroot "$ROOT" /bin/bash -c "
set -e
case \"\$PW_MODE\" in
  lock)   passwd -l root ;;
  set|random)
    # 只写入哈希，不在任何文件/命令行里出现「用户名:口令」明文
    HASH=\$(ROOT_PW=\"\$ROOT_PW\" python3 -c 'import crypt,os;print(crypt.crypt(os.environ["ROOT_PW"], crypt.mksalt(crypt.METHOD_SHA512)))')
    usermod -p \"\$HASH\" root
    ;;
esac
# 面板开机自启
systemctl enable mcpanel.service
systemctl enable NetworkManager || true
systemctl enable ssh || true
systemctl enable ufw || true
# SSH 只允许密钥登录（不放行密码登录，避免任何口令成为攻击面）
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config || true
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config || true
# 开机控制台自动登录
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<'EOF2'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I \$TERM
EOF2
# 放行端口（首次启动由面板再确认一次）
ufw allow 8850/tcp || true
ufw allow 25565/tcp || true
ufw allow 19132/udp || true
echo 'mcpanelos' > /etc/hostname
" || warn "chroot 配置部分失败"
ok "服务配置完成"

say "写入 ISO 引导"
KVER=$(ls "$ROOT/boot/vmlinuz-"* 2>/dev/null | head -1 | sed 's#.*/vmlinuz-##')
INITRD=$(ls "$ROOT/boot/initrd.img-"* 2>/dev/null | head -1)
[[ -n "$KVER" && -n "$INITRD" ]] || die "未找到内核/initrd"
cp "$ROOT/boot/vmlinuz-$KVER" "$ISO_DIR/live/vmlinuz"
cp "$INITRD" "$ISO_DIR/live/initrd"
ok "内核 $KVER"

cat > "$ISO_DIR/boot/grub/grub.cfg" <<'EOF'
set default=0
set timeout=5
insmod part_gpt
insmod part_msdos
insmod fat
insmod ext2
insmod iso9660

menuentry "MCPanelOS（MC 服务器系统，Live 运行）" {
    linux /live/vmlinuz boot=live components quiet splash locales=zh_CN.UTF-8
    initrd /live/initrd
}
menuentry "MCPanelOS（安全模式 / 详细日志）" {
    linux /live/vmlinuz boot=live components noquiet locales=zh_CN.UTF-8
    initrd /live/initrd
}
menuentry "MCPanelOS（持久化：保存存档到 U 盘）" {
    linux /live/vmlinuz boot=live components persistence locales=zh_CN.UTF-8
    initrd /live/initrd
}
EOF

if [[ $MAKE_ISO -eq 1 ]]; then
  say "打包 squashfs（耗时几分钟）"
  mksquashfs "$ROOT" "$SQUASH" -e boot -comp xz -processors "$(nproc)" || die "squashfs 打包失败"
  ok "已生成 filesystem.squashfs（$(du -h "$SQUASH" | cut -f1)）"

  say "生成可启动 ISO"
  OUT="$SRC_DIR/${OUT_NAME}-${SUITE}-${ARCH}.hybrid.iso"
  grub-mkrescue -o "$OUT" "$ISO_DIR" --volid "MCPANELOS" || die "ISO 生成失败"
  ok "ISO 完成：$OUT（$(du -h "$OUT" | cut -f1)）"
  echo
  echo "  写入 U 盘： sudo dd if=$OUT of=/dev/sdX bs=4M status=progress && sync"
  echo "  或用 Ventoy / Rufus(DD模式) 直接烧录"
else
  say "跳过 ISO，导出 rootfs 归档"
  OUT_TAR="$SRC_DIR/${OUT_NAME}-rootfs.tar.gz"
  tar -C "$ROOT" -czf "$OUT_TAR" . && ok "已导出 $OUT_TAR"
fi

say "完成"
case "$PW_MODE" in
  random)
    echo "  🔑 root 密码（随机生成，仅显示这一次，请自行记好）："
    echo "     $ROOT_PW"
    echo "     它就是本机唯一的口令，只在本机控制台生效；SSH 已关闭密码登录。"
    ;;
  lock)
    echo "  🔒 root 密码已锁定：控制台自动登录，SSH 仅允许密钥。"
    echo "     需要口令时启动后执行 passwd 自行设置。"
    ;;
  set)
    echo "  🔑 已使用你通过 --password 指定的 root 密码（请勿把它提交到任何仓库）。"
    ;;
esac
