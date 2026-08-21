#!/usr/bin/env bash
# deploy_on_pi.sh — 在树莓派(Linux/Ubuntu)上安装"仅 CIT 接口测试"部署包并开启自启动
#
# 功能:
#   1. 解压安装包到目标目录(旧版自动备份)
#   2. 创建虚拟环境并安装依赖(PyQt5/PyYAML/requests/paramiko)
#   3. 配置 systemd 服务(开机自启, 图形会话)
#   4. 启动服务并验证状态
#
# 用法(在树上执行):
#   bash deploy_on_pi.sh [安装包路径] [目标目录] [开机自启 on|off]
#
# 默认值:
#   安装包 = /tmp/EOL_CIT.tar.gz
#   目标目录 = $HOME/eol_cit
#   开机自启 = on
#
# 说明: 需要 sudo 权限, 可用环境变量 SUDO_PASS 传密码(自动 sudo), 否则交互输入。
#
set -euo pipefail

PKG="${1:-/tmp/EOL_CIT.tar.gz}"
TARGET="${2:-$HOME/eol_cit}"
AUTOSTART="${3:-on}"
LOGIN_USER="${USER:-$(whoami)}"
SUDO_PASS="${SUDO_PASS:-}"
SERVICE=eol-cit.service

# 展开 ~ 并规范为绝对路径(systemd unit 文件里 ~ / 相对路径无效)
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
    PYTHON="${TARGET}/venv/bin/python3"
    if [ ! -x "$PYTHON" ]; then
        echo "[INFO] 创建虚拟环境 venv ..."
        if ! /usr/bin/python3 -m venv "$TARGET/venv" 2>/dev/null; then
            echo "[WARN] venv 创建失败(请安装 python3-venv), 将使用系统 Python"
        fi
        [ -x "$PYTHON" ] || PYTHON=/usr/bin/python3
    fi
    echo "[INFO] 使用解释器: $PYTHON"
    if [ "$PYTHON" = "/usr/bin/python3" ]; then
        echo "[WARN] 未创建 venv(请安装 python3-venv), 将使用系统 Python 与已有模块"
    fi
    echo "[INFO] 安装依赖 (PyQt5 PyYAML requests paramiko) ..."
    "$PYTHON" -m pip install -q --upgrade pip 2>/dev/null || true
    "$PYTHON" -m pip install -q -r "$TARGET/requirements.txt" 2>/dev/null || {
        echo "[WARN] requirements 安装失败, 尝试逐个安装...";
        for m in PyQt5 PyYAML requests paramiko; do
            "$PYTHON" -m pip install -q "$m" 2>/dev/null || echo "[WARN] $m 安装失败, 请检查网络/PyQt5 系统包: sudo apt install python3-pyqt5"
        done
    }
    RUNCMD="$PYTHON $TARGET/main.py"

    # 冒烟测试
    echo "[INFO] 冒烟测试: 校验模块导入 ..."
    if ! QT_QPA_PLATFORM=offscreen DISPLAY= "$PYTHON" - <<'PY'
import sys
sys.path.insert(0, ".")
from app.core import paths
print("[OK] 路径初始化正常")
print("[OK] 冒烟测试通过")
PY
    then
        echo "[WARN] 冒烟测试未通过，仍继续安装服务"
    fi
else
    echo "[INFO] onedir 可执行文件: $EXE"
    RUNCMD="$EXE"
fi

# ---------- 4. systemd 服务(开机自启) ----------
SVCE_FILE=/etc/systemd/system/$SERVICE

if [ "$AUTOSTART" = "on" ]; then
    if [ -f "$SVCE_FILE" ]; then
        echo "[INFO] 备份旧服务文件: $SVCE_FILE.bak-$(date +%Y%m%d-%H%M%S)"
        sudo_run cp "$SVCE_FILE" "$SVCE_FILE.bak-$(date +%Y%m%d-%H%M%S)"
    fi

    echo "[INFO] 生成服务文件: $SVCE_FILE"
    TMPFILE="$(mktemp)"
    cat > "$TMPFILE" <<SVCE
[Unit]
Description=EOL CIT Test Management
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

    echo "[INFO] 启用开机自启 ..."
    sudo_run systemctl daemon-reload
    sudo_run systemctl enable "$SERVICE" >/dev/null 2>&1
    echo "[INFO] 启动服务 ..."
    sudo_run systemctl restart "$SERVICE" || true
    sleep 5
else
    echo "[INFO] 关闭开机自启 ..."
    sudo_run systemctl disable "$SERVICE" >/dev/null 2>&1 || true
    sudo_run systemctl stop "$SERVICE" >/dev/null 2>&1 || true
    sudo_run rm -f "$SVCE_FILE" || true
    sudo_run systemctl daemon-reload || true
    echo "[INFO] 未开启开机自启，只安装程序。"
fi

# ---------- 5. 验证 ----------
echo "[INFO] 服务状态:"
if systemctl list-unit-files | grep -q "$SERVICE"; then
    echo "  is-active : $(systemctl is-active $SERVICE)"
    echo "  is-enabled: $(systemctl is-enabled $SERVICE)"
fi
ps aux | grep -E "[m]ain.py|[E]OL_CIT" || true

echo "[OK] 部署完成"
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
