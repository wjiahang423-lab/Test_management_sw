#!/usr/bin/env python3
"""
AGX Orin / RK3588 Deployer (headless)

Phase 1: Firmware Flash (USB-TypeC)
Phase 2: Software Deployment (Ethernet)

本模块不包含任何 GUI 页面，提供可被 EOL 测试管理软件调用的执行函数。
每个函数接收 `params: dict`，返回符合 EOL 脚本约定的字典:
    {"value", "pass", "message", "unit"}
运行过程中的输出通过 print() 输出，会被 EOL 软件的 ScriptSandbox 捕获。

在测试计划中可配置的 params 键:
    公共:
        flash_dir   : 固件/镜像所在目录
        sw_dir      : 软件包所在目录
        yaml_dir    : yaml 配置文件目录
    Orin:
        orin_ip, orin_user, orin_pass, orin_sudo_pass
    RK3588:
        rk3588_ip, rk3588_user, rk3588_pass, rk3588_sudo_pass

  打开"步骤属性 → 步骤参数",点 ➕ 添加行:

  ┌──────────┬─────┬───────────────────────┬──────────────────────────────┐
  │ 参数名  │ 类  │           值(示例)            │          说明           │
  │         │ 型  │                               │                         │
  ├─────────┼─────┼───────────────────────────────┼─────────────────────────┤
  │ sw_dir  │ str │ /home/jyzn/flash_folder       │ 软件包所在本地目录。留  │
  │         │     │                               │ 空用默认 PUB_PATH       │
  ├─────────┼─────┼───────────────────────────────┼─────────────────────────┤
  │ orin_ip │ str │ 192.168.88.10                 │ Orin 的 IP。留空用默认  │
  │         │     │                               │ ORIN_IP                 │
  ├─────────┼─────┼───────────────────────────────┼─────────────────────────┤
  │ orin_us │ str │ zioneer                       │ SSH 用户名              │
  │ er      │     │                               │                         │
  ├─────────┼─────┼───────────────────────────────┼─────────────────────────┤
  │ orin_pa │ str │ 123                           │ SSH 密码(明文,脚本读)   │
  │ ss      │     │                               │                         │
  ├─────────┼─────┼───────────────────────────────┼─────────────────────────┤
  │ package │ lis │ ["essp.tar.gz","sensor_driver │ 要上传的文件名列表(留空 │
  │ s       │ t   │ .tar.gz"]                     │ 用默认 ORIN_PACKAGES)   │
  ├─────────┼─────┼───────────────────────────────┼─────────────────────────┤
  │ remote_ │ str │ ~/vision                      │ 远端目录                │
  │ dir     │     │                               │                         │
  └─────────┴─────┴───────────────────────────────┴─────────────────────────┘

"""
import os
import sys
import time
import shutil
import functools
import subprocess

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False

# =====================================================================
# CONFIGURATION — Edit these values to change deployment parameters
# (测试计划中通过 params 传入的值会覆盖以下默认值)
# =====================================================================

# --- AGX Orin network & auth ---
ORIN_IP = "192.168.88.10"
ORIN_USER = "zioneer"
ORIN_PASS = "123"
ORIN_SUDO_PASS = "123"

#-----Service_PC---------
Service_USER = ""
Service_SUDO_PSW = "zhengjijicheng"

# ----Path------------
PUB_PATH = "/home/jyzn/flash_folder"

# --- AGX Orin flash ---
ORIN_FLASH_IMAGE = "mfi_jetson-agx-orin-devkit.tar.gz"
ORIN_FLASH_EXTRACT_DIR = "mfi_jetson-agx-orin-devkit"
ORIN_FLASH_SCRIPT_REL = "tools/kernel_flash/l4t_initrd_flash.sh"
ORIN_FLASH_XML_REL = "tools/kernel_flash/flash_l4t_t234_nvme.xml"
ORIN_FLASH_NETWORK_IFACE = "usb0"
ORIN_FLASH_DEVICE = "jetson-agx-orin-devkit"
ORIN_FLASH_STORAGE = "external"

