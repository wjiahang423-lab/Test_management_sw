# 运控部署与动力学辨识

上位机入口是 `tools/arm_control_gui.py` 的“动力学辨识与部署”页。
连接参数来自 `scripts/arm/arm_interface.yaml`，默认设备为 zioneer@192.168.88.20。

## 操作

1. 停止运控后，点击“设备点位修正并下载包”。程序核实 Home XML 引用的
   `move_left_arm_home.yaml` 和 `move_right_arm_home.yaml`，备份左臂 YAML，
   仅将左臂 `request.goal` 对齐右臂的七个零值，保留左右臂 location。
   XML 仍引用原来的 MetaMove，编码器标定值不变。右臂目标不是全零或结构不符时停止修改。
2. 设备备份包含 install、设备原版 `run_arm_dynamics_identification.py`（存在时）、
   `analog_output_snap.py`、辨识入口封装和来源清单，并下载至本地结果目录。
   SHA256 校验失败则不保留下载包；设备缺少原厂脚本时清单明确标记缺失。
3. 也可用“从本地 install 制作包”选择原厂 install 和辨识脚本。
   本地源文件不变，在副本中修改 Home。缺少辨识文件的包只供查看，不可用于辨识。
4. 选择部署包后“检查部署包”。“一键部署”上传、校验、预检，先备份旧 install 和脚本，
   再切换 install；运控还在运行时拒绝部署。失败回滚原目录。旧版保存在设备
   `~/.arm_workbench/<批次>/previous/`。不会覆盖 `/etc/servo/config`。
5. 填写机械臂编号，点击左臂或右臂“一键启动并辨识”。勾选“辨识前部署”时先部署，
   然后依次检查原厂辨识文件、启动主站、伺服使能、确认 `0x1237`、启动运控和辨识。
   运控已运行时复用该进程并检查使能状态，不并发启动第二个使能写入进程。
6. 原厂程序出现 `Type APPLY:` 后，页面启用发布/跳过按钮。检查原厂输出后，
   操作员选择发布才发送 APPLY。程序退出码为零只表示正常退出，验收是否通过以原厂结果为准。
7. 本次新增/变更的 `experiment_data` 文件、校验清单、日志下载到本机，页面预览 CSV
   前 500 行、64 列。完整数据可点击“打开本地结果目录”。没有新数据不会显示为成功。

“停止辨识 / 取消运动”发送 ROS cancel_goal，并终止本次辨识进程组。
启动、部署和辨识与运控页共用任务队列，不能同时执行。
设备切换 install 的短暂提交阶段不响应中途取消，完成或回滚后才退出。

## 力传感器数据

“读取左臂/右臂力传感器”使用附带的 analog 脚本采集 10 秒，显示均值和原始 CSV。
只调用采集/统计函数，不调用写入 `torque_sensor_zero.yaml` 的入口。
附带脚本来源：`桌面/项目/机器人手臂调试/read_seneor_data/analog_output_snap.py`。
该版本实际使用 SLAVE_MAX=22、PDO_SLAVE_SIZE=512、左/右从站基址1/8；
原脚本说明文字仍有旧的17×256布局。使用前须核实设备底软的真实 PDO 布局，不能仅靠文件大小判断版本。

## 文件与验证边界

- `bundle.py`：包检查、点位修正、打包、部署及回滚，可独立在设备执行。
- `identification_runner.py`：调用原厂辨识程序并记录本次结果；不是原厂动力学模型和轨迹的替代实现。
- `service.py` / `panel.py`：SSH 流程与页面。
- `analog_output_snap.py`：随软件附带的原采集程序。

SOP 来源：`下载/手臂/检测SOP合集.xlsx`，“整机标定SOP” C14:C18（动力学）、
C8:C13（力传感器）。本工具没有修改 SOP。

本次设备访问因自动审批服务连接中断而未执行；未修改设备文件，未下载设备原 install，
未运行真实辨识或发布参数。离线测试使用模拟设备与临时文件。原厂辨识文件和现场结果仍需设备条件恢复后核实。

## 本地回归

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_arm_control.py tests/test_robot_workbench.py
```
