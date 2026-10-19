# 腰部同川电机测试脚本

本目录包含 `pysoem` EtherCAT 底层驱动和测试函数。

## 测试函数

`tongchuan_test.py` 提供以下函数：

- `test_connect(interface="enp2s0")`：连接网络，返回 `bool`。
- `test_enable(node)`：使能指定 EtherCAT 从站，返回 `bool`。
- `test_zero_position(node)`：写入 `0x3801 = 5` 标零，返回 `0x6064` 当前位置脉冲值 `float`。
- `test_read_angle(node)`：读取 `0x6064` 并换算当前角度，返回 `float`，单位为度。
- `test_read_info(node, address)`：读取 SDO 字典地址，返回字符串。

`node` 是 EtherCAT 从站下标，通常第一个电机为 `0`。`address` 支持整数和十六进制字符串，例如 `0x1008` 或 `"0x1008"`。

运行测试管理软件时，请先执行 `test_connect()`，再调用其它函数。EtherCAT 通常需要 root 权限或网卡 capability。
