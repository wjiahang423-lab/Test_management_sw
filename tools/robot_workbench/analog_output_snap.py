#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analog_output 采集与统计工具

从 EtherCAT PDO 共享内存 (/dev/shm/pdo_shared_mem) 连续采集 10 秒的
analog_output1 (0x2401) 和 analog_output2 (0x2402) 数据。

内存布局（依据 pdo_msg.h 推算）：
  - 共享内存总大小 = SLAVE_MAX(17) * PDO_SLAVE_SIZE(256) = 4352 字节
  - 从站 i 起始偏移 = i * 256
  - rokae 从站内 tx 区偏移 = 72 (rx_data[72] 在前)
  - analog_output1 在 rokae_txpdo_t 内偏移 = 16
  - analog_output2 在 rokae_txpdo_t 内偏移 = 18
  => 关节绝对偏移:
     analog_output1 = slave_index * 256 + 72 + 16 = slave_index * 256 + 88
     analog_output2 = slave_index * 256 + 72 + 18 = slave_index * 256 + 90
  类型: int16_t 小端有符号

从站索引 (slave_type_t): JUCTION=0, ARM_L_1..L_7 = 1..7, ARM_R_1..R_7 = 8..14

功能：
  1. 连续采集 10 秒的 analog_output1 和 analog_output2 数据
  2. 保存原始数据到 CSV（包含时间戳）
  3. 计算所有关节的平均值
  4. 保存平均值到单独的 CSV
  5. 在终端显示统计结果
  6. 询问是否将两通道平均值写入零点标定文件 torque_sensor_zero.yaml
     （回车/非 y 即跳过；无输入来源时视为 n；路径可用 --zero-yaml 指定）
"""

import os
import csv
import mmap
import re
import struct
import datetime
import time
import argparse
import sys

"""
    根据嵌软的pdo结构来选择对应的数量
    SLAVE_MAX = 17
    PDO_SLAVE_SIZE = 256
