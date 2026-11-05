"""离线回归：模拟 ROS 命令，不连接或移动真实机械臂。"""
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import arm_control_gui as gui
import arm_driver as arm
import pytest
from PyQt5 import QtCore, QtWidgets, QtTest


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("离线测试禁止连接真实设备")
    monkeypatch.setattr(arm.ArmDriver, "connect", forbidden)


@pytest.fixture
def local_ros(tmp_path, monkeypatch):
    """实际执行生成的 shell，但 ros2 指向临时模拟程序。"""
    setup = tmp_path / "active workspace.bash"
    setup.write_text("export ARM_TEST_WORKSPACE=active\nexport PATH=%s:$PATH\n"
                     % shlex.quote(str(tmp_path)))
    ros = tmp_path / "ros.bash"
    ros.write_text(":\n")
    cli = tmp_path / "ros2"
    cli.write_text('''#!/bin/bash
test "$ARM_TEST_WORKSPACE" = active || exit 90
case "$1 $2" in
  "action info") echo 'Action servers: 1';;
  "action send_goal") echo "Goal finished with status: ${ARM_TEST_STATUS:-SUCCEEDED}";;
  "run mc_task_manager") printf 'TASK=%s\\n' "$4"; echo "Result received - ${ARM_TEST_TASK_STATUS:-Success}";;
  *) exit 91;;
esac
''')
    cli.chmod(0o755)
    monkeypatch.setitem(arm.MOTION, "ros_setup", str(ros))
    monkeypatch.setitem(arm.MOTION, "setup", str(setup))
    monkeypatch.setitem(arm.MOTION, "ros_env", {"ROS_DOMAIN_ID": "37"})
    driver = arm.ArmDriver()

    def local_run(command, timeout=None, **kwargs):
        p = subprocess.run(command, shell=True, executable="/bin/bash",
                           text=True, capture_output=True, timeout=5)
        return p.returncode, p.stdout, p.stderr
    monkeypatch.setattr(driver, "run", local_run)
    return driver


def test_clients_use_active_workspace_and_same_ros_environment(local_ros):
    assert local_ros.action_server_count() == 1
    ok, text = local_ros.run_task("jiyi1/greetings")
    assert ok and "TASK=jiyi1/greetings" in text
    rc, out, _ = local_ros.mc('printf "%s" "$ROS_DOMAIN_ID"')
    assert rc == 0 and out == "37"
    assert local_ros.send_dual_arm_goal([0.1] * 7, [0.1] * 7)
    assert "SUCCEEDED" in local_ros.run_cit_item("mc.arm.small_angle")


def test_task_name_is_one_shell_argument(local_ros, tmp_path):
    marker = tmp_path / "must-not-exist"
    name = "task; touch %s" % marker
    ok, text = local_ros.run_task(name)
    assert ok and "TASK=" + name in text
    assert not marker.exists()


@pytest.mark.parametrize("result", ["ABORTED", "CANCELED", "UNKNOWN"])
def test_cit_rejects_unsuccessful_goal_even_with_zero_exit(local_ros, monkeypatch, result):
    monkeypatch.setitem(arm.MOTION, "ros_env", {"ARM_TEST_STATUS": result})
    with pytest.raises(arm.ArmDriverError, match=result):
        local_ros.run_cit_item("mc.arm.small_angle")


def test_named_task_failure_is_not_success(local_ros, monkeypatch):
    monkeypatch.setitem(arm.MOTION, "ros_env", {"ARM_TEST_TASK_STATUS": "Failure"})
    assert local_ros.run_task("jiyi1/greetings")[0] is False


def test_cit_preserves_compound_command_and_quoting(local_ros, monkeypatch):
    command = "bash ~/script/mc_run.sh 'printf \"first:%s\" \"a b\"'; printf ':second'"
    monkeypatch.setattr(local_ros, "cit_item", lambda _: {"command": command})
    assert local_ros.run_cit_item("compound").strip() == "first:a b:second"


def test_missing_setup_blocks_entire_command(local_ros, monkeypatch, tmp_path):
    marker = tmp_path / "must-not-exist"
    monkeypatch.setitem(arm.MOTION, "setup", str(tmp_path / "missing.bash"))
    rc, _, err = local_ros.mc("echo first; touch %s" % shlex.quote(str(marker)))
    assert rc != 0 and "missing.bash" in err
    assert not marker.exists()