# --- AGX Orin software packages ---
ORIN_PACKAGES = [
    "essp.tar.gz",
    "sensor_driver.tar.gz",
    "motion_install.tar.gz",
    "vision_json_sum.tar.gz",
    "vision_perception.tar.gz",
    "MVS_STD_V3.0.1_251113.zip",
]
ORIN_REMOTE_DIR = "~/vision"
ORIN_MVS_DEB = "MVS-3.0.1_aarch64_20251113.deb"

ORIN_ROS_DOMAIN_ID = "101"
ORIN_OLD_ROS_DOMAIN_ID = "13"
ORIN_ESSP_HOME = "/home/zioneer/essp"

# --- RK3588 network & auth ---
RK3588_IP = "192.168.88.20"
RK3588_USER = "zioneer"
RK3588_PASS = "123"
RK3588_SUDO_PASS = "123"

# --- RK3588 flash ---
RK3588_FLASH_TOOL = "upgrade_tool"
RK3588_IMAGE_FILE = "motion_board.img"

# --- RK3588 software packages ---
RK3588_DEB_PACKAGE = "zioneer-etherkit-1.0.0-aarch64.deb"
RK3588_TAR_PACKAGE = "motion_install.tar.gz"
RK3588_MC_DIR = "~/mc"
RK3588_SCRIPT_DIR = "~/mc/script"

RK3588_YAML_FILES = ["components_config.yaml", "motor_zero.yaml", "robot_axes.yaml"]
RK3588_REMOTE_YAML_DIR = "/etc/servo/config"
RK3588_ROS_DOMAIN_ID = "60"


# =====================================================================
# Result helpers
# =====================================================================

def _result(passed, message, value="", unit=""):
    """返回符合 EOL 脚本约定的结果字典."""
    return {"value": value, "pass": bool(passed), "message": message, "unit": unit}


def _guard(func):
    """将函数异常转换为 FAIL 结果，保证返回结构一致."""
    @functools.wraps(func)
    def wrapper(params=None):
        params = params if isinstance(params, dict) else {}
        try:
            return func(params)
        except Exception as e:
            print(f"[ERROR] {e}")
            return _result(False, str(e))
    return wrapper


# =====================================================================
# Config resolution (params 覆盖模块默认值)
# =====================================================================

def _cfg(params, key, default):
    value = params.get(key)
    return default if value is None else value


# =====================================================================
# Helpers: local & ssh command execution (同步, 替代原 GUI 线程)
# =====================================================================

def _cmd_in(cwd, cmd):
    """Prepend cd if cwd is given — makes intent explicit."""
    return f"cd {cwd} && {cmd}" if cwd else cmd


def _collect_files(base_dir, filenames):
    """Return (existing_abs_paths, missing_filenames)."""
    existing, missing = [], []
    for f in filenames:
        p = os.path.join(base_dir, f)
        if os.path.isfile(p):
            existing.append(p)
        else:
            missing.append(f)
    return existing, missing


def _require_dir(base_dir, what):
    if not base_dir:
        raise RuntimeError(f"未指定{what}目录 (params['...'])")
    if not os.path.isdir(base_dir):
        raise RuntimeError(f"{what}目录不存在: {base_dir}")


def _run_local(cmd, cwd=None, sudo_password=None):
    """在本地同步执行命令，实时打印输出。失败时抛异常."""
    if sudo_password:
        full_cmd = f"echo '{sudo_password}' | sudo -S bash -c \"{cmd}\""
    else:
        full_cmd = cmd
    print(f"$ {full_cmd}")
    proc = subprocess.Popen(
        full_cmd, shell=True, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1
    )
    for line in proc.stdout:
        print(line.rstrip(), flush=True)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"命令执行失败 (exit code: {proc.returncode}): {cmd}")
    return proc.returncode


