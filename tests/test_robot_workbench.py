"""离线部署/辨识回归；从不连接真实设备，不执行真实机械臂运动。"""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from robot_workbench import bundle
from robot_workbench.service import WorkbenchService


@pytest.fixture
def install(tmp_path):
    install = tmp_path / "source" / "install"
    install.mkdir(parents=True)
    (install / "setup.bash").write_text(":\n")
    for name in bundle.EXECUTABLES + bundle.DYNAMICS_EXECUTABLES:
        p = install / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("test binary")
    xml = install / bundle.HOME_XML
    xml.parent.mkdir(parents=True)
    xml.write_text('<root><Action name="move_left_arm_home"/><Action name="move_right_arm_home"/></root>')
    for arm, location, goals in (("left", 1, [0.2, 0, 0, -0.3, 0, 0, 0]), ("right", 2, [0] * 7)):
        p = install / bundle.META_DIR / ("move_%s_arm_home.yaml" % arm)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump({"request": {"component_location": location, "goal": goals}}))
        # 真实配置的 goal 是 flow style，其余内容不应被重写。
        data = yaml.safe_load(p.read_text())
        p.write_text("request:\n  component_location: %d\n  goal: %s\n" % (location, json.dumps(goals)))
    return install


def test_home_patch_updates_goal_preserves_location_and_backup(install):
    left = install / bundle.META_DIR / "move_left_arm_home.yaml"
    before = left.read_text()
    result = bundle.patch_home(install)
    assert Path(result["backup"]).read_text() == before
    cfg = yaml.safe_load(left.read_text())
    assert cfg["request"]["component_location"] == 1
    assert cfg["request"]["goal"] == [0] * 7
    assert result["before"] == [0.2, 0, 0, -0.3, 0, 0, 0]


def test_home_patch_refuses_unexpected_right_goal(install):
    right = install / bundle.META_DIR / "move_right_arm_home.yaml"
    right.write_text("request:\n  component_location: 2\n  goal: [0.5, 0, 0, 0, 0, 0, 0]\n")
    left = install / bundle.META_DIR / "move_left_arm_home.yaml"
    before = left.read_text()
    with pytest.raises(ValueError, match="不是全零"):
        bundle.patch_home(install)
    assert left.read_text() == before


def test_pack_patches_copy_and_marks_missing_vendor(install, tmp_path):
    left = install / bundle.META_DIR / "move_left_arm_home.yaml"
    before = left.read_text()
    out = tmp_path / "bundle.tar.gz"
    result = bundle.pack(install, out, "test:local", patch=True)
    assert not result["dynamics_ready"]
    assert result["missing_dynamics"] == ["run_arm_dynamics_identification.py"]
    assert left.read_text() == before
    assert bundle.inspect_archive(out)["sha256"] == result["sha256"]
    with tarfile.open(out) as tar:
        patched = yaml.safe_load(tar.extractfile("install/" + bundle.META_DIR + "/move_left_arm_home.yaml"))
        assert patched["request"]["goal"] == [0] * 7


@pytest.mark.parametrize("name,link", [("../escape", None), ("/tmp/escape", None),
                                        ("install/link", "/etc"), ("unknown.py", None)])
def test_deployment_rejects_unsafe_archives(tmp_path, name, link):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        item = tarfile.TarInfo(name)
        if link:
            item.type = tarfile.SYMTYPE
            item.linkname = link
        tar.addfile(item)
    with pytest.raises(ValueError):
        bundle.inspect_archive(path)


def test_deploy_keeps_backup_and_sets_executable_permissions(install, tmp_path, monkeypatch):
    home = tmp_path / "robot_home"
    (home / "install").mkdir(parents=True)
    (home / "install/old_version").write_text("keep")
    out = tmp_path / "bundle.tar.gz"
    bundle.pack(install, out, "test:local")
    monkeypatch.setattr(bundle, "ensure_stopped", lambda: None)
    result = bundle.deploy(out, home)
    assert (Path(result["backup"]) / "install/old_version").read_text() == "keep"
    assert (home / "install/setup.bash").is_file()
    assert os.access(home / "install" / bundle.EXECUTABLES[0], os.X_OK)


def test_deploy_rolls_back_install_and_scripts_on_swap_error(install, tmp_path, monkeypatch):
    home = tmp_path / "robot_home"
    (home / "install").mkdir(parents=True)
    (home / "install/old_version").write_text("old")
    (home / "run_arm_dynamics_identification.py").write_text("original vendor")
    vendor = tmp_path / "vendor.py"
    vendor.write_text("new vendor")
    out = tmp_path / "bundle.tar.gz"
    bundle.pack(install, out, "test:local", vendor=vendor)
    monkeypatch.setattr(bundle, "ensure_stopped", lambda: None)
    original = Path.rename

    def fail_swap(self, target):
        if self.name == "install" and self.parent.name == "stage":
            raise OSError("swap failed")
        return original(self, target)
    monkeypatch.setattr(Path, "rename", fail_swap)
    with pytest.raises(OSError, match="swap failed"):
        bundle.deploy(out, home)
    assert (home / "install/old_version").read_text() == "old"
    assert (home / "run_arm_dynamics_identification.py").read_text() == "original vendor"