@pytest.mark.parametrize("rc,text", [
    (0, "Goal rejected"), (0, "Goal accepted"),
    (124, "Goal finished with status: SUCCEEDED"),
    (0, "Result received - Failure\nGoal finished with status: SUCCEEDED"),
])
def test_only_terminal_success_is_accepted(rc, text):
    assert not arm.ArmDriver._action_result_ok(rc, text)


def test_invalid_joint_values_never_send_commands(monkeypatch):
    driver = arm.ArmDriver()
    command = Mock()
    monkeypatch.setattr(driver, "mc", command)
    with pytest.raises(arm.ArmDriverError):
        driver.send_dual_arm_goal([float("nan")] * 7, [0] * 7)
    command.assert_not_called()


def test_cancel_interrupts_ssh_wait_and_closes_channel(monkeypatch):
    driver = arm.ArmDriver()
    channel = Mock()
    channel.recv_ready.return_value = False
    channel.recv_stderr_ready.return_value = False
    channel.exit_status_ready.side_effect = lambda: (driver.request_cancel() or False)
    client = Mock()
    client.get_transport.return_value.open_session.return_value = channel
    monkeypatch.setattr(driver, "connect", lambda: client)
    with pytest.raises(arm.ArmOperationCancelled):
        driver.run("waiting", timeout=1)
    channel.close.assert_called_once()


def test_action_timeout_cancels_remote_goal(monkeypatch):
    driver = arm.ArmDriver()
    monkeypatch.setattr(driver, "mc", Mock(return_value=(124, "Goal accepted", "")))
    cancel = Mock(return_value="cancel_goal completed")
    monkeypatch.setattr(driver, "cancel_motion", cancel)
    with pytest.raises(arm.ArmDriverError, match="超时"):
        driver.send_dual_arm_goal([0.1] * 7, [0.1] * 7, timeout=0.1)
    cancel.assert_called_once()


@pytest.mark.parametrize("error", [arm.ArmDriverError("SSH 命令超时"),
                                  OSError("SSH connection lost")])
def test_action_transport_error_cancels_with_fresh_connection(monkeypatch, error):
    driver = arm.ArmDriver(host="test-host", port=2222, user="operator")
    monkeypatch.setattr(driver, "mc", Mock(side_effect=error))
    cleanup = SimpleNamespace(cancel_motion=Mock(return_value="cancel response"),
                              close=Mock())
    factory = Mock(return_value=cleanup)
    monkeypatch.setattr(arm, "ArmDriver", factory)
    with pytest.raises(arm.ArmDriverError, match="无法确认执行结果") as caught:
        driver.send_dual_arm_goal([0.1] * 7, [0.1] * 7)
    assert str(error) in str(caught.value)
    assert "cancel response" in str(caught.value)
    assert factory.call_args.kwargs["host"] == "test-host"
    assert factory.call_args.kwargs["port"] == 2222
    cleanup.cancel_motion.assert_called_once()
    cleanup.close.assert_called_once()


def test_action_transport_error_reports_failed_cancellation(monkeypatch):
    driver = arm.ArmDriver()
    monkeypatch.setattr(driver, "mc", Mock(side_effect=OSError("SSH lost")))
    cleanup = SimpleNamespace(cancel_motion=Mock(side_effect=OSError("offline")),
                              close=Mock())
    monkeypatch.setattr(arm, "ArmDriver", Mock(return_value=cleanup))
    with pytest.raises(arm.ArmDriverError, match="取消动作失败") as caught:
        driver.send_dual_arm_goal([0.1] * 7, [0.1] * 7)
    assert "SSH lost" in str(caught.value) and "offline" in str(caught.value)
    cleanup.close.assert_called_once()


def test_explicit_action_cancel_does_not_start_duplicate_cleanup(monkeypatch):
    driver = arm.ArmDriver()
    monkeypatch.setattr(driver, "mc", Mock(side_effect=arm.ArmOperationCancelled()))
    factory = Mock()
    monkeypatch.setattr(arm, "ArmDriver", factory)
    with pytest.raises(arm.ArmOperationCancelled):
        driver.send_dual_arm_goal([0.1] * 7, [0.1] * 7)
    factory.assert_not_called()


