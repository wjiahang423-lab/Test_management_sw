# 头部意优电机测试脚本

本目录去掉了原调试器的 Qt 页面，只保留 ZLG CANopen 底层驱动和简单测试函数。

## 测试函数

- `test_connect(channel=0)`：连接 CAN 总线，返回 `bool`。
- `test_enable(node=1)`：使能 CANopen 节点，返回 `bool`。
- `test_zero_position(node=1)`：执行 `0x2262` 机械标零，返回当前位置脉冲值 `float`。
- `test_read_angle(node=1)`：读取 `0x6064` 并按 `0x6091:02` 换算角度，返回 `float`，单位为度。
- `test_read_info(node=1, address="0x1008")`：读取 SDO，返回字符串。

节点使用 CANopen 的 1-based node ID。SDO 地址支持 `0x1008`、`"0x1018:04"` 和 `"0x1018/04"` 写法。

依赖 `python-can`、`zlgcan`，并使用 `Test_management_sw/library` 下的 ZLG 动态库。
