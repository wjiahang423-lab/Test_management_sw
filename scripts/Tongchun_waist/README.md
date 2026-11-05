# 腰部同川电机测试脚本

本目录包含 `pysoem` EtherCAT 底层驱动和测试函数。

## 测试函数

`tongchuan_test.py` 提供以下函数：

- `test_connect(interface="enp2s0", sudo_password="...")`：连接网络，返回 `bool`。
- `test_disconnect()`：断开主站，返回 `bool`。
- `test_read_nodes()`：返回从站数量。
- `test_enable(node)`：使能指定 EtherCAT 从站，返回 `bool`。
- `test_set_torque_mode(node, torque)`：设置扭矩模式并写入扭矩。
- `test_zero_position(node)`：写入 `0x3801 = 5` 标零，返回 `0x6064` 当前位置脉冲值 `float`。
- `test_read_angle(node)`：读取 `0x6064` 并换算输出端角度（含减速比），返回 `float`，单位为度。
- `test_read_info(node, address)`：读取 SDO 字典地址，返回字符串。

## 角度换算（含减速比）

与 `position_controller(同川)` 一致：

```
输出端角度(°) = 0x6064脉冲 × 360 / (131072 × GEAR_RATIO)
GEAR_RATIO = 101.0
```

驱动里 `GEAR_RATIO` 在 `tongchuan_driver.py` 顶部，可按实际减速机修改。
`read_position()` 仍返回原始脉冲；`read_angle()` 返回输出端角度。

## 节点下标（重要）

`node` 是 **pysoem 从站下标，从 0 开始**，不是 CANopen 的 1-based 节点号：

- 第 1 个电机 → `node = 0`
- 第 2 个电机 → `node = 1`

`腰部分装线测试.plan` 已按 0/1 配置。

## EtherCAT 权限（连接失败的首要原因）

`pysoem.Master.open()` 需要 root 或 **CAP_NET_RAW**。只执行
`ip link set enp2s0 up` 是不够的，驱动会报：

```
ConnectionError: could not open interface enp2s0
```

给运行测试软件的 Python / EOL 可执行文件授权：

```bash
# 源码运行（开发机）
sudo setcap cap_net_raw,cap_net_admin+ep "$(readlink -f "$(which python3)")"

# 打包版（设备）
sudo setcap cap_net_raw,cap_net_admin+ep /home/user/eol/EOL
```

检查：

```bash
getcap "$(readlink -f "$(which python3)")"
ip -br link show enp2s0     # 必须是 UP，且不能是 NO-CARRIER
```

还要确认：

1. EtherCAT 网线插在独立的 `enp2s0` 上（不要和普通上网口混用）。
2. 从站已上电，网口 Link 灯亮。
3. `ip -br link show enp2s0` 显示 `UP` 且没有 `NO-CARRIER`。

如果权限不足或找不到从站，运行日志会输出具体原因，例如：

```
EtherCAT 连接失败：ConnectionError: could not open interface enp2s0；EtherCAT 需要 root 或 CAP_NET_RAW ...
EtherCAT 连接失败：网卡 enp2s0 上未发现 EtherCAT 从站
```

## 地址格式

`address` 支持整数和十六进制字符串，例如 `0x1008` 或 `"0x1008"`；
前后空格会自动去掉（计划里 `" 0x1008"` 也能正常读取）。

## 扭矩模式 / 使能（重要）

TC600E 的 `0x6040`（控制字）、`0x6060`（模式）、`0x6071`（目标扭矩）默认在
RxPDO `0x1601` 中。只通过 SDO 写这些对象，驱动器**不会**进入使能状态；必须先：

1. `config_init()` → `config_map()` → `config_dc()`
2. 确保进入 **SAFEOP**，先连续收发 **200 帧**过程数据（同川要求，否则拒绝 OP）
3. `master.state = OP` → `write_state()` → `state_check(OP, 5000)`
4. 后台线程按 5ms 周期持续 `send_processdata()` / `receive_processdata()`
5. 通过 PDO 执行同川使能序列：
   `0x0000(Disable Voltage) → 0x0006 → 0x0007 → 0x000F`
6. 检查 `0x6041` 状态字 bit2=1（Operation enabled）

当前 `tongchuan_driver.py` 已按上述流程实现：

- `connect()` 进入 OP 并启动后台 PDO 线程
- `set_torque_mode(node, torque)` 通过 PDO 写模式/扭矩并等待使能
- 返回 `False` 表示未真正使能（不再“假成功”）
- `close()`/`disconnect()` 停止 PDO 线程并写 `0x0007` 禁用运行

如果 `connect()` 报“从站未能进入 OP 状态”，通常是：

- `enp2s0` 为 `NO-CARRIER`（网线/从站电源）
- 之前反复请求 OP 失败，从站停在 SAFEOP/AL 错误状态  
  → **给 EtherCAT 从站断电重启**后再试
