# scripts/arm · 机械臂分装线测试

面向 `192.168.88.20`（RK3588 / ROS 2 Humble / EtherCAT 双臂）的测试脚本。
配套计划：`~/桌面/机械臂分装线.plan`（已批量转为 **measurement** 用例）。

```
scripts/arm/
├── arm_interface.yaml  接口数据表（设备地址/关节映射/对象字典/比例/工具命令/阈值）★改这里
├── motion_interface.yaml  运控程序启动接口（mc/install 路径/命令/就绪判定）★运控改这里
├── arm_driver.py       底层通信驱动（启动时读取上面的 YAML；含运控程序启停）
├── arm_test.py         测试函数（全部返回数值，供计划按阈值判定）
├── arm_enable.py       后台 SSH 会话：全部关节使能/关闭
├── etherkit_master.py  后台主站/预检会话
├── cit_items.xml       运控 CIT 接口配置（正方向小角度校验等 command 来源）
├── components_config.yaml  伺服层部件安装配置模板（启动前自动检查/修复）
└── README.md           本文件
```

> **设备/ROS 接口在 `arm_interface.yaml`**：设备 IP/账号、`mc_run.sh` 路径、
> ROS action/话题模板、示教按键值、关节→从站映射、关节限位、SDO 对象与比例系数、
> 零位写入命令模板、建议阈值。
>
> **运控程序启动接口单独在 `motion_interface.yaml`**：安装目录（`~/mc/install`）、
> 三层节点命令、就绪关键字、等待关节状态等。切换安装目录只改这一个文件。
>
> 查看当前生效的接口：`python3 scripts/arm/arm_driver.py --check-config`

---

## 1. 为什么用命令接口，而不是 CIT 的 HTTP

| 方案 | 结果 |
|---|---|
| CIT `POST /api/request` | 同步返回，但长任务受服务端超时（默认 30 s）限制；极限运动/往返精度动辄几十秒到几分钟，且需要自己轮询 |
| **SSH 直接执行命令（本方案）** | `ros2 action send_goal` / `ros2 run ... action_client` 本身阻塞到 goal 结束，退出码即结果 —— 天然“等待返回”，无需异步/轮询 |

因此 `arm_driver.py` 全部通过 paramiko 建立 SSH，执行：

- `bash ~/script/mc_run.sh '<ros2 命令>'`（与 CIT 测试项同一环境，`ROS_LOCALHOST_ONLY=1`）
- `sudo -n ethercat upload -p <从站> -t <类型> <对象> <子索引>`（只读伺服寄存器）

CIT 的 HTTP 只保留一个 `driver.cit(...)` 备用方法，默认不用。

---

## 2. 依赖与连接

```bash
pip install paramiko        # requirements.txt 已包含
```

连接参数在 `arm_interface.yaml` 的 `device` 段（改文件即可，无需改代码）：

```yaml
device:
  host: 192.168.88.20
  port: 22
  user: zioneer
  password: "123"
  mc_run: "~/script/mc_run.sh"
  connect_timeout: 6.0
  cmd_timeout: 30.0
```

```bash
python3 scripts/arm/arm_driver.py --check-config   # 打印当前生效的接口表
```

临时连别的机器也可以不改文件：

```python
from arm_driver import configure
configure(host="192.168.88.21", password="123")
```

### 2.1 `arm_interface.yaml` 各段说明

| 段 | 内容 | 什么时候改 |
|---|---|---|
| `device` | IP/端口/账号/`mc_run.sh` 路径/超时 | 换机器、改密码 |
| `ros` | action 名与类型、`arm_mask` 位、话题模板、示教按键值、run_mode 值、健康检查期望值 | 运控接口升级、话题改名 |
| `joints` | 单臂关节数、关节→从站映射、关节名、关节限位 | 换机型、拓扑变化、限位调整 |
| `sdo` | 对象字典：索引/子索引/类型/比例系数/单位；`unsupported` 记录不可用对象 | 换伺服、固件升级 |
| `tools` | 零位写入命令模板、ethercat 命令名 | 厂商工具参数变化 |
| `motion_interface` | 指向独立运控接口文件 `motion_interface.yaml` | 运控工作空间迁移、节点名变化 |
| `thresholds` | 建议阈值（温度/电压/容差/速度/重复精度…） | 判定标准调整（计划里的阈值仍以计划为准） |

