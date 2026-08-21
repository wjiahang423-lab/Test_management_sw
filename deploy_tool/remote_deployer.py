# -*- coding: utf-8 -*-
"""remote_deployer.py — 远程部署核心逻辑(基于 paramiko)。

负责:
  1. 把用户选择的软件文件夹打包成 tar.gz
  2. 通过 SFTP 上传到远程主机
  3. 上传远程安装脚本并执行(解压/建环境/装依赖/可选开机自启)
支持一次部署到多台远程 Linux/Ubuntu 主机。
"""
import os
import shutil
import tarfile
import tempfile
import threading

import paramiko


class DeployError(Exception):
    pass


def _pack_folder(folder_path, out_tar, progress=None):
    """把软件文件夹打成 tar.gz(排除缓存/venv/dist/logs/报告等大目录).

    progress: 可选回调 log(msg), 用于反馈打包进度。
    """
    if not os.path.isdir(folder_path):
        raise DeployError("选择的文件夹不存在: {}".format(folder_path))

    # 这些目录名在任意层级出现都会被排除(避免打包庞大或冗余数据)
    EXCLUDE_DIRS = {
        "venv", ".venv", ".git", "__pycache__", "build", "dist",
        "deploy_tool", "deploy", ".idea", ".vscode",
        "logs", "reports", "ext_packages",
    }

    def exclude(member):
        name = member.name
        if name.endswith(".pyc"):
            return None
        parts = name.split("/")
        if "__pycache__" in parts:
            return None
        # 根目录名(N 层中的第一级)不算, 检查其后的每一级目录
        for seg in parts[1:-1]:
            if seg in EXCLUDE_DIRS:
                return None
        # 排除打包输出自身
        ab = os.path.abspath(out_tar)
        return None if name.endswith(os.path.basename(ab)) else member

    os.makedirs(os.path.dirname(out_tar), exist_ok=True)
    base = os.path.basename(folder_path.rstrip(os.sep)) or "app"
    if progress:
        progress("开始读取 {} ...".format(folder_path))
    nfiles = [0]

    def _member(member):
        r = exclude(member)
        if r is not None and member.isfile():
            nfiles[0] += 1
        return r

    with tarfile.open(out_tar, "w:gz") as tf:
        tf.add(folder_path, arcname=base, filter=_member)
    if progress:
        progress("打包完成, 共 {} 个文件".format(nfiles[0]))


class RemoteHost:
    def __init__(self, ip, username, password, sudo_password=""):
        self.ip = ip
        self.username = username
        self.password = password
        self.sudo_password = sudo_password or password


class RemoteDeployer:
    """连接并执行远程部署(单台主机)。"""

    SSH_OPTS = dict(timeout=20)

    def __init__(self, host, local_tar, remote_install, target_dir, autostart, log=None):
        self.host = host
        self.local_tar = local_tar
        self.remote_install = remote_install
        self.target_dir = target_dir
        self.autostart = autostart
        self._log = log or (lambda msg: None)

    def _out(self, msg):
        self._log(msg)

    def run(self):
        host = self.host
        self._out("[{}] 连接 {}@{} ...".format(host.ip, host.username, host.ip))
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(host.ip, username=host.username,
                           password=host.password, **self.SSH_OPTS)
        except Exception as e:
            raise DeployError("SSH 连接失败 {}@{}: {}".format(host.username, host.ip, e))

        try:
            remote_pkg = "/tmp/deploy_{}.tar.gz".format(os.path.basename(self.local_tar))
            remote_script = "/tmp/remote_install.sh"

            # 上传安装包与安装脚本
            self._out("[{}] 上传安装包 {} ...".format(host.ip, os.path.basename(self.local_tar)))
            sftp = client.open_sftp()
            sftp.put(self.local_tar, remote_pkg)
            sftp.put(self.remote_install, remote_script)
            sftp.close()

            # 执行安装脚本(带 sudo 密码)
            self._out("[{}] 执行安装脚本 ...".format(host.ip))
            cmd = ("SUDO_PASS={!r} bash {} {} {!r} {}"
                   .format(host.sudo_password, remote_script,
                           remote_pkg, self.target_dir,
                           "on" if self.autostart else "off"))
            stdin, stdout, stderr = client.exec_command(cmd, timeout=1800)

            for line in iter(stdout.readline, ""):
                self._out("[{}] {}".format(host.ip, line.rstrip()))
            stdout.channel.recv_exit_status()
            err = stderr.read().decode("utf-8", errors="replace")
            if err.strip():
                self._out("[{}] [stderr] {}".format(host.ip, err.strip()))
        except DeployError:
            raise
        except Exception as e:
            raise DeployError("部署 {} 失败: {}".format(host.ip, e))
        finally:
            client.close()
        self._out("[{}] 部署完成".format(host.ip))


class DeployCoordinator:
    """管理多台主机的并发/串行部署与日志。"""

    def __init__(self, log=None):
        self._log = log or (lambda msg: None)
        self._running = False
        self._lock = threading.Lock()

    def _out(self, msg):
        self._log(msg)

    def deploy(self, folder_path, hosts, target_dir, autostart):
        """执行部署。folder_path 为本机软件文件夹或 .tar.gz。"""
        if self._running:
            raise DeployError("已有部署任务正在执行")
        if not hosts:
            raise DeployError("请至少添加一台远程设备")
        self._running = True
        try:
            tmpdir = tempfile.mkdtemp(prefix="eol_deploy_")
            try:
                if folder_path.lower().endswith(".tar.gz") or folder_path.lower().endswith(".tgz"):
                    local_tar = os.path.abspath(folder_path)
                    self._out("[INFO] 使用现有安装包: {}".format(local_tar))
                else:
                    local_tar = os.path.join(tmpdir, "package.tar.gz")
                    self._out("[INFO] 正在打包软件文件夹, 请稍候 ...")
                    _pack_folder(folder_path, local_tar, progress=self._out)

                remote_install = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "remote_install.sh")
                results = {}
                for host in hosts:
                    self._out("")
                    self._out("========== 开始部署 {}@{} ==========".format(host.username, host.ip))
                    try:
                        RemoteDeployer(host, local_tar, remote_install,
                                       target_dir, autostart, self._log).run()
                        results[host.ip] = "OK"
                    except DeployError as e:
                        self._out("[ERROR] {}".format(e))
                        results[host.ip] = "FAIL: {}".format(e)
                    self._out("========== {} 结束 ==========".format(host.ip))
                return results
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        finally:
            self._running = False
