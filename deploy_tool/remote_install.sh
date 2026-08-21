#!/usr/bin/env bash
# remote_install.sh — 在远程 Linux/Ubuntu 主机上安装部署包(可开启开机自启)
#
# 支持两种部署包:
#   A. 源码包   : 内含 main.py(需 venv/Python 环境)
#   B. onedir包 : 内含可执行文件(如 EOL_CIT, 自带 PyQt5, 无需 Python)
#
# 该脚本会被上传到远程主机并自动执行。功能:
#   1. 解压安装包到目标目录(旧版自动备份)
#   2. 若是源码包: 创建 venv 并安装依赖; 若是 onedir: 直接用可执行文件
#   3. 可选: 配置 systemd 服务实现开机自启
#   4. 启动程序并验证
#
# 用法(远程执行):
#   bash remote_install.sh <安装包路径> [目标目录] [开机自启 on|off]
#
# 说明: 需要 sudo(仅配置开机自启时需要), 可用 SUDO_PASS 传密码。
#
set -euo pipefail

PKG="${1:?用法: bash remote_install.sh <安装包路径> [目标目录] [on|off]}"
TARGET="${2:-$HOME/deployed_app}"
AUTOSTART="${3:-off}"
LOGIN_USER="${USER:-$(whoami)}"
SUDO_PASS="${SUDO_PASS:-}"
SERVICE=remote-app.service

# 展开 ~ 并规范为绝对路径(关键: systemd unit 文件里 ~ 和相对路径无效,
# 会报 "bad unit file setting", 必须用绝对路径)
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

if [ ! -f "$PKG" ]; then
    echo "[ERROR] 安装包不存在: $PKG" >&2
    exit 1
fi

echo "[INFO] 安装包: $PKG"
echo "[INFO] 目标目录: $TARGET"
echo "[INFO] 开机自启: $AUTOSTART"

# ---------- 1. 解压(旧版自动备份) ----------
if [ -d "$TARGET" ]; then
    BAK="${TARGET}.bak-$(date +%Y%m%d-%H%M%S)"
    echo "[INFO] 检测到旧版本, 备份到: $BAK"
    mv "$TARGET" "$BAK"
fi
mkdir -p "$TARGET"
echo "[INFO] 解压安装包 ..."
tar -xzf "$PKG" -C "$TARGET" --strip-components=1
chmod +x "$TARGET/main.py" 2>/dev/null || true

# ---------- 2. 识别部署包类型 & 启动命令 ----------
# onedir 形式: 找到可执行文件
EXE=""
if [ -f "$TARGET/main.py" ]; then
    STARTUP=$TARGET/main.py
    PYSOURCEMODE=1
elif [ -x "$TARGET/EOL_CIT" ] || [ -f "$TARGET/EOL_CIT" ]; then
    EXE=$TARGET/EOL_CIT
    chmod +x "$EXE"
    STARTUP=$EXE
    PYSOURCEMODE=0
else
    # 在子目录中查找可执行文件(如 EOL_CIT/EOL_CIT)
    FOUND=$(find "$TARGET" -maxdepth 2 -type f -name 'EOL_CIT' -perm -u+x 2>/dev/null | head -1)
    if [ -n "$FOUND" ]; then
        EXE=$FOUND
        STARTUP=$EXE
        PYSOURCEMODE=0
    else
        echo "[ERROR] 无法识别的部署包(缺少 main.py 或可执行文件)" >&2
        exit 1
    fi
fi

if [ "$PYSOURCEMODE" = "1" ]; then
    # 源码模式: 创建 venv + 装依赖
    PYTHON="${TARGET}/venv/bin/python3"
    if [ ! -x "$PYTHON" ]; then
        echo "[INFO] 创建虚拟环境 venv ..."
        if ! /usr/bin/python3 -m venv "$TARGET/venv" 2>/dev/null; then
            echo "[WARN] venv 创建失败(请安装 python3-venv), 将使用系统 Python"
        fi
        [ -x "$PYTHON" ] || PYTHON=/usr/bin/python3
    fi
    echo "[INFO] 使用解释器: $PYTHON"
    RUNCMD="$PYTHON $TARGET/main.py"
    if [ -f "$TARGET/requirements.txt" ]; then
        "$PYTHON" -m pip install -q --upgrade pip 2>/dev/null || true
        "$PYTHON" -m pip install -q -r "$TARGET/requirements.txt" 2>/dev/null || \
            echo "[WARN] requirements 安装失败，请检查网络 / 依赖(或安装 python3-venv)"
    fi
else
    # onedir 模式: 直接用可执行文件
    echo "[INFO] onedir 可执行文件: $EXE"
    RUNCMD="$EXE"
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
Description=Deployed Application
After=graphical.target network.target
Wants=graphical.target

[Service]
Type=simple
User=$LOGIN_USER
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/\$(id -u)
WorkingDirectory=$TARGET
ExecStart=$RUNCMD
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
    echo "[INFO] 已开启开机自启"
else
    echo "[INFO] 未开启开机自启"
fi

echo "[OK] 部署完成: $TARGET"
echo ""
echo "==================== 部署信息 ===================="
echo "安装位置 : $TARGET"
echo "启动命令 : $RUNCMD"
if [ "$AUTOSTART" = "on" ]; then
    echo "开机自启 : 已开启 (systemctl status $SERVICE)"
    echo "运行状态 : $(systemctl is-active $SERVICE 2>/dev/null || echo unknown)"
fi
echo "手动启动 : $RUNCMD"
echo "=================================================="