> 比例系数就写在 `sdo` 里：`scale: 0.1` 表示原始值 ×0.1 = 工程值。
> 例如温度 `0x2403` 原始 4637 → `scale 0.01` → 46.37 ℃。

前提：`zioneer` 具备 `sudo -n`（NOPASSWD）权限；`~/script/mc_run.sh` 存在。

---

## 3. 关节与从站映射（已实测确认）

| 关节编号 | 臂 | 从站位置 |
|---|---|---|
| 1 … 7 | 左臂 J1…J7 | p1 … p7 |
| 8 … 14 | 右臂 J1…J7 | p8 … p14 |

验证方法：读 `0x6064` 位置实际值，与 `joint_states.position` 对比（本机左臂 J1=0.2 rad ↔ p1=834430 counts，比例恒为 4 172 150 counts/rad）。

> 旧版 CIT 配置里写的是 `-p 2` 对应“左臂1轴”，那是**错的**（位置 0 是 HUB，伺服从 1 开始）。

---

## 4. 用到的寄存器

| 对象 | 类型 | 含义 | 换算 |
|---|---|---|---|
| 0x6041 | u16 | 状态字 | 0x1237 = OP_ENABLED |
| 0x603F | u16 | 故障码 | 0x8611 = 跟随误差 |
| 0x6077 | i16 | 力矩实际值 | /10 → 额定千分比 |
| 0x6064 | i32 | 位置实际值 | counts |
| 0x606C | i32 | 速度实际值 | counts/s |
| 0x2401 / 0x2402 | i16 | 力矩传感器原始值 | 正常 2200…2800 |
| 0x2403 | u16 | 驱动器温度 | /100 → ℃ |
| 0x2404 | u16 | 母线电压 | /1000 → V |
| 0x1008 / 0x1009 / 0x100A | str | 设备名 / 硬件版本 / 软件版本 | 例：`ROKAE xServo` / `1.0` / `0A24` |
| 0x1018:1…4 | u32 | 厂商ID / 产品码 / 修订号 / 序列号 | 报告按 `0xXXXXXXXX` 字符串输出 |

**没有电流**：ROKAE xServo 未实现 0x6078（电流）/0x6079（母线电压），SDO 返回 `0x06020000 对象不存在`。需要电流只能用 0x6077 力矩千分比代偿，或改驱动器 PDO 映射。

---

## 5. 测试函数清单

返回类型全部是数字（int/float），计划里用「判定方式 + 阈值」判定，报告会记录实测值。