"""

# ---- 内存布局常量 ----
SHM_PATH = "/dev/shm/pdo_shared_mem"
SLAVE_MAX = 22
PDO_SLAVE_SIZE = 512
SHM_SIZE = SLAVE_MAX * PDO_SLAVE_SIZE          # 4352
TX_OFFSET_IN_SLAVE = 72                         # rx_data[72] 之后
ANALOG_OUTPUT1_OFFSET_IN_TX = 16                # analog_output1 在 tx 内偏移
ANALOG_OUTPUT2_OFFSET_IN_TX = 18                # analog_output2 在 tx 内偏移
JOINTS_PER_ARM = 7

# analog_output 的参考中点值，用于计算与其的差值(插值)
REFERENCE_VALUE = 2500

# 每条臂第 1 个关节的从站索引
ARM_BASE_INDEX = {"left": 1, "right": 8}

# 默认输出路径
DEFAULT_CSV_RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "analog_output_raw.csv")
DEFAULT_CSV_AVG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "analog_output_avg.csv")
DEFAULT_ZERO_YAML = "/etc/servo/config/torque_sensor_zero.yaml"


def joint_abs_offset(arm, joint, analog_num):
    """返回某臂第 joint(1..7) 个关节 analog_output 的共享内存绝对字节偏移。

    Args:
        arm: "left" 或 "right"
        joint: 关节编号 1..7
        analog_num: 1 或 2，对应 analog_output1 或 analog_output2

    Returns:
        int: 共享内存绝对字节偏移
    """
    slave_index = ARM_BASE_INDEX[arm] + (joint - 1)
    base_offset = slave_index * PDO_SLAVE_SIZE + TX_OFFSET_IN_SLAVE

    if analog_num == 1:
        return base_offset + ANALOG_OUTPUT1_OFFSET_IN_TX
    elif analog_num == 2:
        return base_offset + ANALOG_OUTPUT2_OFFSET_IN_TX
    else:
        raise ValueError("analog_num 必须是 1 或 2")


def read_analog_outputs(arm, shm_path=SHM_PATH):
    """读取指定臂 7 个关节的 analog_output1 和 analog_output2。

    Returns:
        tuple: (list[int] analog_output1 值, list[int] analog_output2 值)

    Raises:
        FileNotFoundError: 共享内存文件不存在
        ValueError: 共享内存大小不足
    """
    if not os.path.exists(shm_path):
        raise FileNotFoundError("共享内存不存在: %s (PDO 服务是否在运行?)" % shm_path)

    size = os.path.getsize(shm_path)
    if size < SHM_SIZE:
        raise ValueError("共享内存大小不足: %d 字节, 期望至少 %d 字节" % (size, SHM_SIZE))

    analog1_values = []
    analog2_values = []

    fd = os.open(shm_path, os.O_RDONLY)
    try:
        mm = mmap.mmap(fd, SHM_SIZE, mmap.MAP_SHARED, mmap.PROT_READ)
        try:
            for j in range(1, JOINTS_PER_ARM + 1):
                # 读取 analog_output1
                off1 = joint_abs_offset(arm, j, 1)
                (val1,) = struct.unpack_from("<h", mm, off1)   # int16 小端
                analog1_values.append(val1)

                # 读取 analog_output2
                off2 = joint_abs_offset(arm, j, 2)
                (val2,) = struct.unpack_from("<h", mm, off2)   # int16 小端
                analog2_values.append(val2)
        finally:
            mm.close()
    finally:
        os.close(fd)

    return analog1_values, analog2_values


def collect_data(duration, arm, shm_path=SHM_PATH, interval=0.01):
    """连续采集指定时长的数据。

    Args:
        duration: 采集时长（秒）
        arm: "left", "right" 或 "both"
        shm_path: 共享内存路径
        interval: 采集间隔（秒），默认 0.01 秒 (100Hz)

    Returns:
        list: 每个元素是 (timestamp, left_analog1, left_analog2, right_analog1, right_analog2)
    """
    data = []
    start_time = time.time()
    sample_count = 0

    print("开始采集数据，持续 %d 秒...\n" % duration)

    # 进度条相关
    bar_width = 50
    last_progress = -1

    while time.time() - start_time < duration:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

        try:
            if arm == "both":
                left_a1, left_a2 = read_analog_outputs("left", shm_path)
                right_a1, right_a2 = read_analog_outputs("right", shm_path)
                data.append((ts, left_a1, left_a2, right_a1, right_a2))
            elif arm == "left":
                left_a1, left_a2 = read_analog_outputs("left", shm_path)
                data.append((ts, left_a1, left_a2, None, None))
            else:  # right
                right_a1, right_a2 = read_analog_outputs("right", shm_path)
                data.append((ts, None, None, right_a1, right_a2))

            sample_count += 1

            # 更新进度条
            elapsed = time.time() - start_time
            progress = int((elapsed / duration) * 100)

            if progress != last_progress:
                filled = int((elapsed / duration) * bar_width)
                bar = '█' * filled + '░' * (bar_width - filled)
                sys.stdout.write('\r进度: [%s] %3d%% | 时间: %.1fs/%ds | 样本: %d' %
                               (bar, progress, elapsed, duration, sample_count))
                sys.stdout.flush()
                last_progress = progress

        except Exception as e:
            print("\n采集出错: %s" % e)
            break

        time.sleep(interval)

    # 采集完成，换行
    print("\n\n采集完成！共 %d 个样本" % len(data))
    return data


def save_raw_data(data, csv_path, arm):
    """保存原始数据到 CSV。"""
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)

        # 写表头
        if arm == "both":
            header = (["timestamp"] +
                     ["L%d_analog1" % j for j in range(1, JOINTS_PER_ARM + 1)] +
                     ["L%d_analog2" % j for j in range(1, JOINTS_PER_ARM + 1)] +
                     ["R%d_analog1" % j for j in range(1, JOINTS_PER_ARM + 1)] +
                     ["R%d_analog2" % j for j in range(1, JOINTS_PER_ARM + 1)])
        elif arm == "left":
            header = (["timestamp"] +
                     ["L%d_analog1" % j for j in range(1, JOINTS_PER_ARM + 1)] +
                     ["L%d_analog2" % j for j in range(1, JOINTS_PER_ARM + 1)])
        else:  # right
            header = (["timestamp"] +
                     ["R%d_analog1" % j for j in range(1, JOINTS_PER_ARM + 1)] +
                     ["R%d_analog2" % j for j in range(1, JOINTS_PER_ARM + 1)])

        w.writerow(header)

        # 写数据
        for row in data:
            ts, left_a1, left_a2, right_a1, right_a2 = row
            if arm == "both":
                w.writerow([ts] + left_a1 + left_a2 + right_a1 + right_a2)
            elif arm == "left":
                w.writerow([ts] + left_a1 + left_a2)
            else:  # right
                w.writerow([ts] + right_a1 + right_a2)

    print("原始数据已保存到: %s" % csv_path)


def calculate_averages(data, arm):
    """计算平均值。

    Returns:
        dict: 包含各关节 analog_output1 和 analog_output2 的平均值
    """
    if not data:
        return {}

    n = len(data)

    # 初始化累加器
    if arm == "both":
        left_a1_sum = [0] * JOINTS_PER_ARM
        left_a2_sum = [0] * JOINTS_PER_ARM
        right_a1_sum = [0] * JOINTS_PER_ARM
        right_a2_sum = [0] * JOINTS_PER_ARM

        # 累加
        for ts, left_a1, left_a2, right_a1, right_a2 in data:
            for j in range(JOINTS_PER_ARM):
                left_a1_sum[j] += left_a1[j]
                left_a2_sum[j] += left_a2[j]
                right_a1_sum[j] += right_a1[j]
                right_a2_sum[j] += right_a2[j]

        # 计算平均
        return {
            "left_analog1_avg": [s / n for s in left_a1_sum],
            "left_analog2_avg": [s / n for s in left_a2_sum],
            "right_analog1_avg": [s / n for s in right_a1_sum],
            "right_analog2_avg": [s / n for s in right_a2_sum],
        }

    elif arm == "left":
        left_a1_sum = [0] * JOINTS_PER_ARM
        left_a2_sum = [0] * JOINTS_PER_ARM

        for ts, left_a1, left_a2, _, _ in data:
            for j in range(JOINTS_PER_ARM):
                left_a1_sum[j] += left_a1[j]
                left_a2_sum[j] += left_a2[j]

        return {
            "left_analog1_avg": [s / n for s in left_a1_sum],
            "left_analog2_avg": [s / n for s in left_a2_sum],
        }

    else:  # right
        right_a1_sum = [0] * JOINTS_PER_ARM
        right_a2_sum = [0] * JOINTS_PER_ARM

        for ts, _, _, right_a1, right_a2 in data:
            for j in range(JOINTS_PER_ARM):
                right_a1_sum[j] += right_a1[j]
                right_a2_sum[j] += right_a2[j]

        return {
            "right_analog1_avg": [s / n for s in right_a1_sum],
            "right_analog2_avg": [s / n for s in right_a2_sum],
        }


def save_averages(averages, csv_path, arm):
    """保存平均值到 CSV。

    每个关节输出一个合并值: (analog_output1_avg + analog_output2_avg) / 2
    左臂 7 个 + 右臂 7 个 = 共 14 个值。
    """
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)

        # 写表头
        if arm == "both":
            header = ["joint", "avg"]
        elif arm == "left":
            header = ["joint", "avg"]
        else:  # right
            header = ["joint", "avg"]

        w.writerow(header)

        # 写数据（按关节），每个关节输出 (analog1_avg + analog2_avg) / 2
        if arm in ["both", "left"]:
            for j in range(JOINTS_PER_ARM):
                combined = (averages["left_analog1_avg"][j] +
                            averages["left_analog2_avg"][j]) / 2
                w.writerow(["L%d" % (j + 1), combined])

        if arm in ["both", "right"]:
            for j in range(JOINTS_PER_ARM):
                combined = (averages["right_analog1_avg"][j] +
                            averages["right_analog2_avg"][j]) / 2
                w.writerow(["R%d" % (j + 1), combined])

    print("平均值已保存到: %s" % csv_path)


def _display_arm_averages(prefix, a1_avg, a2_avg, arm_name):
    """打印单条臂各关节的通道原始平均值、与参考值的差值、以及两通道合并平均值。"""
    print("\n%s:" % arm_name)
    print("  关节      analog1_avg     Δ%d       analog2_avg     Δ%d       两通道平均"
          % (REFERENCE_VALUE, REFERENCE_VALUE))
    print("  " + "-" * 72)
    for j in range(JOINTS_PER_ARM):
        a1 = a1_avg[j]
        a2 = a2_avg[j]
        combined = (a1 + a2) / 2
        print("  %s%-4d %13.2f %11.2f %15.2f %11.2f %15.2f"
              % (prefix, j + 1,
                 a1, a1 - REFERENCE_VALUE,
                 a2, a2 - REFERENCE_VALUE,
                 combined))


def display_averages(averages, arm):
    """在终端显示平均值统计。

    每个关节显示:
      - 每个通道的原始平均值 (analog1_avg, analog2_avg)
      - 每个通道与参考值 REFERENCE_VALUE 的差值(插值)
      - 两通道合并平均值: (analog1_avg + analog2_avg) / 2
    """
    print("\n" + "=" * 74)
    print("平均值统计结果  (Δ%d = 通道平均值 - %d，两通道平均 = (a1+a2)/2)"
          % (REFERENCE_VALUE, REFERENCE_VALUE))
    print("=" * 74)

    if arm in ["both", "left"]:
        _display_arm_averages("L", averages["left_analog1_avg"],
                              averages["left_analog2_avg"], "左臂 (Left Arm)")

    if arm in ["both", "right"]:
        _display_arm_averages("R", averages["right_analog1_avg"],
                              averages["right_analog2_avg"], "右臂 (Right Arm)")

    print("=" * 74 + "\n")


def write_torque_sensor_zero(averages, arm, yaml_path):
    """将两通道平均值写入 torque_sensor_zero.yaml。

    逐行原地替换 torque_sensor_zero: 块内的数值，保留行内注释与文件其余内容。
    数值为原始浮点精度（与 analog_output_avg.csv 一致），不做舍入。

    Args:
        averages: calculate_averages() 的返回值
        arm: "left", "right" 或 "both"；单臂模式只替换对应 7 项，
             另一臂保持文件原值
        yaml_path: 目标 YAML 文件路径

    Raises:
        ValueError: 块内条目数不是 14（拒绝写入，文件保持原样）
    """
    # 本次要写入的 14 个值，未涉及的臂保持 None（不替换）
    values = [None] * 2 * JOINTS_PER_ARM
    if arm in ["both", "left"]:
        for j in range(JOINTS_PER_ARM):
            values[j] = (averages["left_analog1_avg"][j] +
                         averages["left_analog2_avg"][j]) / 2
    if arm in ["both", "right"]:
        for j in range(JOINTS_PER_ARM):
            values[JOINTS_PER_ARM + j] = (averages["right_analog1_avg"][j] +
                                          averages["right_analog2_avg"][j]) / 2

    entry_re = re.compile(r"^(\s*-\s*)\S+(\s*#.*)?$")

    with open(yaml_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    in_block = False
    count = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("torque_sensor_zero:"):
            in_block = True
            new_lines.append(line)
            continue
        if in_block and stripped:
            m = entry_re.match(line.rstrip("\n"))
            if m:
                count += 1
                if count > 14:
                    raise ValueError("torque_sensor_zero 块内条目数超过 14，拒绝写入")
                if values[count - 1] is not None:
                    new_lines.append("%s%s%s\n" % (m.group(1), values[count - 1],
                                                   m.group(2) or ""))
                else:
                    new_lines.append(line)
                continue
            in_block = False  # 遇到非条目行，块结束
        new_lines.append(line)

    if count != 14:
        raise ValueError("torque_sensor_zero 块内条目数为 %d，期望 14，拒绝写入" % count)

    with open(yaml_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def prompt_and_write_zero(averages, arm, yaml_path):
    """询问是否将两通道平均值写入零点标定文件 torque_sensor_zero.yaml。

    Returns:
        int: 0 = 跳过写入或写入成功；1 = 出错（文件不存在/格式异常/权限不足）
    """
    if not os.path.isfile(yaml_path):
        print("错误: torque_sensor_zero.yaml 不存在: %s" % yaml_path)
        return 1

    print("可将以上 \"两通道平均\" 值写入零点标定文件: %s" % yaml_path)
    if arm == "left":
        print("  (仅更新 left 7 项，right 保持原值)")
    elif arm == "right":
        print("  (仅更新 right 7 项，left 保持原值)")

    try:
        answer = input("是否写入? [Y/N] ").strip().lower()
    except EOFError:
        answer = "n"  # 无输入来源（非交互）时按默认 n 处理
    if answer not in ("y", "yes"):
        print("已跳过写入")
        return 0

    try:
        write_torque_sensor_zero(averages, arm, yaml_path)
    except PermissionError:
        print("写入失败：需要 root 权限，请用 sudo 运行")
        return 1
    except ValueError as e:
        print("写入失败：%s" % e)
        return 1

    print("✓ 零点标定值已写入: %s" % yaml_path)
    return 0


# ---------------------------- 命令行 ----------------------------
def main():
    ap = argparse.ArgumentParser(
        description="采集机械臂关节 analog_output1/2 数据并统计平均值",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    ap.add_argument("--duration", type=int, default=10,
                    help="采集时长（秒），默认 10 秒")
    ap.add_argument("--arm", choices=["left", "right", "both"], default="both",
                    help="采集哪条臂: left / right / both (默认 both)")
    ap.add_argument("--interval", type=float, default=0.01,
                    help="采集间隔（秒），默认 0.01 秒 (100Hz)")
    ap.add_argument("--csv-raw", default=DEFAULT_CSV_RAW,
                    help="原始数据 CSV 输出路径")
    ap.add_argument("--csv-avg", default=DEFAULT_CSV_AVG,
                    help="平均值 CSV 输出路径")
    ap.add_argument("--shm", default=SHM_PATH,
                    help="共享内存路径 (调试/mock 用)")
    ap.add_argument("--zero-yaml", default=DEFAULT_ZERO_YAML,
                    help="零点标定 YAML 路径 (默认 %s)" % DEFAULT_ZERO_YAML)
    args = ap.parse_args()

    try:
        # 1. 采集数据
        data = collect_data(args.duration, args.arm, args.shm, args.interval)

        if not data:
            print("未采集到数据，退出")
            return 1

        # 2. 保存原始数据
        save_raw_data(data, args.csv_raw, args.arm)

        # 3. 计算平均值
        averages = calculate_averages(data, args.arm)

        # 4. 保存平均值
        save_averages(averages, args.csv_avg, args.arm)

        # 5. 在终端显示
        display_averages(averages, args.arm)

        # 6. 询问是否写入零点标定文件
        rc = prompt_and_write_zero(averages, args.arm, args.zero_yaml)
        if rc != 0:
            return rc

        print("✓ 所有操作完成")
        return 0

    except Exception as e:
        print("错误: %s" % e)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
