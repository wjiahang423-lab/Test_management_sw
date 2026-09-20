#!/usr/bin/env bash
# install_eol_tablet.sh — 把 onedir 包(dist/EOL)部署到工业平板(10.5.33.219)并配置 eol.service 开机自启
#
# 功能:
#   1. 打包本地 dist/EOL 推到设备 -> /home/user/eol/ (旧版自动备份)
#   2. 在设备上生成 systemd 服务文件 /etc/systemd/system/eol.service
#   3. 开启开机自启并立即启动
#   4. 验证服务状态
#
# 用法:
#   ./install_eol_tablet.sh                     # 全部使用下方默认值
#   ./install_eol_tablet.sh [IP] [USER] [PASS] [on|off] [源目录]
#
# 依赖: sshpass (sudo apt install sshpass)
set -euo pipefail

# ==================== 配置区(修改这里) ====================
REMOTE_IP="10.5.33.219"        # 远程设备 IP
REMOTE_USER="user"             # 远程设备用户名
REMOTE_PASS="111111"           # 远程设备密码
AUTOSTART="on"                 # 开机自启: on|off
SRC_DIR="$HOME/Test_management_sw/dist/EOL"   # 本地 onedir 包路径
REMOTE_BASE="/home/user/eol"   # 设备上安装目录
SERVICE="eol.service"          # systemd 服务名
# =========================================================

# 命令行可覆盖默认值
[ $# -ge 1 ] && REMOTE_IP="$1"
[ $# -ge 2 ] && REMOTE_USER="$2"
[ $# -ge 3 ] && REMOTE_PASS="$3"
[ $# -ge 4 ] && AUTOSTART="$4"
[ $# -ge 5 ] && SRC_DIR="$5"

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=15)

ssh_run() {
    sshpass -p "$REMOTE_PASS" ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$REMOTE_IP" "$@"
}

if [ ! -d "$SRC_DIR" ]; then
    echo "[ERROR] onedir 包不存在: $SRC_DIR (请先运行: python3 build_app.py)" >&2
    exit 1
fi
if ! command -v sshpass >/dev/null 2>&1; then
    echo "[ERROR] 未找到 sshpass, 请先安装: sudo apt install sshpass" >&2
    exit 1
fi

echo "[INFO] 目标设备: $REMOTE_USER@$REMOTE_IP  开机自启: $AUTOSTART"
echo "[INFO] 本地包:   $SRC_DIR"
echo "[INFO] 安装目录: $REMOTE_BASE"

# ---------- 1. 推送 onedir 包到设备 ----------
echo "[INFO] 打包并推送: tar.gz -> $REMOTE_BASE ..."
ssh_run "mkdir -p /tmp/eol_deploy && if [ -d '$REMOTE_BASE' ]; then mv '$REMOTE_BASE' '$REMOTE_BASE.bak-\$(date +%Y%m%d-%H%M%S)' && echo '[INFO] 旧版本已备份'; fi"
tar -czf - -C "$(dirname "$SRC_DIR")" "$(basename "$SRC_DIR")" | sshpass -p "$REMOTE_PASS" ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$REMOTE_IP" \
    "tar -xzf - -C /tmp/eol_deploy && mv /tmp/eol_deploy/$(basename "$SRC_DIR") '$REMOTE_BASE' && rm -rf /tmp/eol_deploy"

# ---------- 2. 生成 systemd 服务文件 ----------
if [ "$AUTOSTART" = "on" ]; then
    echo "[INFO] 生成服务文件: /etc/systemd/system/$SERVICE"
    ssh_run "
        set -e
        LOGIN_USER='$(id -un)'
        cat > /tmp/$SERVICE <<SVCE
[Unit]
Description=EOL Test Management
After=graphical.target network.target
Wants=graphical.target

[Service]
Type=simple
User=$LOGIN_USER
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/\$(id -u)
WorkingDirectory=$REMOTE_BASE
ExecStart=$REMOTE_BASE/$(basename "$SRC_DIR")
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
SVCE
        echo '$REMOTE_PASS' | sudo -S cp /tmp/$SERVICE /etc/systemd/system/$SERVICE
        rm -f /tmp/$SERVICE
        echo '$REMOTE_PASS' | sudo -S systemctl daemon-reload
        echo '$REMOTE_PASS' | sudo -S systemctl enable $SERVICE >/dev/null 2>&1
        echo '$REMOTE_PASS' | sudo -S systemctl restart $SERVICE
        sleep 4
    "
else
    echo "[INFO] 关闭开机自启 $SERVICE ..."
    ssh_run "
        echo '$REMOTE_PASS' | sudo -S systemctl disable $SERVICE >/dev/null 2>&1 || true
        echo '$REMOTE_PASS' | sudo -S systemctl stop $SERVICE >/dev/null 2>&1 || true
        echo '$REMOTE_PASS' | sudo -S rm -f /etc/systemd/system/$SERVICE || true
        echo '$REMOTE_PASS' | sudo -S systemctl daemon-reload || true
    "
fi

# ---------- 3. 验证 ----------
echo "[INFO] 设备上服务状态:"
ssh_run "
    echo '  is-active : \$(systemctl is-active $SERVICE 2>/dev/null || echo unknown)'
    echo '  is-enabled: \$(systemctl is-enabled $SERVICE 2>/dev/null || echo unknown)'
    ps aux | grep -E '[E]OL' || true
"

echo "[OK] 部署完成: $REMOTE_USER@$REMOTE_IP ($REMOTE_BASE)"
echo "手动启动: $REMOTE_BASE/$(basename "$SRC_DIR")"