#!/usr/bin/env bash
# push_to_pi.sh — 从开发机推送 CIT 部署包到树莓派并触发部署(含开机自启)
#
# 支持两种部署包:
#   A. 源码包   : deploy/EOL_CIT.tar.gz (由 build_cit_package.py 生成, 目标需 Python/PyQt5)
#   B. onedir包 : deploy/dist/EOL_CIT   (由 build_cit_onedir.py 生成, 自带 PyQt5, 无需 Python)
#
# 用法:
#   ./push_to_pi.sh                          # 默认推送源码包
#   ./push_to_pi.sh IP USER PASS [on|off]
#   ./push_to_pi.sh IP USER PASS [on|off] /path/to/EOL_CIT(目录或tar.gz)
#
# 默认值:
#   IP=10.5.35.49  USER=pi  PASSWORD=raspberry  AUTOSTART=on
#
# 依赖: sshpass (安装: sudo apt install sshpass)
#
set -euo pipefail

IP="${1:-10.5.35.49}"
USER="${2:-pi}"
PASSWORD="${3:-raspberry}"
AUTOSTART="${4:-on}"
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_SCRIPT="$BASE_DIR/deploy_on_pi.sh"
REMOTE_PKG="/tmp/EOL_CIT.tar.gz"
TMP_TAR=""

# 第 5 个参数指定部署源(目录=onedir文件夹 / 文件=tar.gz)
SRC="${5:-$BASE_DIR/EOL_CIT.tar.gz}"

if [ -d "$SRC" ]; then
    # onedir 文件夹 -> 打成 tar.gz
    TMP_TAR="$(mktemp --suffix=.tar.gz)"
    echo "[INFO] 打包 onedir 文件夹: $SRC -> $TMP_TAR"
    tar -czf "$TMP_TAR" -C "$(dirname "$SRC")" "$(basename "$SRC")"
    PKG="$TMP_TAR"
elif [ -f "$SRC" ]; then
    PKG="$SRC"
else
    echo "[ERROR] 部署源不存在: $SRC (请先 run build_cit_package.py 或 build_cit_onedir.py)" >&2
    exit 1
fi

trap 'rm -f "$TMP_TAR"' EXIT

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=15)

if ! command -v sshpass >/dev/null 2>&1; then
    echo "[ERROR] 未找到 sshpass, 请先安装: sudo apt install sshpass" >&2
    exit 1
fi

echo "[INFO] 目标主机: $USER@$IP  开机自启: $AUTOSTART"
echo "[INFO] 推送安装包: $PKG"
sshpass -p "$PASSWORD" scp "${SSH_OPTS[@]}" "$PKG" "$USER@$IP:$REMOTE_PKG"
echo "[INFO] 推送部署脚本: $DEPLOY_SCRIPT"
sshpass -p "$PASSWORD" scp "${SSH_OPTS[@]}" "$DEPLOY_SCRIPT" "$USER@$IP:/tmp/deploy_on_pi.sh"

echo "[INFO] 在远程主机上执行部署 ..."
sshpass -p "$PASSWORD" ssh "${SSH_OPTS[@]}" "$USER@$IP" \
    "SUDO_PASS='$PASSWORD' bash /tmp/deploy_on_pi.sh $REMOTE_PKG \"$HOME/eol_cit\" $AUTOSTART"

echo "[OK] 推送+部署完成"
