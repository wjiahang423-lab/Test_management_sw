# 远程部署上位机（PyQt5）

一个 PyQt5 上位机，用于把本机软件（源码包或 PyInstaller onedir 单文件夹包）
一键部署到一台或多台远程 Linux / Ubuntu 主机，支持配置**开机启动**。

## 功能

1. **选择部署源**：点“浏览…”选择本机软件文件夹（可以是项目源码目录，也可以是
   已打包好的 onedir 文件夹，如 `deploy/dist/EOL_CIT`）。也支持直接选 `.tar.gz` 安装包。
2. **多台远程设备**：输入远程 IP/用户名/密码，点击“+ 添加设备”可添加多台；
   可移除选中 / 清空。
3. **一键部署**：对每台设备执行 打包→上传(SFTP)→解压→装依赖→(可选)开机自启→启动。
4. **开机启动**：勾选“开启开机启动”即通过 systemd 服务实现开机自动运行。

支持两种部署包：
- **源码包**（含 `main.py`）：自动建 venv 并安装 PyQt5/PyYAML/requests/paramiko。
- **onedir 包**（含可执行文件 `EOL_CIT`）：自带 PyQt5，目标机无需 Python，直接用可执行文件。

## 运行

```bash
cd /home/jyzn/Test_management_sw/deploy_tool
python3 -m pip install -r requirements.txt   # PyQt5, paramiko
python3 deploy_tool.py
```

## 目录文件

| 文件 | 作用 |
|------|------|
| `deploy_tool.py` | PyQt5 上位机主程序（界面 + 交互） |
| `remote_deployer.py` | 部署核心逻辑（paramiko SSH/SFTP、多台串行部署、打包） |
| `remote_install.sh` | 上传到远程主机执行的安装脚本（识别源码/onedir，可配开机自启） |
| `requirements.txt` | 上位机依赖 |
| `README.md` | 本说明 |

## 使用步骤

1. 选择软件文件夹（**推荐直接选已经打包好的 onedir 单文件夹 `deploy/dist/EOL_CIT`**，
   目标机免装 Python；也可选项目源码目录，工具会自动排除 `data/logs`、
   `data/reports`、`deploy/dist`、`build`、`venv` 等大目录，打包很快）。
   也可在“浏览”中切换选择 `.tar.gz` 安装包。
2. 输入第一台远程设备 IP/用户名/密码，点“+ 添加设备”；重复添加更多设备。
3. 填写远程目标目录（默认 `~/Test_Management`）。
4. 根据需要勾选“开机启动”。
5. 点“一键部署”，在下方日志查看每台设备的打包/上传/安装进度与结果。

> 说明：若日志停在“正在打包…”，说明软件文件夹较大，请稍等；或改选
> `deploy/dist/EOL_CIT` 单文件夹以获得飞快的打包与部署。

## 说明

- 上位机通过 `paramiko` 连接远程主机（SSH/SFTP），要求远程主机已开启 SSH。
- 开启开机启动需要远程用户具备 `sudo` 权限（可在“sudo 密码”处单独填写，留空则用密码）。
- onedir 包体积较大（几十 MB），上传耗时与网络相关。