| 函数 | 参数 | 返回 | 建议阈值 | 对应计划 |
|---|---|---|---|---|
| `measure_safety_ready` | — | int 满足数 | 等于 3 | 安全防护措施到位 |
| `measure_arm_id` | `arm` | int 序列号 | 大于 0 | 左/右臂 ID 码扫描 |
| `measure_ethercat_nodes_online` | — | int 从站节点数 | 大于 13 | 各节点在线检测（`sudo ethercat slaves`） |
| `enable_all_joints` | — | int 使能关节数 | — | 启动主站后后台使能全部 14 轴（Action） |
| `wait_motors_status_word` | `expected, timeout` | int 就绪轴数 | 等于 14 | 等待 14 轴 0x6041 状态字=0x1208 |
| `measure_nodes_online` | — | int ROS 节点数 | 大于 10 | 备用（ROS 节点检测） |
| `measure_joint_jog_panel` | `joint, speed` | int 1 | 等于 1 | 关节 N 人工点动到零位（弹面板，不标零；15050） |
| `measure_joint_zero_align_panel` | `joint, speed` | float rad | -0.02~0.02 | 关节 N 人工点动+标零（弹面板，备用） |
| `measure_joint_zero_align` | `joint` | float rad | -0.02~0.02 | 关节 N 自动对齐（备用） |
| `measure_joint_zero_write` | `joint` | int counts | -200~200 | 关节 N 零位写入（15060） |
| `measure_force_sensor` | `joint, channel` | int | 2200~2800 | 关节 N 力传感器 0/1 值 |
| `measure_force_change` | `arm, duration` | int 峰峰值 | 大于 0 | 力传感器受力值变化 |
| `measure_positive_direction` | `delta` | float rad（14 轴最小位移） | 大于 0.05 | 正方向复核 |
| `measure_motion_control_start` | — | int 1 | 等于 1 | 启动 `~/mc/install` 运控程序 |
| `measure_positive_direction_small_angle` | `timeout` | int 1 | 等于 1 | 执行 CIT 小角度校验并记录原位 |
| `measure_motion_restore` | `speed, timeout` | float rad（最大回位误差） | 小于 0.05 | 恢复动作前原位 |
| `measure_motion_control_stop` | — | int 1 | 等于 1 | 停止运控节点（保留主站） |
| `measure_identity` | `joint, field` | str | —（读失败即异常） | PN/SN/vendor→`0xXXXXXXXX`；SW=0x100A、HW=0x1009 |
| `measure_move_limit` | `joint, direction, speed` | float rad | 限位 ±0.06 | 运动到正/负极限 |
| `measure_limit_angle` | `joint` | float rad | 限位 ±0.06 | 正/负极限角度 |
| `measure_joint_speed` | `joint, delta, speed` | float rad/s | 大于 0.1 | 运动速度 |
| `measure_joint_home` | `joint` | float rad | -0.03~0.03 | 关节 N 归零 |
| `measure_joint_temperature` | `joint` | float ℃ | 0~75 | 关节 N 温度 |
| `measure_move_delta` | `joint, delta` | float rad | 0.95~1.05 | 正方向运动 1 弧度 |
| `measure_repeatability` | `arm, cycles` | float rad 峰峰值 | 小于 0.02 | 往返 N 次重复定位精度 |
| `measure_teach_button` | `arm` | int run_mode | 等于 3 | 示教按钮有效 |
| `measure_collision_state` | `arm` | int | 等于 0 | 碰撞急停检测 |
| `probe_device` | — | int 从站数 | 等于 15 | 自检用（未进计划） |
| `run_dual_arm_line1` | `timeout` | int 1/0 | 等于 1 | 备用（整臂线束动作） |

> `measure_joint_speed` 会自己做一次小位移（朝空间更大的一侧）并采样峰值速度，因此不依赖上一条用例是否刚运动过。

主站启停（`scripts/arm/etherkit_master.py`）：

- `start_master()`：Action 用例调用，后台 SSH 启动预检/主站
  `sudo /opt/zioneer_etherkit/bin/zioneer_etherkit -n
  /opt/zioneer_etherkit/etc/etherkit/QUALITY_INSPECTION.xml`，自动回答 sudo 密码；
  同时注册测试结束清理回调。
- `stop_master()`：整个测试计划结束时由引擎清理回调调用，发送 Ctrl+C 并关闭 SSH。

全部使能（`scripts/arm/arm_enable.py`）：

- `start_enable_all()`：后台独立 SSH 会话，循环对 J1..J14 下发
  `cia402_tools -s <joint> -m 8`；命令完成后会话保持，测试结束/人工停止时关闭。
- `stop_enable_all()`：引擎清理回调，发送 Ctrl+C 并关闭后台会话。

`15040 通信检测` 的执行顺序：启动主站 → 延时 → 等待 14 轴状态字=0x1208 → 全部使能 →
各节点在线检测（EtherCAT 从站数）。

### 5.1 运控程序启停（`~/mc/install`）

配置在独立文件 `scripts/arm/motion_interface.yaml` 的 `motion` 段
（`arm_interface.yaml` 只保留 `motion_interface: motion_interface.yaml` 指针）。
切换回 `~/install` 时，只改该文件的 `setup` 与 `controller_dir`。启动顺序：

1. EtherCAT 主站（已在运行则跳过）
2. 伺服层：`ros2 run servo2plan servo_motor_jiyi1_dualArm_Waist_node`
3. **等待 `/mc/*/joint_states` 真正发布**（`joint_state_wait`，默认 90s 超时）
4. 控制器：`~/mc/install/servo2plan/lib/servo2plan/hw_controller_manager.py`
5. 运控层：`HW_TYPE=jiyi1_robot ros2 run mc_task_manager robot_app`

第 3 步是 `~/mc/install` 必需的：伺服层打印 `Shared memory attached` 后还要约
20~30s 才发布关节状态，而 `robot_app` 的组件等待只有 5s；提前启动会报
`Not found the following components` 并退出。等待话题可在 `joint_state_topics`
中按实际安装部件调整。

