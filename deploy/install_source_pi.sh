#!/usr/bin/env bash
# install_source_pi.sh — 在树莓派(aarch64)上安装"仅 CIT 接口测试"源码包并配置开机自启
#
# 本脚本直接在树莓派本机运行, 用 apt 安装 PyQt5 等系统依赖(树莓派上最可靠),
# 无需 venv / pip 编译。也兼容普通 Ubuntu。
#
# 用法(在树莓派上):
#   SUDO_PASS=密码 bash install_source_pi.sh [TARGET目录] [开机自启 on|off]
#   # 若安装包已在 /tmp, 也可: bash install_source_pi.sh
#
# 前置: 源码已解压到 $1(默认 $HOME/eol_source), 本脚本与之同级或任意位置。
# 说明: 需要 sudo 装系统包; 用 SUDO_PASS 自动 sudo, 否则交互输入。
#
set -euo pipefail

TARGET="${1:-$HOME/eol_source}"
AUTOSTART="${2:-on}"
LOGIN_USER="${USER:-$(whoami)}"
SUDO_PASS="${SUDO_PASS:-}"
SERVICE=eol-cit.service

# 展开 ~ 并规范为绝对路径(systemd 里不能用 ~)
case "$TARGET" in
    "~"*) TARGET="$HOME${TARGET#\~}" ;;
esac
TARGET="$(realpath -m "$TARGET" 2>/dev/null || echo "$TARGET")"

sudo_run() {
    if [ -n "$SUDO_PASS" ]; then
        echo "$SUDO_PASS" | sudo -S "$@"
    else
        sudo "$@"
    fi
}

if [ ! -f "$TARGET/main.py" ]; then
    echo "[ERROR] 未找到 main.py: $TARGET (请先解压源码到该目录)" >&2
    exit 1
fi

echo "[INFO] 目标目录: $TARGET"
echo "[INFO] 开机自启: $AUTOSTART"

# ---------- 1. 安装系统依赖(apt, 树莓派最可靠方式) ----------
echo "[INFO] 安装系统依赖: PyQt5 / yaml / requests / paramiko (apt) ..."
sudo_run apt-get update -y
sudo_run apt-get install -y python3 \
    python3-pyqt5 \
    python3-yaml \
    python3-requests \
    python3-paramiko \
    python3-venv

# ---------- 2. 冒烟测试(确认可导入) ----------
echo "[INFO] 冒烟测试: 校验模块导入 ..."
if ! QT_QPA_PLATFORM=offscreen DISPLAY= /usr/bin/python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
from app.core import paths
print("[OK] paths ok:", paths.BASE_DIR)
' "$TARGET"; then
    echo "[WARN] 冒烟测试未通过, 仍继续配置服务"
fi

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
Description=EOL CIT Test Management (source)
After=graphical.target network.target
Wants=graphical.target

[Service]
Type=simple
User=$LOGIN_USER
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/\$(id -u)
WorkingDirectory=$TARGET
ExecStart=/usr/bin/python3 $TARGET/main.py
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
echo "安装位置 : $TARGET"
echo "启动命令 : /usr/bin/python3 $TARGET/main.py"
if [ "$AUTOSTART" = "on" ]; then
    echo "开机自启 : 已开启 (systemctl status $SERVICE)"
fi
echo "=================================================="