def _run_ssh(host, user, password, commands=None, upload_files=None,
             remote_dir='~', sudo_password=None):
    """通过 SSH 同步执行命令 / 上传文件。失败时抛异常."""
    if not HAS_PARAMIKO:
        raise RuntimeError("paramiko 未安装，请执行: pip install paramiko")

    commands = commands or []
    upload_files = upload_files or []
    sudo = sudo_password or "123"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print(f"[SSH] Connecting to {user}@{host}...")
        client.connect(host, username=user, password=password, timeout=15)
        print("[SSH] Connected!")

        # 执行命令
        for idx, cmd in enumerate(commands):
            print(f"[SSH] ({idx + 1}/{len(commands)}) {cmd}")
            if 'sudo' in cmd:
                wrapped = cmd.replace('sudo ', f"echo '{sudo}' | sudo -S ")
                stdin, stdout, stderr = client.exec_command(wrapped, timeout=120)
            else:
                stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
            exit_status = stdout.channel.recv_exit_status()

            out = stdout.read().decode('utf-8', errors='replace').strip()
            err = stderr.read().decode('utf-8', errors='replace').strip()

            if out:
                for line in out.split('\n'):
                    print(f"  {line}")
            if err:
                for line in err.split('\n'):
                    print(f"  [ERR] {line}")

            if exit_status != 0:
                raise RuntimeError(f"SSH 命令执行失败 (exit: {exit_status}): {cmd}")

        # 上传文件 - 使用 putfo 替代 put
        if upload_files:
            # 打开 SFTP 会话
            transport = client.get_transport()
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # 获取远程目录的绝对路径
            stdin, stdout, stderr = client.exec_command(f"mkdir -p {remote_dir} && realpath {remote_dir}")
            remote_abs_path = stdout.read().decode('utf-8', errors='replace').strip()
            
            if not remote_abs_path:
                # 如果 realpath 失败，尝试手动解析
                stdin, stdout, stderr = client.exec_command(f"cd {remote_dir} && pwd")
                remote_abs_path = stdout.read().decode('utf-8', errors='replace').strip()
            
            print(f"[SFTP] 远程绝对路径: {remote_abs_path}")
            
            total = len(upload_files)
            for i, local_file in enumerate(upload_files):
                filename = os.path.basename(local_file)
                remote_path = f"{remote_abs_path}/{filename}"
                
                print(f"[SFTP] Uploading {filename} ({i + 1}/{total})...")
                
                # 使用 putfo 方法，先打开本地文件
                try:
                    with open(local_file, 'rb') as local_f:
                        # 获取文件大小
                        file_size = os.path.getsize(local_file)
                        print(f"[SFTP] 文件大小: {file_size} bytes")
                        
                        # 使用 putfo 上传文件对象
                        sftp.putfo(local_f, remote_path, file_size=file_size)
                    
                    print(f"[SFTP] OK: {remote_path}")
                except Exception as e:
                    print(f"[SFTP] 上传失败: {e}")
                    raise
            
            sftp.close()
            print("[SFTP] All files uploaded.")

        print("[SSH] All commands completed successfully")
    except Exception as e:
        print(f"[SSH ERROR] {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        client.close()
# =====================================================================
# USB 设备检测 (烧录前置检查)
# =====================================================================

@_guard
def check_usb_device(params):
    """检查 lsusb 输出中是否包含指定关键字, 确认设备已接入 USB.

    params:
        keyword : 单个关键字, 如 "NVIDIA" / "3588"
        keywords: 关键字列表 (优先于 keyword)
    """
    keywords = params.get("keywords")
    if keywords is None:
        kw = _cfg(params, "keyword", "")
        keywords = [kw] if kw else []
    keywords = [str(k).strip() for k in keywords if str(k).strip()]
    if not keywords:
        raise RuntimeError("未指定检查关键字 (params['keyword'] 或 params['keywords'])")

    print(f"[USB] 执行 lsusb 检查关键字: {keywords}")
    try:
        proc = subprocess.run("lsusb", shell=True, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError("lsusb 执行超时")
    text = proc.stdout or ""
    print(text)

    found = [kw for kw in keywords if kw in text]
    missing = [kw for kw in keywords if kw not in text]
    if missing:
        raise RuntimeError(
            f"USB 列表未检测到: {', '.join(missing)}; "
            f"已找到: {', '.join(found) if found else '无'}"
        )
    return _result(True, f"USB 设备已确认接入: {', '.join(found)}")


@_guard
def check_orin_usb(params):
    """确认 Orin 出现在 lsusb 列表中 (关键字 NVIDIA)."""
    p = dict(params or {})
    p.setdefault("keyword", "NVIDIA")
    return check_usb_device(p)


@_guard
def check_rk3588_usb(params):
    """确认 RK3588 出现在 lsusb 列表中 (关键字 3588)."""
    p = dict(params or {})
    p.setdefault("keyword", "3588")
    return check_usb_device(p)


# =====================================================================
# AGX Orin — Phase 1 (Firmware Flash)
# =====================================================================

@_guard  # 镜像解压缩
def orin_flash_decompress(params):
    """Step 1: 解压 Orin 刷机镜像 (mfi_jetson-agx-orin-devkit.tar.gz)."""
    print("输入的参数信息：",params)
    flash_dir = PUB_PATH
    sudo_pass = Service_SUDO_PSW
    

    _require_dir(flash_dir, "固件(flash)")
    tar_path = os.path.join(flash_dir, ORIN_FLASH_IMAGE)

    if not os.path.isfile(tar_path):
        raise RuntimeError(f"找不到镜像文件: {tar_path}")

    print(f"[Orin Step1] 解压镜像: {ORIN_FLASH_IMAGE}")
    _run_local(f"tar -xzvpf {tar_path}", cwd=flash_dir, sudo_password=sudo_pass)
    return _result(True, f"解压完成: {ORIN_FLASH_IMAGE}",True)

# 镜像刷写
@_guard
def orin_flash_firmware(params):
    """Step 2: 烧录 Orin 固件 (l4t_initrd_flash.sh)."""
    flash_dir =  PUB_PATH
    sudo_pass = Service_SUDO_PSW

    _require_dir(flash_dir, "固件(flash)")
    extract_dir = os.path.join(flash_dir, ORIN_FLASH_EXTRACT_DIR)
    script = os.path.join(extract_dir, ORIN_FLASH_SCRIPT_REL)
    if not os.path.isfile(script):
        raise RuntimeError(f"找不到刷机脚本(请先执行解压步骤):\n{script}")

    print("[Orin Step2] 烧录固件, 请勿断开 USB-TypeC ...")
    cmd = (
        f"{script} --flash-only --massflash 1 "
        f"--external-device nvme0n1p1 "
        f"-c {ORIN_FLASH_XML_REL} "
        f"--showlogs --network {ORIN_FLASH_NETWORK_IFACE} "
        f"{ORIN_FLASH_DEVICE} {ORIN_FLASH_STORAGE}"
    )
    _run_local(cmd, cwd=extract_dir, sudo_password=sudo_pass)
    return _result(True, "固件烧录完成")


@_guard
def orin_ssh_keyscan(params):
    """Step 3: 将 Orin 加入本机 SSH known_hosts."""
    orin_ip = ORIN_IP
    print(f"[Orin Step3] SSH keyscan {orin_ip}")
    cmd = f"ssh-keyscan {orin_ip} >> ~/.ssh/known_hosts 2>/dev/null"
    _run_local(cmd)
    return _result(True, f"SSH key 已记录: {orin_ip}")


# =====================================================================
# AGX Orin — Phase 2 (Software Deployment)
# =====================================================================

# @_guard  # 上传包到Orin
# def orin_transfer_packages(params):
#     """Step 4: 上传软件包到 Orin 的 ~/vision 目录."""
#     sw_dir = PUB_PATH
#     orin_ip = ORIN_IP
#     orin_user = ORIN_USER
#     orin_pass =ORIN_PASS

#     _require_dir(sw_dir, "软件包")
#     upload_files, missing = _collect_files(sw_dir, ORIN_PACKAGES)
#     if missing:
#         raise RuntimeError("缺少文件:\n" + "\n".join(missing))

#     print(f"[Orin Step4] 上传 {upload_files} 软件包到 {orin_user}@{orin_ip}:{ORIN_REMOTE_DIR}")
#     _run_ssh(
#         orin_ip, orin_user, orin_pass,
#         commands=[_cmd_in(None, f"mkdir -p {ORIN_REMOTE_DIR}")],
#         upload_files=upload_files, remote_dir=ORIN_REMOTE_DIR,
#     )
#     return _result(True, f"软件包上传完成 ({len(upload_files)} 个)")

@_guard  # 上传包到Orin
def orin_transfer_packages(params):
    """Step 4: 上传软件包到 Orin 的 ~/vision 目录."""
    sw_dir = PUB_PATH
    orin_ip = ORIN_IP
    orin_user = ORIN_USER
    orin_pass = ORIN_PASS
    
    # 添加调试信息
    # print(f"[DEBUG] sw_dir = {sw_dir}")
    # print(f"[DEBUG] 当前工作目录 = {os.getcwd()}")
    # print(f"[DEBUG] 目录内容 = {os.listdir(sw_dir) if os.path.exists(sw_dir) else '目录不存在'}")
    
    _require_dir(sw_dir, "软件包")
    upload_files, missing = _collect_files(sw_dir, ORIN_PACKAGES)
    
    # 添加调试信息
    # print(f"[DEBUG] upload_files = {upload_files}")
    for f in upload_files:
        print(f"[DEBUG] 文件 {f} 存在? {os.path.isfile(f)}")
    
    if missing:
        raise RuntimeError("缺少文件:\n" + "\n".join(missing))

    print(f"[Orin Step4] 上传 {upload_files} 软件包到 {orin_user}@{orin_ip}:{ORIN_REMOTE_DIR}")
    _run_ssh(
        orin_ip, orin_user, orin_pass,
        commands=[_cmd_in(None, f"mkdir -p {ORIN_REMOTE_DIR}")],
        upload_files=upload_files, remote_dir=ORIN_REMOTE_DIR,
    )
    return _result(True, f"软件包上传完成 ({len(upload_files)} 个)")


# 软件部署 谨慎使用，目前essp路径有问题：不知道在哪里解压，不能自动化部署


@_guard
def orin_deploy_packages(params):
    """Step 5: 在 Orin 上安装部署软件包并配置 ROS_DOMAIN_ID."""
    orin_ip = ORIN_IP
    orin_user =  ORIN_USER
    orin_pass = ORIN_PASS
    orin_sudo = ORIN_SUDO_PASS

    print("[Orin Step5] 部署软件包...") 
    commands = [
        _cmd_in(ORIN_REMOTE_DIR, "sudo tar -zxvf essp.tar.gz"),
        f"sudo sed -i 's/ROS_DOMAIN_ID={ORIN_OLD_ROS_DOMAIN_ID}/ROS_DOMAIN_ID={ORIN_ROS_DOMAIN_ID}/' {ORIN_ESSP_HOME}/code/essp_service/essp_roam_service.sh",
        _cmd_in(f"{ORIN_ESSP_HOME}/code/essp_service", "sudo ./setup_service.sh"),
        f"sudo sed -i 's/export ROS_DOMAIN_ID={ORIN_OLD_ROS_DOMAIN_ID}/export ROS_DOMAIN_ID={ORIN_ROS_DOMAIN_ID}/' {ORIN_ESSP_HOME}/task_manager_new/start_task.sh",
        _cmd_in(ORIN_REMOTE_DIR, "unzip -o MVS_STD_V3.0.1_251113.zip"),
        f"sudo dpkg -i {ORIN_REMOTE_DIR}/{ORIN_MVS_DEB} || sudo dpkg -i {ORIN_REMOTE_DIR}/{ORIN_MVS_DEB} --force-depends",
        f"grep -q 'ROS_DOMAIN_ID={ORIN_ROS_DOMAIN_ID}' /home/{orin_user}/.bashrc || echo 'export ROS_DOMAIN_ID={ORIN_ROS_DOMAIN_ID}' >> /home/{orin_user}/.bashrc",
        f"sudo tar -xvf {ORIN_REMOTE_DIR}/sensor_driver.tar.gz -C /opt/zioneer/",
    ]
    _run_ssh(orin_ip, orin_user, orin_pass, commands=commands, sudo_password=orin_sudo)
    return _result(True, "Orin 软件部署完成")


# =====================================================================
# RK3588 — Phase 1 (Firmware Flash)
# =====================================================================

@_guard
def rk3588_flash_firmware(params):
    """Step 1: 使用 upgrade_tool 烧录 RK3588 固件 (motion_board.img)."""
    img_dir = PUB_PATH
    sudo_pass = Service_SUDO_PSW

    _require_dir(img_dir, "镜像(flash)")
    script = os.path.join(img_dir, RK3588_FLASH_TOOL)
    img_file = os.path.join(img_dir, RK3588_IMAGE_FILE)
    if not os.path.isfile(script):
        raise RuntimeError(f"找不到刷机工具: {script}")
    if not os.path.isfile(img_file):
        raise RuntimeError(f"找不到固件镜像: {img_file}")

    print(f"[RK3588 Step1] 烧录固件: {RK3588_IMAGE_FILE}, 请勿断开 USB-TypeC ...")
    _run_local(f"./{RK3588_FLASH_TOOL} uf {RK3588_IMAGE_FILE}",
               cwd=img_dir, sudo_password=sudo_pass)
    return _result(True, "RK3588 固件烧录完成")


@_guard
def rk3588_ssh_keyscan(params):
    """Step 2: 将 RK3588 加入本机 SSH known_hosts."""
    rk3588_ip = _cfg(params, "rk3588_ip", RK3588_IP)
    known_hosts = os.path.expanduser("~/.ssh/known_hosts")
    print(f"[RK3588 Step2] SSH keyscan {rk3588_ip}")
    cmd = (
        f"ssh-keygen -f {known_hosts} -R {rk3588_ip} 2>/dev/null; "
        f"ssh-keyscan {rk3588_ip} >> {known_hosts} 2>/dev/null"
    )
    _run_local(cmd)
    return _result(True, f"SSH key 已记录: {rk3588_ip}")


@_guard
def rk3588_transfer_packages(params):
    """Step 3: 上传 deb/tar.gz 软件包到 RK3588 home 目录."""
    sw_dir = PUB_PATH
    rk3588_ip = _cfg(params, "rk3588_ip", RK3588_IP)
    rk3588_user = _cfg(params, "rk3588_user", RK3588_USER)
    rk3588_pass = _cfg(params, "rk3588_pass", RK3588_PASS)

    _require_dir(sw_dir, "软件包")
    upload_files, missing = _collect_files(sw_dir, [RK3588_DEB_PACKAGE, RK3588_TAR_PACKAGE])
    if missing:
        raise RuntimeError("缺少文件:\n" + "\n".join(missing))

    print(f"[RK3588 Step3] 上传 {len(upload_files)} 个软件包到 {rk3588_user}@{rk3588_ip}:~")
    _run_ssh(rk3588_ip, rk3588_user, rk3588_pass, upload_files=upload_files, remote_dir="~")
    return _result(True, f"软件包上传完成 ({len(upload_files)} 个)")


# =====================================================================
# RK3588 — Phase 2 (Software Deployment)
# =====================================================================

@_guard
def rk3588_install_packages(params):
    """Step 4: 创建 ~/mc, 移动软件包并安装 etherkit deb."""
    rk3588_ip = _cfg(params, "rk3588_ip", RK3588_IP)
    rk3588_user = _cfg(params, "rk3588_user", RK3588_USER)
    rk3588_pass = _cfg(params, "rk3588_pass", RK3588_PASS)
    rk3588_sudo = _cfg(params, "rk3588_sudo_pass", RK3588_SUDO_PASS)

    print("[RK3588 Step4] 安装 zioneer-etherkit deb ...")
    commands = [
        f"mkdir -p {RK3588_MC_DIR}",
        f"mv ~/{RK3588_DEB_PACKAGE} {RK3588_MC_DIR}/",
        f"mv ~/{RK3588_TAR_PACKAGE} {RK3588_MC_DIR}/",
        _cmd_in(RK3588_MC_DIR, f"sudo apt install -y ./{RK3588_DEB_PACKAGE}"),
    ]
    _run_ssh(rk3588_ip, rk3588_user, rk3588_pass, commands=commands, sudo_password=rk3588_sudo)
    return _result(True, "RK3588 etherkit 安装完成")


@_guard
def rk3588_extract_motion(params):
    """Step 5: 解压 motion_install.tar.gz."""
    rk3588_ip = _cfg(params, "rk3588_ip", RK3588_IP)
    rk3588_user = _cfg(params, "rk3588_user", RK3588_USER)
    rk3588_pass = _cfg(params, "rk3588_pass", RK3588_PASS)

    print("[RK3588 Step5] 解压 motion_install.tar.gz ...")
    _run_ssh(
        rk3588_ip, rk3588_user, rk3588_pass,
        commands=[_cmd_in(RK3588_MC_DIR, f"tar -xzf {RK3588_TAR_PACKAGE}")],
    )
    return _result(True, "motion 软件包解压完成")


@_guard
def rk3588_chmod_scripts(params):
    """Step 6: 为启停脚本添加可执行权限."""
    rk3588_ip = _cfg(params, "rk3588_ip", RK3588_IP)
    rk3588_user = _cfg(params, "rk3588_user", RK3588_USER)
    rk3588_pass = _cfg(params, "rk3588_pass", RK3588_PASS)
    rk3588_sudo = _cfg(params, "rk3588_sudo_pass", RK3588_SUDO_PASS)

    print("[RK3588 Step6] chmod 启停脚本 ...")
    commands = [
        f"sudo chmod +x {RK3588_SCRIPT_DIR}/start_jiyi_robot.sh",
        f"sudo chmod +x {RK3588_SCRIPT_DIR}/stop_jiyi_robot.sh",
    ]
    _run_ssh(rk3588_ip, rk3588_user, rk3588_pass, commands=commands, sudo_password=rk3588_sudo)
    return _result(True, "脚本权限设置完成")


@_guard
def rk3588_set_domain_id(params):
    """Step 7: 配置 RK3588 的 ROS_DOMAIN_ID."""
    rk3588_ip = _cfg(params, "rk3588_ip", RK3588_IP)
    rk3588_user = _cfg(params, "rk3588_user", RK3588_USER)
    rk3588_pass = _cfg(params, "rk3588_pass", RK3588_PASS)

    print(f"[RK3588 Step7] 设置 ROS_DOMAIN_ID={RK3588_ROS_DOMAIN_ID} ...")
    commands = [
        f"grep -q 'ROS_DOMAIN_ID={RK3588_ROS_DOMAIN_ID}' /home/{rk3588_user}/.bashrc "
        f"|| echo 'export ROS_DOMAIN_ID={RK3588_ROS_DOMAIN_ID}' >> /home/{rk3588_user}/.bashrc",
    ]
    _run_ssh(rk3588_ip, rk3588_user, rk3588_pass, commands=commands)
    return _result(True, f"ROS_DOMAIN_ID={RK3588_ROS_DOMAIN_ID} 已配置")


@_guard
def rk3588_transfer_yaml(params):
    """Step 8: 上传 yaml 配置文件到 /etc/servo/config."""
    yaml_dir = _cfg(params, "yaml_dir", PUB_PATH)
    rk3588_ip = _cfg(params, "rk3588_ip", RK3588_IP)
    rk3588_user = _cfg(params, "rk3588_user", RK3588_USER)
    rk3588_pass = _cfg(params, "rk3588_pass", RK3588_PASS)

    _require_dir(yaml_dir, "yaml 配置")
    upload_files, missing = _collect_files(yaml_dir, RK3588_YAML_FILES)
    if missing:
        raise RuntimeError("缺少文件:\n" + "\n".join(missing))

    print(f"[RK3588 Step8] 上传 {len(upload_files)} 个 yaml 到 {RK3588_REMOTE_YAML_DIR} ...")
    _run_ssh(
        rk3588_ip, rk3588_user, rk3588_pass,
        commands=[f"sudo mkdir -p {RK3588_REMOTE_YAML_DIR}"],
        upload_files=upload_files, remote_dir=RK3588_REMOTE_YAML_DIR,
        sudo_password=_cfg(params, "rk3588_sudo_pass", RK3588_SUDO_PASS),
    )
    return _result(True, f"yaml 配置上传完成 ({len(upload_files)} 个)")


# =====================================================================
# 组合执行函数 (一次调用跑完整阶段)
# =====================================================================

@_guard
def orin_phase1_all(params):
    """Orin Phase 1: 解压镜像 -> 烧录固件 -> SSH keyscan."""
    steps = [orin_flash_decompress, orin_flash_firmware, orin_ssh_keyscan]
    for fn in steps:
        r = fn(params)
        print(f"  [{r['pass'] and 'PASS' or 'FAIL'}] {fn.__name__}: {r['message']}")
        if not r["pass"]:
            return r
    return _result(True, "Orin Phase 1 全部完成")


@_guard
def orin_phase2_all(params):
    """Orin Phase 2: 上传软件包 -> 部署安装."""
    steps = [orin_transfer_packages, orin_deploy_packages]
    for fn in steps:
        r = fn(params)
        print(f"  [{r['pass'] and 'PASS' or 'FAIL'}] {fn.__name__}: {r['message']}")
        if not r["pass"]:
            return r
    return _result(True, "Orin Phase 2 全部完成")


@_guard
def rk3588_phase1_all(params):
    """RK3588 Phase 1: 烧录固件 -> SSH keyscan -> 上传软件包."""
    steps = [rk3588_flash_firmware, rk3588_ssh_keyscan, rk3588_transfer_packages]
    for fn in steps:
        r = fn(params)
        print(f"  [{r['pass'] and 'PASS' or 'FAIL'}] {fn.__name__}: {r['message']}")
        if not r["pass"]:
            return r
    return _result(True, "RK3588 Phase 1 全部完成")


@_guard
def rk3588_phase2_all(params):
    """RK3588 Phase 2: 安装 deb -> 解压 -> chmod -> 配置域 -> 上传 yaml."""
    steps = [
        rk3588_install_packages, rk3588_extract_motion, rk3588_chmod_scripts,
        rk3588_set_domain_id, rk3588_transfer_yaml,
    ]
    for fn in steps:
        r = fn(params)
        print(f"  [{r['pass'] and 'PASS' or 'FAIL'}] {fn.__name__}: {r['message']}")
        if not r["pass"]:
            return r
    return _result(True, "RK3588 Phase 2 全部完成")


# =====================================================================
# CLI 自测入口 (EOL 软件通过 ScriptSandbox 调用, 不走这里)
# =====================================================================

if __name__ == "__main__":
    print("AGX Orin / RK3588 Deployer (headless) 已加载")
    print("可用执行函数:")
    print("  USB检测: check_usb_device, check_orin_usb, check_rk3588_usb")
    print("  Orin   : orin_flash_decompress, orin_flash_firmware, orin_ssh_keyscan,")
    print("           orin_transfer_packages, orin_deploy_packages")
    print("  Orin 组合: orin_phase1_all, orin_phase2_all")
    print("  RK3588 : rk3588_flash_firmware, rk3588_ssh_keyscan, rk3588_transfer_packages,")
    print("           rk3588_install_packages, rk3588_extract_motion, rk3588_chmod_scripts,")
    print("           rk3588_set_domain_id, rk3588_transfer_yaml")
    print("  RK3588组合: rk3588_phase1_all, rk3588_phase2_all")
    print()
    print("用法示例 (在 EOL 测试计划中):")
    print("  script: ../../scripts/orin_3588_deployer.py")
    print("  function: orin_phase2_all")
    print("  params: {flash_dir: '...', sw_dir: '...', orin_ip: '192.168.88.10', ...}")
