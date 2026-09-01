# 地盘标定 —— RViz2 验证视图集成说明

配合文件：

- `scripts/地盘标定/calib_rviz.py` — 验证视图弹窗脚本（Action 用例可调用）
- `scripts/地盘标定/calib_rviz.rviz` — RViz2 配置模板（话题/固定帧在文件顶部注释有说明）
- `scripts/地盘标定/差速AGV多传感器外参标定工作站设计与操作规范.docx` — 标定工作站设计与 SOP

## 一、部署前只需确认三处

1. **calib_rviz.py 顶部 CONFIG**
   确认 `ros_distro` / `ros_setup`、`ros_domain_id`（与 RK3588 一致）、`rviz_config` 文件名。

2. **calib_rviz.rviz 话题与固定帧**
   - `Global Options -> Fixed Frame`（默认 `base_link`）→ 改为 RK3588 发布的坐标系
   - 各 Display 的 `Topic` → 与 RK3588 实际发布话题一致：
     - `/calib/sensor_points` — 传感器点云（PointCloud2）
     - `/calib/template` — 标定模板点云（默认关闭，按需启用）
     - `/calib/lidar_outline` — 雷达 2D 轮廓（MarkerArray，红色）
     - `/calib/camera_outline` — 相机水平投影 2D 轮廓（MarkerArray，绿色）
   - 红/绿颜色由 RK3588 在 Marker 消息里设置，RViz 只订阅显示，此处无需改。

3. **测试计划**
   在「标定」用例之后加 Action 用例：脚本选 `calib_rviz.py`，函数选 `open_rviz_view`。

## 二、函数一览（供计划配置用）

| 函数 | 作用 | 常用参数 |
| --- | --- | --- |
| `open_rviz_view` | 启动 RViz2 验证视图 | `rviz_config`、`ros_domain_id`、`display`、`kill_before_start` |
| `wait_rviz_close` | 阻塞等待操作员关窗 | `timeout_s`（0=一直等）、`auto_close` |
| `capture_rviz_screenshot` | 对 RViz2 窗口截图存 PNG | `save_dir`、`filename`、`display` |
| `save_calib_result` | 保存标定结果 JSON | `sn`、`passed`、`rmse`、`threshold`、`extrinsic`、`note` |
| `collect_calib_result` | 汇总标定结果并保存 JSON（返回 dict，供 Action 用例进报告/上报） | 同 `save_calib_result`，另有 `calib_input_file` |
| `verify_and_save` | Step4 一键流程：查看→截图→保存→自动关闭 | `wait_s`、`screenshot`、`auto_close` 及其余透传 `save_calib_result` |
| `close_rviz_view` | 关闭已打开的 RViz2 视图 | — |
| `rviz_is_running` | 检查 rviz2 进程是否在运行（辅助） | — |

典型动作链（三个 Action 用例）：

```
open_rviz_view        # 弹窗，操作员确认轮廓重合
verify_and_save       # 等待查看 + 截图存档 + 保存外参 JSON + 自动关闭
```

- 若需等待操作员手动关窗后再判定，用 `wait_rviz_close({"timeout_s": 0})` 替代 `verify_and_save` 的自动关闭。
- 标定数据默认存 `<软件根目录>/data/calib/`，截图默认存 `<软件根目录>/data/reports/`，均可用参数或 CONFIG 覆盖。

## 四、标定参数进报告与后端上报

`collect_calib_result` / `verify_and_save` 返回的 dict（`sn`/`passed`/`rmse`/`threshold`/
`extrinsic`/`note`）会：
- 显示在**本地 HTML 报告**该用例的「参数 / 值」子表中；
- 随**逐用例 JSON 上报**（计划设置 → 逐用例 JSON 上报，见《服务器对接接口文档》第 9 节）
  一并发送到后端，可入库保存标定数据。

标定参数的来源优先级：**用例 params（Action 传入）<< `calib_input_file` 指定的标定算法原始
结果 JSON（如 RK3588 落盘文件）<< 软件全局变量 `calib.*`（脚本用 `set_variable` 写入）**。

例如在标定算法脚本中写入：

```python
test_api.set_variable("calib.rmse", 0.006)
test_api.set_variable("calib.threshold", 0.01)
test_api.set_variable("calib.passed", True)
test_api.set_variable("calib.extrinsic", {"x": 0.0, "y": 0.0, "z": 0.05,
                                          "roll": 0.0, "pitch": 0.0, "yaw": 0.0})
```

之后 `verify_and_save` 无需再传参即可把这些标定参数写入报告和上报报文。

## 三、依赖与注意

- 上位机需安装 rviz2：`sudo apt install ros-humble-rviz2`（发行版与 CONFIG.ros_distro 对应）。
- RK3588 与上位机需在同一 ROS 网络：`ROS_DOMAIN_ID` 一致、网络互通。
- 截图依赖 `xwd`（x11-apps）与 `wmctrl`；未安装时 `capture_rviz_screenshot` 返回空串并在日志提示。
- `calib_rviz.py` 用 `import test_api` 与软件内置 API 对接（`test_api.log` 打日志），必须作为计划内 Action 用例运行，不可脱离软件单独执行。