@pytest.fixture(scope="module")
def app():
    app = QtWidgets.QApplication.instance() or gui.SafeApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def pump_until(app, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Qt 任务没有按期结束")
        QtTest.QTest.qWait(5)
    app.processEvents()


@pytest.fixture
def window(app, monkeypatch):
    win = gui.MainWindow()
    win.chk_stop_on_exit.setChecked(False)
    fake = SimpleNamespace(cancel_motion=Mock(return_value="cancelled"), close=Mock(),
                           stop_motion_control=Mock(return_value=1))
    monkeypatch.setattr(win, "_new_driver", lambda: fake)
    win.show()
    yield win
    arm.driver.request_cancel()
    pump_until(app, lambda: not win._tasks)
    win.close()
    pump_until(app, lambda: win._allow_close and not win.isVisible())


def test_worker_error_and_callbacks_stay_on_gui_thread(window, app):
    threads = []
    window._run_task("测试", lambda: 42,
                     on_done=lambda _: threads.append(QtCore.QThread.currentThread()))
    pump_until(app, lambda: not window._tasks)
    assert threads == [app.thread()]
    window._run_task("异常", lambda: 1 / 0)
    pump_until(app, lambda: not window._tasks)
    assert "ZeroDivisionError" in window.log_view.toPlainText()
    assert window.isVisible() and not window._busy


def test_main_window_has_capture_tab_and_function_point_command_list(window):
    assert window.tabs.count() == 3
    assert window.tabs.tabText(0) == "机械臂运控"
    assert window.tabs.tabText(1) == "手眼采集"
    assert window.tabs.tabText(2) == "动力学辨识与部署"
    assert window.cmb_instructions.count() == len(window._instructions)
    assert window.cmb_instructions.count() >= 20
    assert window.cmb_instructions.itemText(0) == "运动停止"
    assert "ros2 service call" in window.ed_instruction.toPlainText()
    tbd = next(i for i, item in enumerate(window._instructions)
               if "TBD" in item["name"])
    window.cmb_instructions.setCurrentIndex(tbd)
    assert not window.btn_instruction.isEnabled()
    assert "TBD" in window.instruction_hint.text()


def test_dynamics_one_click_orders_startup_and_shows_csv(window, app, monkeypatch, tmp_path):
    panel = window.dynamics_panel
    calls = []
    monkeypatch.setattr(panel.service, "prepare_helpers", lambda: "/helpers")
    monkeypatch.setattr(arm.driver, "mc", lambda *a, **kw: (0, "ready", ""))
    monkeypatch.setattr(arm.driver, "start_master_only", lambda: calls.append("master"))
    monkeypatch.setattr(arm.driver, "motion_control_running", lambda: False)
    monkeypatch.setattr(arm.driver, "start_motion_control", lambda **kw: calls.append("motion"))
    import arm_enable
    monkeypatch.setattr(arm_enable, "stop_enable_all", lambda: calls.append("stop_enable"))
    monkeypatch.setattr(gui.arm_test, "enable_all_joints", lambda: calls.append("enable"))
    monkeypatch.setattr(gui.arm_test, "wait_enable_ready", lambda **kw: calls.append("ready"))
    (tmp_path / "data.csv").write_text("joint,torque\nJ1,0.25\n")

    def identify(arm_name, serial, destination):
        calls.append((arm_name, serial))
        return {"arm": arm_name, "serial": serial, "local": str(tmp_path),
                "status": "program completed", "files": ["data.csv"]}
    monkeypatch.setattr(panel.service, "run_identification", identify)
    panel.serial.setText("LEFT_SN_TEST")
    panel.left_btn.click()
    pump_until(app, lambda: not window._tasks)
    assert calls == ["master", "stop_enable", "enable", "ready", "motion", ("left", "LEFT_SN_TEST")]
    assert window.master_started and window.servo_enabled and window.motion_started
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "0.25"


def test_dynamics_missing_vendor_blocks_startup(window, app, monkeypatch):
    panel = window.dynamics_panel
    monkeypatch.setattr(panel.service, "prepare_helpers", lambda: "/helpers")
    monkeypatch.setattr(arm.driver, "mc", lambda *a, **kw: (2, "missing vendor", ""))
    start = Mock()
    monkeypatch.setattr(arm.driver, "start_master_only", start)
    panel.right_btn.click()
    pump_until(app, lambda: not window._tasks)
    start.assert_not_called()
    assert "missing vendor" in panel.console.toPlainText()


def test_enable_status_wait_propagates_cancel_immediately(monkeypatch):
    monkeypatch.setattr(arm.driver, "status_word_slave", Mock(side_effect=arm.ArmOperationCancelled("cancel")))
    with pytest.raises(arm.ArmOperationCancelled):
        gui.arm_test.wait_enable_ready(timeout=60)


def test_window_exit_waits_for_capture_worker_and_cleans_service(window, app):
    capture = window.capture_panel
    gate = threading.Event()
    stopped = Mock()
    class Session:
        def stop_service(self):
            stopped()
    capture._touched_sessions.add(Session())
    thread = gui.TaskThread(lambda: gate.wait(2), parent=capture)
    capture.capture_thread = thread
    thread.start()
    try:
        window.close()
        app.processEvents()
        assert not window._allow_close
        assert window.isVisible()
        stopped.assert_not_called()
    finally:
        gate.set()
    pump_until(app, lambda: window._allow_close and not window.isVisible())
    assert capture._allow_close
    stopped.assert_called_once()


@pytest.mark.parametrize("first_fails", [False, True])
def test_servo_enable_can_be_repeated_without_overlapping_commands(
        window, app, monkeypatch, first_fails):
    import arm_enable

    gate = threading.Event()
    stop = Mock()
    enable = Mock(side_effect=lambda: (gate.wait(2), 14)[1])
    ready = Mock(side_effect=[arm.ArmDriverError("未使能就绪") if first_fails else 14, 14])
    monkeypatch.setattr(arm_enable, "stop_enable_all", stop)
    monkeypatch.setattr(gui.arm_test, "enable_all_joints", enable)
    monkeypatch.setattr(gui.arm_test, "wait_enable_ready", ready)
    window.master_started = True
    window.motion_started = True
    window.servo_enabled = True
    window._refresh_ui_state()
    assert window.btn_enable_servo.isEnabled()
    try:
        window.btn_enable_servo.click()
        assert not window.btn_enable_servo.isEnabled()
        window.btn_enable_servo.click()
        assert len(window._tasks) == 1
    finally:
        gate.set()
    pump_until(app, lambda: not window._tasks)
    assert window.servo_enabled is (not first_fails)
    assert window.btn_enable_servo.isEnabled()
    window.btn_enable_servo.click()
    pump_until(app, lambda: not window._tasks)
    assert enable.call_count == stop.call_count == ready.call_count == 2
    ready.assert_called_with(expected="0x1237", timeout=60)
    assert window.servo_enabled and window.motion_started
    assert window.btn_enable_servo.isEnabled()


def test_named_action_dispatch_and_fault_details(window, app, monkeypatch):
    window.motion_started = True
    monkeypatch.setattr(arm.driver, "action_server_count", lambda _: 1)
    sent = []
    monkeypatch.setattr(arm.driver, "run_task", lambda name, **kw: (
        sent.append(name) or True, "Result received - Success"))
    window.ed_task_name.setText("jiyi1/dual_arm_line1")
    window._run_named_task()
    pump_until(app, lambda: not window._tasks)
    assert sent == ["jiyi1/dual_arm_line1"]
    fake = window._new_driver()
    fake._read_log = lambda *a, **kw: "servo fault gate closed: 0x7500"
    monkeypatch.setattr(arm.driver, "run_task", lambda *a, **kw: (
        False, "Result received - Failure"))
    window._run_named_task()
    pump_until(app, lambda: not window._tasks)
    assert "0x7500" in window.log_view.toPlainText()
    assert "失败" in window.log_view.toPlainText()


def test_unavailable_action_server_never_sends_goal(window, app, monkeypatch):
    window.motion_started = True
    monkeypatch.setattr(arm.driver, "action_server_count", lambda _: 0)
    send = Mock()
    monkeypatch.setattr(arm.driver, "run_task", send)
    window._run_named_task()
    pump_until(app, lambda: not window._tasks)
    send.assert_not_called()
    assert "未就绪" in window.log_view.toPlainText()


def test_position_fill_does_not_block_gui(window, app, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(arm.driver, "joint_positions_all",
                        lambda: (gate.wait(2), [0.1] * 14)[1])
    try:
        window._fill_target_from_positions()
        app.processEvents()
        assert window._busy
    finally:
        gate.set()
    pump_until(app, lambda: not window._tasks)
    assert float(window.left_edits[0].text()) == 0.1


def test_repeated_control_clicks_do_not_overlap_or_accept_stale_success(window, app):
    gate = threading.Event()
    try:
        window._run_task("启动运控", lambda: (gate.wait(2), 1)[1])
        first = window._run_control("停止运控", lambda: 1)
        assert window._run_control("重复停止", lambda: 1) is None
        assert len(window._tasks) == 2
    finally:
        gate.set()
    pump_until(app, lambda: not window._tasks)
    assert not window.motion_started
    assert "已中断" in window.log_view.toPlainText()


def test_close_waits_for_worker_without_destroying_thread(window, app):
    gate = threading.Event()
    try:
        window._run_task("等待", lambda: gate.wait(2))
        started = time.monotonic()
        window.close()
        assert time.monotonic() - started < 0.2
        assert window.isVisible() and window._closing and window._tasks
    finally:
        gate.set()
    pump_until(app, lambda: window._allow_close and not window.isVisible())
    assert not window._tasks


def test_exit_cleanup_failure_keeps_window_available_for_retry(window, app, monkeypatch):
    window._remote_touched = True
    window.chk_stop_on_exit.setChecked(True)
    fake = window._new_driver()
    fake.stop_motion_control.side_effect = RuntimeError("cleanup offline")
    window.close()
    pump_until(app, lambda: not window._tasks and not window._closing)
    assert window.isVisible() and not window._closing
    assert "cleanup offline" in window.log_view.toPlainText()
    window.chk_stop_on_exit.setChecked(False)


def test_motion_interface_uses_mc_install_and_separate_file():
    assert arm.MOTION["setup"] == "~/mc/install/setup.bash"
    assert arm.MOTION["controller_dir"] == "~/mc/install/servo2plan/lib/servo2plan"
    assert arm.MOTION["joint_state_wait"] is True
    assert arm.MOTION["joint_state_topics"]
    assert (ROOT / "scripts" / "arm" / "motion_interface.yaml").is_file()
    summary = arm.interface_summary()
    assert summary["motion_file"].endswith("motion_interface.yaml")


def test_wait_joint_states_skips_when_disabled(monkeypatch):
    driver = arm.ArmDriver()
    monkeypatch.setitem(arm.MOTION, "joint_state_wait", False)
    monkeypatch.setattr(driver, "_topic_has_data",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应探测")))
    assert driver._wait_joint_states() is True


def test_wait_joint_states_requires_all_topics(monkeypatch):
    driver = arm.ArmDriver()
    monkeypatch.setitem(arm.MOTION, "joint_state_wait", True)
    monkeypatch.setitem(arm.MOTION, "joint_state_topics", ["/a", "/b"])
    monkeypatch.setitem(arm.MOTION, "joint_state_timeout_s", 0.2)
    monkeypatch.setitem(arm.MOTION, "joint_state_poll_s", 0.01)
    monkeypatch.setattr(driver, "_topic_has_data", lambda topic, timeout=None: topic == "/a")
    with pytest.raises(arm.ArmDriverError, match="/b"):
        driver._wait_joint_states()


def test_wait_joint_states_returns_when_all_published(monkeypatch):
    driver = arm.ArmDriver()
    monkeypatch.setitem(arm.MOTION, "joint_state_wait", True)
    monkeypatch.setitem(arm.MOTION, "joint_state_topics", ["/a", "/b"])
    monkeypatch.setitem(arm.MOTION, "joint_state_timeout_s", 1.0)
    seen = []
    monkeypatch.setattr(driver, "_topic_has_data",
                        lambda topic, timeout=None: seen.append(topic) or True)
    assert driver._wait_joint_states() is True
    assert seen == ["/a", "/b"]
