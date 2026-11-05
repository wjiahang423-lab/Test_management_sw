"""后台部署与辨识服务；所有设备操作由调用者的工作线程执行。"""
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import threading
import time
import uuid

from .bundle import inspect_archive, sha256


class WorkbenchService:
    def __init__(self, driver, helper_dir, emit=print):
        self.driver = driver
        self.helper_dir = Path(helper_dir)
        self.emit = emit
        self._channel = None
        self._input_lock = threading.Lock()
        self.awaiting_apply = False
        self.home = None
        self._pidfile = None

    def _run(self, command, timeout=60):
        rc, out, err = self.driver.run(command, timeout=timeout)
        if rc:
            raise RuntimeError("设备命令失败 rc=%d：\n%s\n%s" % (rc, out[-4000:], err[-4000:]))
        return out.strip()

    def remote_home(self):
        if self.home is None:
            self.home = self._run('printf "%s" "$HOME"')
            if not self.home.startswith("/") or "\n" in self.home:
                raise ValueError("设备 HOME 路径无效")
        return self.home

    def _check_cancel(self):
        if self.driver._cancel_event.is_set():
            from arm_driver import ArmOperationCancelled
            raise ArmOperationCancelled("工作台任务已取消")

    def _put(self, local, remote):
        self._check_cancel()
        with self.driver.connect().open_sftp() as sftp:
            sftp.put(str(local), remote, callback=lambda *_: self._check_cancel())

    def _get(self, remote, local):
        self._check_cancel()
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        partial = local.with_name(local.name + ".partial")
        try:
            with self.driver.connect().open_sftp() as sftp:
                sftp.get(remote, str(partial), callback=lambda *_: self._check_cancel())
            partial.replace(local)
        finally:
            partial.unlink(missing_ok=True)

    def prepare_helpers(self):
        home = self.remote_home()
        remote = home + "/.arm_workbench/helpers"
        self._run("mkdir -p " + shlex.quote(remote))
        for name in ("bundle.py", "identification_runner.py"):
            self._put(self.helper_dir / name, remote + "/" + name)
        return remote

    def backup(self, local_dir, patch_home=False):
        """停机后才修改 Home；打包是设备原文件，缺少项在清单中标记。"""
        home = self.remote_home()
        helper = self.prepare_helpers()
        if patch_home:
            self.emit("核实左右臂 Home 目标，备份原 YAML 后将左臂目标对齐右臂。\n")
            self.emit(self._run("python3 %s patch-home %s" % (
                shlex.quote(helper + "/bundle.py"), shlex.quote(home + "/install"))))
        name = "动力学标定程序_device_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6] + ".tar.gz"
        remote = home + "/.arm_workbench/" + name
        analog = home + "/analog_output_snap.py"
        # 保留设备原版 analog；缺失时使用随软件附带的版本，并在清单记录。
        exists = self._run("test -f %s && echo YES || echo NO" % shlex.quote(analog))
        if exists != "YES":
            analog = helper + "/analog_output_snap.py"
            self._put(self.helper_dir / "analog_output_snap.py", analog)
            self.emit("设备缺少 analog_output_snap.py；归档附带本地版本，使用前须核实 PDO 布局。\n")
        command = "python3 %s pack %s %s --origin %s --vendor %s --analog %s" % tuple(map(shlex.quote, (
            helper + "/bundle.py", home + "/install", remote,
            "device:" + self.driver.host + ":" + home + "/install;analog=" + analog,
            home + "/run_arm_dynamics_identification.py", analog)))
        info = json.loads(self._run(command, timeout=600))
        local = Path(local_dir) / name
        self._get(remote, local)
        if sha256(local) != info["sha256"]:
            local.unlink()
            raise RuntimeError("下载包 SHA256 不一致，已删除不完整文件")
        local.with_name(name + ".sha256").write_text(info["sha256"] + "  " + name + "\n")
        # 用户可直接取得脚本，归档中也保留一份。
        for remote_file, filename in ((analog, "analog_output_snap.py"),
                (home + "/run_arm_dynamics_identification.py", "run_arm_dynamics_identification.py")):
            if self._run("test -f %s && echo YES || echo NO" % shlex.quote(remote_file)) == "YES":
                self._get(remote_file, local.parent / (local.stem + "_files") / filename)
        return dict(info, local=str(local))

    def deploy(self, archive_path):
        info = inspect_archive(archive_path)
        helper = self.prepare_helpers()
        remote = self.remote_home() + "/.arm_workbench/upload_" + uuid.uuid4().hex + ".tar.gz"
        self.emit("上传部署包并校验 SHA256…\n")
        self._put(archive_path, remote)
        actual = self._run("sha256sum " + shlex.quote(remote)).split()[0]
        if actual != info["sha256"]:
            raise RuntimeError("设备端部署包 SHA256 不一致，未替换 install")
        self._check_cancel()
        # 切换操作必须完整结束，取消仅在传输/预检阶段响应。
        independent = self._fresh_driver()
        try:
            rc, out, err = independent.run("python3 %s deploy %s" % (
                shlex.quote(helper + "/bundle.py"), shlex.quote(remote)), timeout=600)
            if rc:
                raise RuntimeError("部署失败：" + out[-3000:] + err[-3000:])
            return json.loads(out)
        finally:
            independent.close()

    def capture_analog(self, arm, local_dir, duration=10):
        """调用附带脚本的只读采集函数，不调用写入 torque_sensor_zero 的交互入口。"""
        if arm not in ("left", "right"):
            raise ValueError("请选择左臂或右臂")
        helper = self.prepare_helpers()
        self._put(self.helper_dir / "analog_output_snap.py", helper + "/analog_output_snap.py")
        remote = self.remote_home() + "/.arm_workbench/analog_" + arm + "_" + uuid.uuid4().hex
        self._run("mkdir -p " + shlex.quote(remote))
        script = (
            "import sys; sys.path.insert(0, %r); import analog_output_snap as a; "
            "data=a.collect_data(%d, %r); "
            "assert data, '未采集到模拟量数据'; "
            "a.save_raw_data(data, %r, %r); "
            "avg=a.calculate_averages(data, %r); "
            "a.save_averages(avg, %r, %r); a.display_averages(avg, %r)"
        ) % (helper, duration, arm, remote + "/raw.csv", arm, arm,
             remote + "/averages.csv", arm, arm)
        self.emit("采集 analog_output1/2，使用附带脚本的 22×512 PDO 布局（左右臂基址 1/8）；不写入零点。\n")
        self.emit(self._run("python3 -u -c " + shlex.quote(script), timeout=duration + 30) + "\n")
        local = Path(local_dir) / Path(remote).name
        for name in ("averages.csv", "raw.csv"):
            self._get(remote + "/" + name, local / name)
        return {"arm": arm, "serial": "analog", "local": str(local),
                "files": ["averages.csv", "raw.csv"],
                "status": "模拟量采集完成，未写入零点配置"}

    def _fresh_driver(self):
        from arm_driver import ArmDriver
        d = self.driver
        return ArmDriver(host=d.host, port=d.port, user=d.user, password=d.password,
                         connect_timeout=d.connect_timeout, cmd_timeout=d.cmd_timeout)

    def reply_apply(self, apply):
        with self._input_lock:
            if not self.awaiting_apply or self._channel is None:
                raise RuntimeError("当前没有等待 APPLY 的原厂提示")
            self._channel.sendall(b"APPLY\n" if apply else b"SKIP\n")
            self.awaiting_apply = False

    def run_identification(self, arm, serial, local_dir, timeout=3600):
        if arm not in ("left", "right") or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", serial):
            raise ValueError("机械臂编号只允许字母、数字、下划线和短横线")
        home = self.remote_home()
        helper = self.prepare_helpers()
        runner = helper + "/identification_runner.py"
        base = "python3 -u %s %s %s --home %s" % tuple(map(shlex.quote, (runner, arm, serial, home)))
        rc, out, err = self.driver.mc(base + " --check", timeout=30)
        if rc:
            raise RuntimeError("辨识前置检查失败，未发送运动：" + out + err)
        if self.driver.action_server_count() != 1:
            raise RuntimeError("动作服务未就绪或有重复服务端，未启动辨识")
        token = uuid.uuid4().hex
        self._pidfile = home + "/.arm_workbench/ident_" + token + ".pid"
        # 独立进程组，让取消同时终止 runner、原厂脚本及其子客户端。
        command = "cd %s && exec setsid --wait bash -c %s" % (shlex.quote(home), shlex.quote(
            "echo $$ > %s; exec %s" % (shlex.quote(self._pidfile), base)))
        channel = self.driver.connect().get_transport().open_session(timeout=15)
        channel.settimeout(15)
        self._channel = channel
        text = ""
        prompt_buffer = ""
        completed = False
        deadline = time.monotonic() + timeout
        try:
            channel.exec_command(self.driver._mc_shell(command))
            while True:
                self._check_cancel()
                if time.monotonic() > deadline:
                    raise TimeoutError("辨识超时，正在取消")
                for ready, receive in ((channel.recv_ready, channel.recv),
                                       (channel.recv_stderr_ready, channel.recv_stderr)):
                    if ready():
                        chunk = receive(65536).decode("utf-8", "replace")
                        self.emit(chunk)
                        text += chunk
                        prompt_buffer = (prompt_buffer + chunk)[-1200:]
                        # 支持提示跨 SSH 数据块；仅在确切 APPLY 提示后允许写入。
                        if re.search(r"Type\s+APPLY\s*:", prompt_buffer, re.I):
                            with self._input_lock:
                                self.awaiting_apply = True
                            prompt_buffer = ""
                        if len(text) > 2_000_000:
                            text = text[-1_000_000:]
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                time.sleep(0.02)
            rc = channel.recv_exit_status()
            completed = True
        finally:
            with self._input_lock:
                self.awaiting_apply = False
                self._channel = None
            channel.close()
            if not completed:
                try:
                    self.cancel()
                except Exception as exc:
                    self.emit("取消辨识失败，请检查设备：%s\n" % exc)
            else:
                self._pidfile = None
        reports = re.findall(r"WORKBENCH_REPORT=(/[^\r\n]+)", text)
        if not reports:
            raise RuntimeError("辨识程序未提供本次结果，rc=%d\n%s" % (rc, text[-3000:]))
        remote_report = reports[-1].strip()
        prefix = home + "/.arm_workbench/reports/"
        if not remote_report.startswith(prefix) or "/" in remote_report[len(prefix):]:
            raise ValueError("辨识结果路径无效")
        local = Path(local_dir) / (arm + "_" + serial + "_" + token[:8])
        self._get(remote_report, local / "report.json")
        report = json.loads((local / "report.json").read_text(encoding="utf-8"))
        if report["arm"] != arm or report["serial"] != serial:
            raise ValueError("返回结果不属于本次机械臂")
        for name in report.get("files", []):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("结果文件路径无效")
            self._get(home + "/experiment_data/" + str(path), local / path)
            if sha256(local / path) != report.get("sha256", {}).get(name):
                raise RuntimeError("结果文件校验失败：" + name)
        (local / "console.log").write_text(text, encoding="utf-8")
        if rc or report.get("returncode"):
            raise RuntimeError("辨识失败，输出已保存：%s\n%s" % (local, text[-2500:]))
        return dict(report, local=str(local))

    def cancel(self):
        d = self._fresh_driver()
        errors = []
        try:
            try:
                self.emit("发送运动取消…\n" + d.cancel_motion() + "\n")
            except Exception as exc:
                errors.append(str(exc))
            if self._pidfile:
                command = (
                    "p=$(cat %s 2>/dev/null); case $p in ''|*[!0-9]*) exit 0;; esac; "
                    "tr '\\0' ' ' < /proc/\"$p\"/cmdline 2>/dev/null | "
                    "grep -q '[i]dentification_runner.py' || exit 0; "
                    "kill -TERM -- -\"$p\" 2>/dev/null || true") % shlex.quote(self._pidfile)
                d.run(command, timeout=15)
        finally:
            d.close()
        if errors:
            raise RuntimeError("取消服务失败，请检查设备：" + "; ".join(errors))
