# 🎮 MCPanelOS —— 开箱即用的 Minecraft 服务器系统

一套面向 **Debian** 的 MC 开服方案：图形面板 + 内置下载器 + 防火墙/端口映射 + 自启动 + NBT 修改器 + 自动 sudo 提权 + 日志分析 + 类 CI 流水线。
可以**直接装到你现有的 Debian**，也可以**打包成一张完整的可启动 Linux 光盘/U盘系统**。

> 你只需要会复制粘贴命令。装好后全程网页点鼠标。

---

## 一、最快上手（已有 Debian / Ubuntu）

```bash
# 1) 下载本项目（或把文件夹拷到机器上）
git clone <本仓库地址> mcpanel && cd mcpanel

# 2) 一键安装（自动装 Java、ufw、systemd 服务）
sudo bash install.sh

# 3) 浏览器打开（手机也能开）
#    http://<这台机器的IP>:8850/
```

想一步步跟着走（更推荐第一次用）：

```bash
mcpanel wizard        # 交互式开服向导：选版本 → 下载 → 放行端口 → 设自启 → 启动
mcpanel status        # 看状态
```

其它命令：

| 命令 | 作用 |
|---|---|
| `mcpanel serve` | 启动网页面板（`--port 8850 --open --daemon`） |
| `mcpanel desktop` | 打开桌面窗口（需要图形环境 + `python3-tk`） |
| `mcpanel deps` | 安装系统依赖 |
| `mcpanel sudo` | 配置自动提权（推荐写 sudoers，比存密码安全） |
| `mcpanel pipeline xxx.yml` | 命令行跑流水线 |
| `mcpanel nbt level.dat --set Data.LevelName=新名字` | 命令行改 NBT |
| `mcpanel log logs/latest.log` | 命令行分析日志 |
| `mcpanel service` | 把面板本身设成开机自启服务 |

---

## 二、制作「完整 Linux」（可启动 ISO / U 盘）

在**任意一台 Debian/Ubuntu 机器**上以 root 运行：

```bash
sudo bash build/build-iso.sh                 # 纯命令行版（推荐做服务器）
sudo bash build/build-iso.sh --with-desktop  # 带 Xfce 桌面，可直连显示器操作
```

产出 `mcpanelos-bookworm-amd64.hybrid.iso`（BIOS + UEFI 双启动）：

```bash
sudo dd if=mcpanelos-bookworm-amd64.hybrid.iso of=/dev/sdX bs=4M status=progress && sync
# /dev/sdX 换成你的 U 盘（用 lsblk 看），也可以用 Ventoy / Rufus(DD 模式)
```

U 盘启动后：

- 控制台**自动登录 root，无需密码**；SSH 默认只接受密钥登录
- 构建时不会写入任何写死的口令：默认每次随机生成并只打印一次，也可用 `--no-password` 直接锁定
- 屏幕会直接打印面板地址：`http://<IP>:8850/`
- MC 面板已设为开机自启（systemd），世界数据放 `/srv/minecraft`
- 已预装 OpenJDK 21/17、ufw、curl/unzip、ssh（可远程维护）
- 想让存档跟着 U 盘走：GRUB 里选 **persistence** 启动项

> 构建脚本需要 `debootstrap / squashfs-tools / xorriso / grub`，脚本会自动装。

---

## 三、功能一览

