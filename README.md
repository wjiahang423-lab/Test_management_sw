# Test Management / EOL 测试软件

机器人 EOL 测试用例管理与执行软件（PyQt5）。

- 源码入口：`main.py`
- 打包入口：`python3 build_app.py` → `dist/EOL/`
- 计划目录：`data/plans/`
- 图片目录：`data/images/`
- 设备部署与更新说明：本文；树莓派/CIT 部署另见 `deploy/README.md`
- 发布包：`dist/EOL/`（含 `docs/`、`RELEASE_NOTES.md`、头部/腰部计划与脚本）

## 0. 发布包内容（2026-10-29）

打包后 `dist/EOL/` 已包含：

| 内容 | 路径 |
|------|------|
| 头部计划（完整） | `data/plans/头部分装线测试.plan` |
| 头部计划（2026-10-28 变体，关节测试默认跳过） | `data/plans/头部分装线测试20261028.plan` |
| 腰部计划 | `data/plans/腰部分装线测试.plan` |
| 头部脚本 | `_internal/scripts/eyou_head/` |
| 腰部脚本 | `_internal/scripts/Tongchun_waist/` |
| 图片素材 | `data/images/eyoushow.jpg`、`tongchuan1.jpg`、`tongchuan2.jpg` |
| 文档 | `docs/`、`RELEASE_NOTES.md` |

打包命令：

```bash
python3 build_app.py
cd dist && zip -r EOL_$(date +%Y%m%d).zip EOL
```

`build_app.py` 会自动复制计划、图片、文档，并自检头部/腰部 PLAN、脚本、图片是否齐全。

## 1. 现场设备信息

| 项目 | 值 |
|------|----|
| 设备 IP | `10.5.38.87` |
| 系统 | Ubuntu 22.04 x86_64 触摸屏 |
| SSH 用户 | `user`（密码按现场记录输入） |
| 安装目录 | `/home/user/eol` |
| 数据目录 | `/home/user/eol/data` |
| 可执行文件 | `/home/user/eol/EOL` |
| 自启方式 | tty1 自动登录 → `~/.kiosk/xinitrc` → `/home/user/eol/EOL` |

设备上的目录结构：

```
/home/user/eol/
├── EOL                  # 可执行文件
├── _internal/           # 打包资源（只读）
│   ├── scripts/         # 测试脚本
│   ├── library/         # ZLG 库
│   ├── UI/              # 界面文件
│   └── scripts/eyou_head/USB_CAN_FD_Driver/  # 创芯 CANFD 库
└── data/                # 运行数据（升级时保留）
    ├── plans/           # 测试计划
    ├── images/          # 图片
    ├── logs/            # 日志
    ├── reports/         # 报告
    ├── settings.json
    └── users.json
```

## 2. 日常更新

> 更新前先确认软件当前没有正在执行的测试。若计划已在软件中打开，更新文件后要重新打开计划，避免旧内存覆盖磁盘文件。

### 2.1 只更新测试计划（最常用）

在开发机项目根目录执行：

```bash
scp "data/plans/头部分装线测试.plan" \
    "data/plans/头部分装线测试20261028.plan" \
    "data/plans/腰部分装线测试.plan" \
    user@10.5.38.87:/home/user/eol/data/plans/
```

更新后在设备上重新打开对应计划即可。头部/腰部计划已包含：

- 序列 `block_number`：如 `10001` / `10002` / `10003`
- SN 用例：`scripts/scan_sn.py :: scan_sn`
- 头部“设置扭矩模式”用例保持 `skip=true`（0x6060 子索引 4 设备不支持）
- 腰部使用 `scripts/Tongchun_waist/tongchuan_test.py`，节点 0/1，EtherCAT 网卡 `enp2s0`

### 2.2 只更新脚本

```bash
scp scripts/eyou_head/eyou_test.py scripts/eyou_head/eyou_driver.py \
    user@10.5.38.87:/home/user/eol/_internal/scripts/eyou_head/

scp scripts/Tongchun_waist/tongchuan_test.py scripts/Tongchun_waist/tongchuan_driver.py \
    user@10.5.38.87:/home/user/eol/_internal/scripts/Tongchun_waist/

scp scripts/set_picture_show.py \
    user@10.5.38.87:/home/user/eol/_internal/scripts/

scp scripts/scan_sn.py \
    user@10.5.38.87:/home/user/eol/_internal/scripts/
```

### 2.3 只更新图片

```bash
scp data/images/eyoushow.jpg \
    data/images/tongchuan1.jpg \
    data/images/tongchuan2.jpg \
    user@10.5.38.87:/home/user/eol/data/images/
```

### 2.4 只更新创芯 CANFD 库