等待 `robot_app` 输出 `Components in robot are ready` 后返回。启动伺服层前会检查目标机
`/etc/servo/config/components_config.yaml`：缺失或 YAML 损坏时，自动用本目录的
`components_config.yaml` 模板修复（需要 sudo）。停止时默认**只停运控节点**，
保留 EtherCAT 主站，避免后续 SDO 用例（温度/身份/零位）失效；异常中止由清理回调兜底。

`15085 正方向复核` 的执行顺序：

1. `measure_motion_control_start`：启动运控
2. `measure_positive_direction_small_angle`：记录原位 → 执行 `cit_items.xml` 的
   `mc.arm.small_angle`（正方向小角度校验）
3. `test_set_picture_show`：弹窗确认动作与 `arm_test_right_place.jpg` 一致
4. `measure_motion_restore`：双臂慢速回到原位（判定最大误差 < 0.05 rad）
5. `measure_motion_control_stop`：停止运控节点

`cit_items.xml` 中 `command` 的 `bash ~/script/mc_run.sh '...'` 会被自动解包，直接在当前
`motion.setup` 环境（默认 `~/mc/install/setup.bash`）里执行，不要求目标机存在 `mc_run.sh`。

---

## 6. 单独调试

```bash
cd /home/jyzn/Test_management_sw
python3 -m scripts.arm.arm_driver            # 连接自检：从站数/节点数/关键寄存器
python3 - <<'PY'
import sys; sys.path.insert(0, "scripts/arm")
import arm_test
print("从站:", arm_test.probe_device())
print("EtherCAT 节点:", arm_test.measure_ethercat_nodes_online())
print("ROS 节点:", arm_test.measure_nodes_online())
print("J1 温度:", arm_test.measure_joint_temperature(joint=1))
print("J1 力矩传感器0:", arm_test.measure_force_sensor(joint=1, channel=0))
PY
```

> 单独调试时 `test_api` 不存在，脚本会自动退化为 `print`，不影响函数逻辑。

---

## 7. 注意事项

1. **不要在运动中跑 SDO 监视**（`ethercat upload` 与实时 PDO 争用邮箱通道），本包只在读取用例里用 SDO。
2. **零位写入**默认调用 `/opt/zioneer_etherkit/bin/cia402_tools -s <从站> -z`（homing/写零）。15050 只做人工点动，不写零；写零在 15060。
3. **点动必须用 `cia402_csp_tools -s <关节> -v <增量>`**。`cia402_tools` 没有 `-v` 选项（只有 `-m/-e/-d/-z/-i/-l/-t`），写成 `cia402_tools ... -v` 会直接报 `invalid option -- 'v'`，表现为“已使能但关节不动”。
4. **`master_tcp: connect: No route to host`** 是主站访问腰部控制器 `192.168.88.30` 失败，本机没有腰部时属正常提示，不影响手臂。
5. **运动目标会被夹到关节限位内**（`send_arm_goal` 内做 clamp），但仍需确保机械行程内无人/无障碍。
6. **软限位现状**：伺服 0x607D（软件位置限位）实测为 ±2 000 000 000 counts（约 ±479 rad），等于未启用；驱动器本身不会在机械行程处停。只有运控层（`joint_limit_check.xml` / 本包 `send_arm_goal` 的 clamp）会把目标限制在硬限位内缩 10 mrad。直接用 `cia402_csp_tools` 点动**没有软限位**，一直按住会顶到机械硬限位。
7. 失败语义：通信/运动失败 → 抛异常 → 用例 FAIL；数值异常（温度高、偏差大）→ 返回数字由阈值判定 FAIL。
8. 计划的备份：转换脚本会生成 `机械臂分装线.plan.bak-<时间戳>`，需要还原直接覆盖回去即可。

---

## 8. 计划转换

```bash
python3 tools/plan_arm_measurement.py --dry-run   # 只看映射统计
python3 tools/plan_arm_measurement.py            # 就地转换（自动备份）
```

转换做了三件事：

1. `type: action` → `measurement`（237 条全部）；
2. 填 `config.script / function / params / returns`，每条都有数值返回 + 判定阈值；
3. 重设 `timeout_ms`（原 5 s 对运动类完全不够）：读 20 s、单关节运动 180 s、极限 240 s、往返 600 s；运动类失败策略 `pause`，读取类 `continue`。