| 模块 | 能力 |
|---|---|
| **启动器** | 多实例管理；Java 版（Vanilla/Paper/Purpur/Fabric/Forge/NeoForge）+ 基岩版 BDS；内存/JVM 参数；崩溃自动重启 |
| **内置下载器** | 官方接口拉版本清单，自动选最新稳定版，断点信息、进度条、SHA1/SHA256 校验 |
| **防火墙面板** | ufw / firewalld / iptables / nft 统一操作；MC 端口一键放行；规则增删启停 |
| **联机/映射** | UPnP 自动端口映射；Tailscale 一键组网；frp 穿透配置生成；局域网/公网/虚拟网三种地址直接展示 |
| **自启动** | 生成并安装 systemd 单元（含 systemd 启停/状态），非 systemd 环境自动退回 `@reboot` 计划任务 |
| **NBT 修改器** | 纯标准库读写 NBT（gzip/zlib/未压缩）；改 LevelName、种子、游戏规则、任意路径；自动备份 .bak |
| **自动 root 密码** | 三种档位：已是 root → 免密 sudoers（推荐，只放行白名单命令）→ 加密保存密码自动喂给 `sudo -S` |
| **清理器** | 日志/崩溃报告/旧备份/旧 jar/面板缓存/apt/journal 全部可视化，先扫描预览再删 |
| **日志分析** | 崩溃、Log4j、OOM、Watchdog、插件加载失败、区块损坏等规则化识别，给中文处理建议；玩家与聊天统计 |
| **文件浏览器** | 列目录、在线编辑文本、上传下载、重命名、删、chmod，全路径防越界 |
| **CI 流水线** | YAML 流水线 + 简单命令脚本两种写法，内置 backup/start/stop/restart/firewall/nbt/sleep/notify 动作，步骤级日志与失败中断 |
| **图形界面** | 网页面板（手机/远程可开）+ 桌面窗口（Tkinter）双形态，数据互通 |

系统资源占用极低：核心只依赖 Python 3 标准库，没有 Flask/Node/数据库。

---

## 四、让另一台电脑连进来

面板「联机/映射」页会把三种地址直接算好：

1. **同一 Wi-Fi**：另一台电脑直接填 `局域网IP:25565`（最简单，先试这个）
2. **跨网络**：装 Tailscale（面板一键安装），两台机器登录同一账号，用 Tailscale 那列 IP
3. **有公网**：路由器开 UPnP 后点「自动映射」，或用 frp / playit.gg 穿透

基岩版端口是 **UDP 19132**，Java 版是 **TCP 25565**，别搞混。

---

## 五、安全建议

- 面板默认不设密码，**如果在公网/云服务器上跑，务必在「设置」里设登录密码**
- 提权优先用 `mcpanel sudo` → 选「写入免密 sudoers」，而不是保存明文 root 密码
- 服务端只装信任的插件/模组；日志分析页出现 🔴 时先备份再排查
- 定期用「每日维护」流水线做备份（默认保留 14 天）

---

## 六、目录结构

```
mcpanel/            Python 后端（零第三方依赖）
  util.py           路径/命令执行/提权/存储
  secret.py         sudo 密码加密保存 + sudoers 生成
  java.py           Java 检测与自动安装（apt → Adoptium 兜底）
  catalog.py        Vanilla/Paper/Purpur/Fabric/Forge/NeoForge/Bedrock 版本清单
  downloader.py     下载器与服务端安装
  instance.py       实例与进程（pty 控制台）
  nbt.py            NBT 读写修改
  firewall.py       防火墙
  autostart.py      systemd 自启动
  net.py            UPnP / Tailscale / frp / 连接信息
  cleaner.py        清理器
  loganalyzer.py    日志分析
  pipeline.py       YAML + 脚本流水线
  browser.py        文件浏览器
  server.py         HTTP API + 静态前端
  desktop.py        桌面窗口
webui/              网页面板（原生 HTML/CSS/JS）
install.sh          Debian 一键安装器
build/              ISO 构建脚本与 overlay
docs/              使用文档
```

## 七、常见问题

**Q：面板打不开？** 先 `systemctl status mcpanel`；再确认防火墙放行 8850（`ufw allow 8850/tcp`）。

**Q：下载服务端失败/超时？** 服务器在国外源，国内网络可能慢。可在「下载器」页手动粘贴下载地址，或用代理后重试。

**Q：基岩版起不来？** BDS 需要 `LD_LIBRARY_PATH=.`，面板已自动处理；另外它用 UDP 19132/19133，别只放行 TCP。

**Q：桌面窗口报错？** `sudo apt install python3-tk`，或直接用网页面板。

**Q：世界存档在哪？** `/srv/minecraft/<实例名>/world`（基岩版是 `worlds/`）。