def test_deploy_rejects_running_motion_before_changes(install, tmp_path, monkeypatch):
    out = tmp_path / "bundle.tar.gz"
    bundle.pack(install, out, "test")
    monkeypatch.setattr(bundle.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError, match="仍在运行"):
        bundle.deploy(out, tmp_path / "home")
    assert not (tmp_path / "home").exists()


def test_vendor_runner_does_not_apply_without_explicit_reply(install, tmp_path):
    home = install.parent
    vendor = home / "run_arm_dynamics_identification.py"
    vendor.write_text('''from pathlib import Path
import sys
data=Path('experiment_data')/sys.argv[2]
data.mkdir(parents=True)
(data/'measurements.csv').write_text('joint,torque\\n1,0.25\\n')
answer=input('Write parameters to runtime YAML? Type APPLY:')
if answer=='APPLY': Path('runtime_modified').write_text('yes')
''')
    runner = ROOT / "tools/robot_workbench/identification_runner.py"
    result = subprocess.run([sys.executable, str(runner), "left", "SN_TEST", "--home", str(home)],
                            input="SKIP\n", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (home / "runtime_modified").exists()
    report = json.loads(next((home / ".arm_workbench/reports").glob("*.json")).read_text())
    assert report["files"] == ["SN_TEST/measurements.csv"]
    assert len(report["sha256"]["SN_TEST/measurements.csv"]) == 64


def test_missing_vendor_blocks_runner_before_motion(install):
    runner = ROOT / "tools/robot_workbench/identification_runner.py"
    result = subprocess.run([sys.executable, str(runner), "right", "SN", "--home", str(install.parent)],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "未启动运动" in result.stdout
    assert not (install.parent / "experiment_data").exists()


def test_service_timeout_attempts_remote_cancel(monkeypatch, tmp_path):
    channel = Mock()
    channel.recv_ready.return_value = channel.recv_stderr_ready.return_value = False
    driver = Mock()
    driver._cancel_event = threading.Event()
    driver.mc.return_value = (0, "ready", "")
    driver.action_server_count.return_value = 1
    driver.connect.return_value.get_transport.return_value.open_session.return_value = channel
    service = WorkbenchService(driver, tmp_path)
    service.home = "/home/test"
    monkeypatch.setattr(service, "prepare_helpers", lambda: "/helpers")
    cancel = Mock()
    monkeypatch.setattr(service, "cancel", cancel)
    with pytest.raises(TimeoutError):
        service.run_identification("left", "TEST", tmp_path, timeout=-1)
    cancel.assert_called_once()
    channel.close.assert_called_once()


def test_service_handles_split_apply_prompt_and_downloads_only_current_result(tmp_path, monkeypatch):
    chunks = [b"Write parameters to runtime YAML? Type AP", b"PLY:"]
    report = {"arm": "left", "serial": "SN", "returncode": 0, "files": ["SN/m.csv"],
              "sha256": {}, "status": "program completed"}
    csv_file = tmp_path / "source.csv"
    csv_file.write_text("joint,value\n1,0.2\n")
    report["sha256"]["SN/m.csv"] = bundle.sha256(csv_file)

    class Channel:
        sent = []
        def settimeout(self, _): pass
        def exec_command(self, _): pass
        def recv_ready(self): return bool(chunks)
        def recv(self, _): return chunks.pop(0)
        def recv_stderr_ready(self): return False
        def recv_stderr(self, _): return b""
        def exit_status_ready(self): return bool(self.sent) and not chunks
        def recv_exit_status(self): return 0
        def close(self): pass
        def sendall(self, data):
            self.sent.append(data)
            chunks.append(b"\nWORKBENCH_REPORT=/home/test/.arm_workbench/reports/run.json\n")
    channel = Channel()
    driver = Mock()
    driver._cancel_event = threading.Event()
    driver.mc.return_value = (0, "ready", "")
    driver.action_server_count.return_value = 1
    driver.connect.return_value.get_transport.return_value.open_session.return_value = channel
    service = WorkbenchService(driver, tmp_path, lambda _: None)
    service.home = "/home/test"
    monkeypatch.setattr(service, "prepare_helpers", lambda: "/helpers")

    def get(remote, local):
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        if remote.endswith("run.json"):
            local.write_text(json.dumps(report))
        else:
            shutil.copyfile(csv_file, local)
    monkeypatch.setattr(service, "_get", get)
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(service.run_identification("left", "SN", tmp_path)))
    thread.start()
    deadline = time.monotonic() + 2
    while not service.awaiting_apply and time.monotonic() < deadline:
        time.sleep(0.005)
    assert service.awaiting_apply
    assert not channel.sent
    service.reply_apply(False)
    thread.join(2)
    assert not thread.is_alive()
    assert channel.sent == [b"SKIP\n"]
    assert not service.awaiting_apply
    assert (Path(outcome[0]["local"]) / "SN/m.csv").read_text() == csv_file.read_text()
