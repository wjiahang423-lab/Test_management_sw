# 部署到树莓派 / 远程 Linux（仅 CIT 接口测试）

本目录用于将**测试管理系统（仅 CIT 接口测试）**部署到树莓派(aarch64)或远程
Linux/Ubuntu 主机，并配置开机自启动。

## ⚠️ 重要：onedir 包的架构问题

PyInstaller **不能跨架构交叉编译**。开发机是 **x86_64**，而树莓派是 **aarch64/ARM**。
因此**在开发机上打的 onedir 包无法在树莓派上运行**（会直接起不来）。

**正确做法：把源码推到树莓派，在树莓派本机打包/安装。**
（本目录的树莓派脚本已自动处理。）

## 目录文件

| 文件 | 作用 | 在哪运行 |
|------|------|----------|
| `build_cit_onedir.py` | 打包 onedir 单文件夹包（**在目标机同架构上运行**） | 目标机 |
| `build_cit_package.py` | 打包源码版 `EOL_CIT.tar.gz` | 开发机 |
| `push_source_to_pi.sh` | **推源码到树莓派并部署**（推荐的一键入口） | 开发机 |
| `pi_build_onedir.sh` | 在树莓派本机打包 aarch64 onedir + 开机自启 | 树莓派 |
| `install_source_pi.sh` | 在树莓派用 apt 装依赖、源码运行 + 开机自启 | 树莓派 |
| `deploy_on_pi.sh` | 通用远程安装（源码/onedir）、systemd 自启 | 远程主机 |
| `push_to_pi.sh` | 推送**预先打好的包**到远程主机并部署 | 开发机 |
| `README.md` | 本说明 | - |

## 精简包包含内容

```
主要代码   : main.py, app/, UI/, assets/, requirements.txt
CIT 脚本   : scripts/cit_api.py, cit_loop_run.yaml, cit_loop_describe.yaml
CIT 用例   : data/plans/CIT接口测试.plan
运行数据   : data/users.json            (admin/admin, 免登录直接进入)
```

## 树莓派部署（推荐，两种方式）

### 方式 A：源码 -> 在树莓派本机打包 onedir（免装 Python，自包含）

```bash
cd /home/jyzn/Test_management_sw/deploy
./push_source_to_pi.sh 10.5.35.49 wangbo 你的密码 on onedir
```

流程：推源码 -> 树莓派 apt 装 python3-pyqt5 + pyinstaller -> 树莓派本机打包
aarch64 的 `~/eol_source/deploy/dist/EOL_CIT` -> 配置 `eol-cit.service` 开机自启。
（首次需联网装依赖，约几分钟。）

### 方式 B：源码 + apt 依赖（不打包，直接源码跑）

```bash
cd /home/jyzn/Test_management_sw/deploy
./push_source_to_pi.sh 10.5.35.49 wangbo 你的密码 on source
```

流程：推源码 -> apt 装 python3-pyqt5/python3-yaml/python3-requests/python3-paramiko
-> 用系统 `/usr/bin/python3` 直接跑 `~/eol_source/main.py` -> 配置开机自启。

> 参数：`IP 用户 密码 [on|off] [onedir|source]`
> 依赖 `sshpass`：`sudo apt install sshpass`

## 验证（在树莓派上）

```bash
systemctl status eol-cit.service     # active (running)
systemctl is-enabled eol-cit.service # enabled
# 查看失败日志
journalctl -u eol-cit.service -n 50
# 手动启动测试
# onedir:  ~/eol_source/deploy/dist/EOL_CIT/EOL_CIT
# source:  /usr/bin/python3 ~/eol_source/main.py
```

## 开机自启说明

- 默认**开启开机自启**，通过 systemd 服务 `eol-cit.service` 实现，随图形会话启动。
- `data/users.json` 中 `require_login=false`，开机后**直接进入主界面**，无需登录。
- 关闭自启：部署脚本参数传 `off`。

## 常见问题

| 问题 | 解决 |
|------|------|
| onedir 启动不起来 | 架构不匹配！用 `push_source_to_pi.sh ... onedir` 在树莓派本机重新打包 |
| apt 装 PyQt5 失败 | `sudo apt update && sudo apt install python3-pyqt5` |
| 程序没窗口 | `journalctl -u eol-cit.service -n 50` 看报错 |
| 服务 failed / bad unit | 已改用绝对路径（本目录脚本已修复 systemd `~` 问题） |
