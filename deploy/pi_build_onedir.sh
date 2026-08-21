#!/usr/bin/env bash
# pi_build_onedir.sh — 在树莓派(aarch64/ARM)本机打包 onedir 单文件夹可执行程序并配置开机自启
#
# 为什么需要它:
#   PyInstaller 不可交叉编译, 开发机(x86_64)打的 onedir 包无法在树莓派(ARM)运行。
#   必须在树莓派本机用 pyinstaller 重新打包, 才能得到树莓派可运行的版本。
#
# 前置: 源码已推送到树莓派(见 push_source_to_pi.sh), 本脚本位于源码根目录同级或任意位置。
#
# 用法(在树莓派上):
#   SUDO_PASS=密码 bash pi_build_onedir.sh [源码目录] [开机自启 on|off]
#
# 默认:
#   源码目录 = $HOME/eol_source
#   开机自启 = on
#
# 会生成: <源码目录>/deploy/dist/EOL_CIT/  (aarch64 可执行文件)
# 并配置 systemd 服务 eol-cit.service 开机自启。
#
set -euo pipefail

SOURCE="${1:-$HOME/eol_source}"
AUTOSTART="${2:-on}"
LOGIN_USER="${USER:-$(whoami)}"
SUDO_PASS="${SUDO_PASS:-}"
SERVICE=eol-cit.service
MAIN="${SOURCE}/main.py"

sudo_run() {
    if [ -n "$SUDO_PASS" ]; then
        echo "$SUDO_PASS" | sudo -S "$@"
    else
        sudo "$@"
    fi
}

if [ ! -f "$MAIN" ]; then
    echo "[ERROR] 源码目录缺少 main.py: $SOURCE" >&2
    exit 1
fi

# ---------- 1. 安装构建与运行依赖 ----------
echo "[INFO] 安装系统依赖 (python3-pyqt5, pip, 构建工具) ..."
sudo_run apt-get update -y
sudo_run apt-get install -y python3 python3-pip python3-pyqt5 python3-dev build-essential 2>/dev/null || \
    sudo_run apt-get install -y python3 python3-pip python3-pyqt5

echo "[INFO] 安装 pyinstaller 与运行依赖 ..."
# Debian/树莓派OS 默认启用 PEP668 (externally-managed-environment), pip 需加
# --break-system-packages 才能装 pyinstaller 到系统环境。
/usr/bin/python3 -m pip install --quiet --break-system-packages --upgrade pip
/usr/bin/python3 -m pip install --quiet --break-system-packages pyinstaller
/usr/bin/python3 -m pip install --quiet --break-system-packages -r "${SOURCE}/requirements.txt" || \
    echo "[WARN] requirements 部分安装失败, 继续打包(源码模式受影响, onedir 打包不受影响)"

# ---------- 2. 在树莓派本机打包 onedir ----------
BUILD_SCRIPT="${SOURCE}/deploy/build_cit_onedir.py"
if [ ! -f "$BUILD_SCRIPT" ]; then
    echo "[ERROR] 找不到构建脚本: $BUILD_SCRIPT" >&2
    exit 1
fi

echo "[INFO] 树莓派本机打包 onedir ..."
cd "${SOURCE}/deploy"
QT_QPA_PLATFORM=offscreen /usr/bin/python3 "$BUILD_SCRIPT" "$SOURCE" "${SOURCE}/deploy/dist"

APP_DIR="${SOURCE}/deploy/dist/EOL_CIT"
EXE="${APP_DIR}/EOL_CIT"
if [ ! -f "$EXE" ]; then
    echo "[ERROR] 打包失败, 未生成可执行文件: $EXE" >&2
    exit 1
fi
chmod +x "$EXE"

echo "[OK] onedir 打包完成:"
echo "  架构: $(uname -m)"
echo "  目录: $APP_DIR"
echo "  可执行文件: $EXE"

# ---------- 3. 开机自启 ----------
if [ "$AUTOSTART" = "on" ]; then
    SVCE_FILE=/etc/systemd/system/$SERVICE
    if [ -f "$SVCE_FILE" ]; then
        sudo_run cp "$SVCE_FILE" "$SVCE_FILE.bak-$(date +%Y%m%d-%H%M%S)"
    fi
    echo "[INFO] 配置开机自启服务: $SERVICE"
    TMPFILE="$(mktemp)"
    cat > "$TMPFILE" <<SVCE
[Unit]
Description=EOL CIT Test Management (aarch64 onedir)
After=graphical.target network.target
Wants=graphical.target

[Service]
Type=simple
User=$LOGIN_USER
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/\$(id -u)
WorkingDirectory=$APP_DIR
ExecStart=$EXE
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
SVCE
    sudo_run cp "$TMPFILE" "$SVCE_FILE"
    rm -f "$TMPFILE"
    sudo_run systemctl daemon-reload
    sudo_run systemctl enable "$SERVICE" >/dev/null 2>&1
    sudo_run systemctl restart "$SERVICE" || true
    sleep 4
    echo "[INFO] 运行状态: $(systemctl is-active $SERVICE 2>/dev/null || echo unknown)"
else
    echo "[INFO] 未开启开机自启"
fi

echo ""
echo "==================== 部署信息 ===================="
echo "安装位置 : $APP_DIR"
echo "启动命令 : $EXE"
echo "架构     : $(uname -m)"
if [ "$AUTOSTART" = "on" ]; then
    echo "开机自启 : 已开启 (systemctl status $SERVICE)"
fi
echo "手动启动 : $EXE"
echo "=================================================="