```bash
scp scripts/eyou_head/USB_CAN_FD_Driver/libcontrolcanfd.so \
    user@10.5.38.87:/home/user/eol/_internal/scripts/eyou_head/USB_CAN_FD_Driver/
```

### 2.5 更新整个程序（代码改动后）

1）开发机重新打包：

```bash
cd /home/jyzn/Test_management_sw
python3 build_app.py
tar -czf /tmp/eol_app_update.tar.gz -C dist/EOL EOL _internal
scp /tmp/eol_app_update.tar.gz user@10.5.38.87:/tmp/
```

2）在设备上替换程序（保留 `data/`）：

```bash
ssh user@10.5.38.87

TAG=$(date +%Y%m%d-%H%M%S)
sudo systemctl stop getty@tty1          # 关闭 kiosk（SSH 不受影响）
mv ~/eol ~/eol.bak-$TAG                 # 备份旧版本
mkdir -p ~/eol
tar -xzf /tmp/eol_app_update.tar.gz -C ~/eol
cp -a ~/eol.bak-$TAG/data ~/eol/data    # 恢复现场数据
rm -f ~/eol/data/logs/.prev_crash
chmod +x ~/eol/EOL
sudo systemctl start getty@tty1         # 重新启动 kiosk
```

3）验证：

```bash
ps -ef | grep /home/user/eol/EOL | grep -v grep
systemctl is-active getty@tty1
lsusb | grep 04d8
```

## 3. 计划中的脚本路径规则（不用改路径）

计划里统一写相对路径，例如：

```json
"script": "scripts/eyou_head/eyou_test.py"
"script": "scripts/set_picture_show.py"
"script": "scripts/scan_sn.py"
```

打包运行时：

- `BASE_DIR = /home/user/eol`
- `BUNDLE_DIR = /home/user/eol/_internal`
- 查 `scripts/xxx.py` 的顺序：先 `BASE_DIR/scripts/xxx.py`，找不到再 `BUNDLE_DIR/scripts/xxx.py`

所以最终解析到：

```
/home/user/eol/_internal/scripts/xxx.py
```

结论：**计划从开发机拷到设备时，脚本路径不需要修改。**

例外是图片：计划里的 `image_name` 只写文件名，解析到：

```
/home/user/eol/data/images/<image_name>
```

例如 `image_name = eyoushow.jpg` → `/home/user/eol/data/images/eyoushow.jpg`。

新增脚本时：放到 `~/eol/_internal/scripts/` 对应子目录，计划继续写 `scripts/...`。不建议写绝对路径。

## 4. USB-CAN（创芯）配置

设备识别为：

```
04d8:0053 Microchip Technology, Inc. USB CANFD DEBUG
```

首次部署或换 USB 口后，确认 udev 权限规则存在：

```bash
cat /etc/udev/rules.d/99-cx-usbcan.rules
```

内容应为：

```
SUBSYSTEM=="usb", ATTR{idVendor}=="04d8", ATTR{idProduct}=="0053", MODE="0666"
```

没有就创建并重新加载：

```bash
sudo tee /etc/udev/rules.d/99-cx-usbcan.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="04d8", ATTR{idProduct}=="0053", MODE="0666"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
ls -l /dev/bus/usb/*/*
```

头部驱动优先使用创芯 `libcontrolcanfd.so`，找不到设备时回退 ZLG。

## 5. Pop 弹窗设置

设备上：**系统设置 → Pop 弹窗设置**

| 设置 | 默认值 |
|------|--------|
| 弹窗宽度 | `840 px` |
| 弹窗高度 | `440 px` |
| 弹窗字体 | `30 px` |
| 弹窗背景色 | `#b8c0c4` |

改完点“保存设置”，写入 `data/settings.json`，无需重启。

## 6. 备份与回滚

升级时每次都会生成 `/home/user/eol.bak-<时间>`。回滚：

```bash
ssh user@10.5.38.87
TAG=<要回滚到的备份后缀>

sudo systemctl stop getty@tty1
mv ~/eol ~/eol.failed-$TAG
mv ~/eol.bak-$TAG ~/eol
sudo systemctl start getty@tty1
```

## 7. 注意事项

- 管理页的“执行”按钮是**调试模式**，不支持图片弹窗；正式测试请用“切换执行页面”后运行。
- 不要在调试模式跑完整头部计划：图片弹窗会被跳过，`标定0位`仍会执行并写入 `0x2262`。
- 头部 `设置扭矩模式` 当前代码写的是 `0x6060:4`（设备不存在该子索引），计划里已置为跳过。
- 两块电机 `0x2082` 读回为空，SN 改由 `scripts/scan_sn.py` 扫码获取。
- 计划更新后若软件已打开该计划，请重新打开，避免旧内存覆盖。
