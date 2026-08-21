#!/usr/bin/env bash
# push_source_to_pi.sh — 把源码推到树莓派, 在树莓派本机按需部署
#
# 因为 PyInstaller 不可交叉编译: 开发机(x86)打的 onedir 不能跑在树莓派(ARM)。
# 必须把源码推到树莓派, 在树莓派本机处理。
#
# 两种模式:
#   onedir (默认): 在树莓派本机用 pyinstaller 打包 aarch64 的 onedir 单文件夹程序, 配置开机自启。
#   source       : 用 apt 装 PyQt5 等系统依赖, 直接以源码(+系统 python)运行, 配置开机自启。
#
# 用法:
#   ./push_source_to_pi.sh [IP] [USER] [PASSWORD] [开机自启 on|off] [mode: onedir|source]
#
# 默认值:
#   IP=10.5.35.49  USER=pi  PASSWORD=raspberry  AUTOSTART=on  MODE=onedir
#
# 依赖: sshpass (sudo apt install sshpass)
#
set -euo pipefail

IP="${1:-10.5.35.49}"
USER="${2:-pi}"
PASSWORD="${3:-raspberry}"
AUTOSTART="${4:-on}"
MODE="${5:-onedir}"
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="/home/jyzn/Test_management_sw"

case "$MODE" in
    onedir) PI_SCRIPT="pi_build_onedir.sh" ;;
    source) PI_SCRIPT="install_source_pi.sh" ;;
    *) echo "[ERROR] 未知 mode: $MODE (可选 onedir|source)" >&2; exit 1 ;;
esac
PI_HOME_SRC='$HOME/eol_source'

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=20)

if ! command -v sshpass >/dev/null 2>&1; then
    echo "[ERROR] 未找到 sshpass, 请先安装: sudo apt install sshpass" >&2
    exit 1
fi

echo "[INFO] 目标主机: $USER@$IP  模式=$MODE  开机自启=$AUTOSTART"

# ---------- 1. 打包源码(tar.gz), 排除大目录/缓存 ----------
echo "[INFO] 打包源码 ..."
TMP_TAR="$(mktemp --suffix=.tar.gz)"
trap 'rm -f "$TMP_TAR"' EXIT

tar --exclude='*/__pycache__' --exclude='*.pyc' \
    --exclude='venv' --exclude='.git' \
    --exclude='data/logs' --exclude='data/reports' \
    --exclude='deploy/dist' --exclude='deploy/build' \
    --exclude='deploy/EOL_CIT.tar.gz' \
    -czf "$TMP_TAR" -C "$(dirname "$SOURCE")" "$(basename "$SOURCE")"

# ---------- 2. 推送到树莓派并解压(旧目录自动备份) ----------
echo "[INFO] 推送源码到树莓派 ..."
sshpass -p "$PASSWORD" scp "${SSH_OPTS[@]}" "$TMP_TAR" "$USER@$IP:/tmp/eol_source.tar.gz"

sshpass -p "$PASSWORD" ssh "${SSH_OPTS[@]}" "$USER@$IP" '
    set -e
    if [ -d "$HOME/eol_source" ]; then
        mv "$HOME/eol_source" "$HOME/eol_source.bak-$(date +%Y%m%d-%H%M%S)"
    fi
    mkdir -p "$HOME/eol_source"
    tar -xzf /tmp/eol_source.tar.gz -C "$HOME/eol_source" --strip-components=1
'

# ---------- 3. 推送树莓派侧执行脚本 ----------
echo "[INFO] 推送执行脚本: $PI_SCRIPT"
sshpass -p "$PASSWORD" scp "${SSH_OPTS[@]}" "$BASE_DIR/$PI_SCRIPT" "$USER@$IP:/tmp/$PI_SCRIPT"

# ---------- 4. 在树莓派本机打包并配置自启 ----------
echo "[INFO] 在树莓派上执行 (onedir, 需联网装依赖, 可能几分钟) ..."
# 源码已解压到远端默认目录 ~/eol_source, 故不传源码路径, 让树莓派脚本用默认值
# (在远端本机展开 $HOME, 避免跨机转义问题)
sshpass -p "$PASSWORD" ssh "${SSH_OPTS[@]}" "$USER@$IP" \
    "SUDO_PASS='$PASSWORD' bash /tmp/$PI_SCRIPT '' $AUTOSTART"

echo "[OK] 树莓派部署完成 (mode=$MODE)"