- 驱动器 STO/安全转矩关断未释放

## 手动拖动 / 摩擦力补偿

扭矩模式下想手动搬动电机，先确认：

1. `0x6060 = 4`，且 `0x6041` bit2 = 1（Operation enabled）
2. 如果电机带抱闸，先强制打开抱闸：`Pr8.30 / 0x381E` 写 `128`
   （`test_release_brake(node)`，不用时写 0 恢复）
3. 仍有明显阻力时，启动力矩前馈：

```
0x6071 = Fc * sign(v) + B * v
```

- `v` 来自 `0x606C`（puu/s），换算：`v(rad/s) = puu/s × 2π / 131072`
- `Fc`（库伦摩擦）函数默认 5，`B`（粘性摩擦）默认 10；计划里使用变量
  `friction_fc` / `friction_b`（默认 1.0 / 0）
- 默认单位 `unit="0.1nm"`：Fc=5 → 0.5 Nm，B=10 → 1.0 Nm·s/rad
- 也支持 `unit="nm"`（Nm）和 `unit="raw"`（0x6071 原始值，0.1% 额定扭矩）
- 换算会实时读取驱动器的 `0x6076`（电机额定扭矩，mNm），不写死 2700
- `deadband` 函数默认 0.15 rad/s（计划里也是 0.15），低于此速度不补偿
- `max_raw` 函数默认 150；计划里用变量 `friction_max_raw`，默认 80

计划里已在两个关节的扭矩模式后增加“设置摩擦力补偿”用例，参数来自全局变量：

```
test_set_friction_compensation(node, fc=friction_fc, b=friction_b,
                               max_raw=friction_max_raw, deadband=0.15,
                               v_start=0.3, unit="0.1nm")
```

关闭：`test_disable_friction_compensation(node)`。

> `unit="0.1nm"` 时：`raw = torque_Nm × 100000 / rated_mNm`；
> `unit="nm"` 时：`raw = torque_Nm × 1000000 / rated_mNm`。

## 抱闸保持时间与恢复

- `0x381E = 128`（Pr8.30）**不是定时打开**，会一直保持，直到：
  - 手动写 `0x381E = 0`，或
  - 伺服使能后再次关闭（手册 6.7：`pr8.30` 自动变回 0）
- `Pr8.31` 只是“打开抱闸时 48V 通电时间”，不是保持时间。
- 伺服使能时由驱动器正常抱闸逻辑控制：伺服 ON 后约 `Pr1.54`（默认 200ms）
  打开，伺服 OFF 后立即关闭（当前转速低于 `Pr1.56` 默认 6000rpm）。

当前 `腰部分装线测试.plan` 每个关节的顺序（以关节1为例）：

```
强制打开抱闸(0x381E=128)
→ 延时 300ms
→ 设置扭矩模式(能，目标扭矩0)
→ 延时 500ms
→ 设置摩擦力补偿
→ 手动调节0位(图片)
→ 标定0位(0x3801=5)
→ 手动转到俯极限 → 读取角度
→ 手动转到仰极限 → 读取角度
→ 手动回0位
→ 恢复抱闸并关闭扭矩(0x0000 + 摩擦补偿关闭 + 0x381E=0)
```

> 注意：标定0位后**没有立即恢复抱闸**，而是等该关节的俯/仰极限角度都读完，
> 再执行“恢复抱闸并关闭扭矩”。这样手动搬动角度时抱闸是打开的。

## 摩擦补偿参数（现场自己调）

计划 `腰部分装线测试.plan` 的全局变量里新增：

| 变量 | 默认 | 说明 |
|------|------|------|
| `friction_fc` | 1.0 | 关节1库伦力，单位 0.1 Nm |
| `friction_fc_1` | 1.3 | 关节2库伦力，单位 0.1 Nm |
| `friction_b` | 0 | 粘性力，单位 0.1 Nm·s/rad |
| `friction_max_raw` | 80 | 最大输出（0x6071 raw），80≈0.216 Nm（按 2.7 Nm 额定） |

在【全局变量管理】里修改，改完重新运行计划。建议从 0 开始，每次只加一点：

1. 先加 `friction_fc`（例如 1 → 2 → 3）
2. 不够再加 `friction_b`
3. 需要更大助力时再提高 `friction_max_raw`（50 → 80 → 100）

摩擦补偿需要伺服处于扭矩模式并使能；计划里的“设置扭矩模式(0扭矩)”只负责使能，
目标扭矩固定 0，不会主动控制运动。补偿本身还有：

- 死区 `0.15 rad/s`（低于此速度不出力）
- 渐入 `0.3 rad/s`
- 最大输出限幅 `friction_max_raw`

如果一推就自己加速：降低 `max_raw` 或 `fc/b`；如果推着仍吃力：小幅提高。
