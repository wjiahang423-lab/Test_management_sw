# -*- coding: utf-8 -*-
"""RViz2 验证视图弹窗脚本。

用于标定完成后自动弹出 RViz2 视图，供操作员二次确认雷达/相机轮廓是否重合。

用法（只需改顶部 CONFIG 配置即可）：
  1. 编辑本文件顶部 CONFIG：ROS 路径、rviz 配置文件路径、ROS_DOMAIN_ID 等
  2. 在测试计划里新增一个 Action 用例：
        脚本 -> 本文件
        函数 -> open_rviz_view
      放在「标定」用例之后，标定完成后即自动弹出视图
  3. 可选：再新增一个 Action 用例，函数 close_rviz_view，用于关闭视图

主要函数：
  - open_rviz_view          启动 RViz2 验证视图
  - close_rviz_view         关闭视图
  - wait_rviz_close         阻塞等待关窗（可配 timeout_s / auto_close 自动关闭）
  - capture_rviz_screenshot 对 RViz2 窗口截图保存 PNG（供报告）
  - save_calib_result       保存标定结果 JSON（外参矩阵+残差+时间戳）
  - verify_and_save         Step4 一键流程：查看→截图→保存→自动关闭

依赖：
  - 上位机需安装 rviz2（如 sudo apt install ros-humble-rviz2）
  - RK3588 与上位机在同一 ROS 网络：ROS_DOMAIN_ID 一致、网络互通
  - 截图依赖 xwd + wmctrl（x11-apps / wmctrl）

                                                                                                                                                                                                      
"""
import datetime
import json
import os
import struct
import subprocess
import time
import zlib

import test_api

# ===================== 用户配置区（按需修改） =====================
CONFIG = {
    # ROS 2 发行版与安装路径（rviz2 所在环境）
    "ros_distro": "humble",
    "ros_setup": "/opt/ros/humble/setup.bash",   # 留空则按 ros_distro 自动拼接
    "ros_domain_id": "0",                          # 与 RK3588 保持一致

    # rviz2 可执行文件，留空自动查找 /opt/ros/<distro>/bin/rviz2
    "rviz_bin": "/opt/ros/<distro>/bin/rviz2",

    # RViz2 配置文件（.rviz 模板）。支持：
    #   绝对路径；或相对本脚本目录的文件名；或相对软件根目录的文件名
    "rviz_config": "calib_rviz.rviz",

    # 图形显示（DISPLAY），留空使用当前环境默认
    "display": "",

    # 启动前先关闭已打开的 rviz2（避免多开）
    "kill_before_start": True,

    # 启动后等待 rviz2 就绪的时间（秒）
    "startup_wait": 2.0,

    # 标定结果保存目录（留空默认 <软件根目录>/data/calib）
    "calib_data_dir": "",

    # 截图保存目录（留空默认 <软件根目录>/data/reports）
    "report_img_dir": "",

    # 标定算法原始结果文件（RK3588/标定算法落盘的 JSON）。
    # 缺省时按 params << 原始文件 << 全局变量 的优先级取标定参数。
    # 留空则不读取原始文件，参数仅从 params/变量来。
    "calib_input_file": "",
}
# ================================================================

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
_ROOT_DIR = os.path.dirname(_PROJECT_DIR)


def _ros_setup():
    setup = CONFIG.get("ros_setup", "").strip()
    if setup and os.path.exists(setup):
        return setup
    distro = CONFIG.get("ros_distro", "humble")
    candidate = os.path.join("/opt/ros", distro, "setup.bash")
    return candidate if os.path.exists(candidate) else ""


def _rviz_bin():
    rviz = CONFIG.get("rviz_bin", "").strip()
    if rviz and os.path.exists(rviz):
        return rviz
    distro = CONFIG.get("ros_distro", "humble")
    candidate = os.path.join("/opt/ros", distro, "bin", "rviz2")
    return candidate if os.path.exists(candidate) else "rviz2"


def _resolve_config(cfg):
    """把配置里的 .rviz 路径解析为真实存在的绝对路径，找不到则原样返回。"""
    if not cfg:
        return ""
    if os.path.isabs(cfg):
        return cfg if os.path.exists(cfg) else cfg
    for candidate in (
        os.path.join(_SCRIPT_DIR, cfg),
        os.path.join(_PROJECT_DIR, cfg),
        os.path.join(_PROJECT_DIR, "data", cfg),
    ):
        if os.path.exists(candidate):
            return candidate
    return cfg


def _shq(text):
    """给 shell 参数加单引号，防止路径含空格出错。"""
    return "'" + str(text).replace("'", "'\\''") + "'"


def _ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _data_dir(cfg_key, sub):
    """解析数据保存目录：CONFIG 指定优先，否则回退到 <软件根目录>/data/<sub>。"""
    cfg = CONFIG.get(cfg_key, "").strip()
    if cfg:
        _ensure_dir(cfg)
        return cfg
    d = os.path.join(_ROOT_DIR, "data", sub)
    _ensure_dir(d)
    return d


def _timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _load_calib_input(params=None):
    """读取标定算法原始结果 JSON，返回 dict（空 dict 表示没有）。

    路径优先级：params['calib_input_file'] > CONFIG['calib_input_file']。
    文件不存在或解析失败返回 {}。
    """
    params = params or {}
    path = (params.get("calib_input_file") or
            CONFIG.get("calib_input_file", "") or "").strip()
    if not path:
        return {}
    if os.path.isabs(path) and os.path.exists(path):
        pass
    else:
        for base in (_SCRIPT_DIR, _ROOT_DIR):
            cand = os.path.join(base, path)
            if os.path.exists(cand):
                path = cand
                break
    if not os.path.exists(path):
        test_api.log("标定输入文件不存在：{}".format(path))
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        test_api.log("读取标定输入文件失败：{}（{}）".format(path, e))
        return {}


def _resolve_calib_params(params=None):
    """按 params << 原始文件 << 软件全局变量 的优先级合并标定参数。

    返回含 keys：sn / passed / rmse / threshold / extrinsic / note。
    """
    params = params or {}
    merged = dict(_load_calib_input(params))
    merged.update(params)

    # 从软件全局变量补齐（变量优先级最低，仅当上面没有对应键时）
    for key in ("sn", "passed", "rmse", "threshold", "extrinsic", "note"):
        if key in merged:
            continue
        try:
            val = test_api.get_variable("calib." + key)
            if val is not None:
                merged[key] = val
        except Exception:
            pass
    return merged


def _run_capture(cmd, env=None, timeout=10):
    try:
        return subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout)
    except Exception:
        return None


def _find_rviz_window(display=""):
    """用 wmctrl 找到 rviz2 窗口，返回 (win_id, x, y, w, h, title) 或 None。"""
    env = os.environ.copy()
    if display:
        env["DISPLAY"] = display
    try:
        out = subprocess.run(["wmctrl", "-lG"], env=env, capture_output=True,
                             text=True, timeout=10)
    except Exception:
        return None
    for line in out.stdout.splitlines():
        if "rviz" not in line.lower():
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            win_id = parts[0]
            x, y, w, h = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
        except ValueError:
            continue
        title = " ".join(parts[7:])
        return win_id, x, y, w, h, title
    return None


def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _write_png(path, width, height, rgb):
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3]
                   for y in range(height))
    idat = zlib.compress(raw, 6)
    with open(path, "wb") as f:
        f.write(sig)
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", idat))
        f.write(_png_chunk(b"IEND", b""))


def _mask_shift(mask):
    return (mask & -mask).bit_length() - 1 if mask else 0


def _xwd_to_png(xwd_path, png_path):
    """把 xwd 截图转成 PNG（纯标准库，无需 PIL/numpy）。"""
    with open(xwd_path, "rb") as f:
        data = f.read()
    be = struct.unpack(">25I", data[:100])
    le = struct.unpack("<25I", data[:100])
    endian = ">" if 60 <= be[0] <= 1000000 else "<"
    v = be if endian == ">" else le
    hs, w, h, bpl = v[0], v[4], v[5], v[12]
    rm, gm, bm = v[14], v[15], v[16]
    bpp = bpl // max(w, 1)
    if bpp not in (2, 3, 4) or w <= 0 or h <= 0:
        return False
    sr, sg, sb = _mask_shift(rm), _mask_shift(gm), _mask_shift(bm)
    if bpp == 4:
        fmt = (">" if endian == ">" else "<") + "I" * w
    elif bpp == 2:
        fmt = (">" if endian == ">" else "<") + "H" * w
    else:
        return False
    rgb = bytearray()
    off = hs
    for y in range(h):
        row = data[off + y * bpl:off + (y + 1) * bpl]
        arr = struct.unpack(fmt, row[:w * bpp])
        for px in arr:
            rgb.append((px & rm) >> sr)
            rgb.append((px & gm) >> sg)
            rgb.append((px & bm) >> sb)
    _write_png(png_path, w, h, bytes(rgb))
    return True


def rviz_is_running():
    """检查是否已有 rviz2 进程在运行。"""
    try:
        proc = subprocess.run(["pgrep", "-x", "rviz2"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def close_rviz_view(params=None):
    """关闭已打开的 RViz2 视图（Action 用例可调用）。"""
    params = params or {}
    if not rviz_is_running():
        test_api.log("RViz2 当前未运行，无需关闭")
        return True
    test_api.log("正在关闭 RViz2 视图...")
    try:
        subprocess.run(["pkill", "-x", "rviz2"], timeout=10)
        time.sleep(0.5)
        test_api.log("RViz2 已关闭")
        return True
    except FileNotFoundError:
        test_api.log("未找到 pkill 命令，无法关闭 rviz2")
        return False
    except Exception as e:
        test_api.log("关闭 RViz2 失败：{}".format(e))
        return False


def open_rviz_view(params=None):
    """启动 RViz2 验证视图（Action 用例主函数）。

    可选通过 params 覆盖 CONFIG（在用例 params 里传）：
        rviz_config / ros_setup / ros_domain_id / display / kill_before_start
    """
    params = params or {}

    cfg = params.get("rviz_config") or CONFIG.get("rviz_config", "")
    display = params.get("display") or CONFIG.get("display", "")
    kill = params.get("kill_before_start", CONFIG.get("kill_before_start", True))

    if kill:
        close_rviz_view()

    setup = _ros_setup()
    rviz = _rviz_bin()
    cfg_path = _resolve_config(cfg)

    if cfg_path and not os.path.isabs(cfg_path) and not os.path.exists(cfg_path):
        test_api.log("警告：找不到 RViz 配置文件：{}".format(cfg))

    env = os.environ.copy()
    env["ROS_DOMAIN_ID"] = str(params.get("ros_domain_id") or CONFIG.get("ros_domain_id", "0"))
    if display:
        env["DISPLAY"] = display

    rviz_cmd = " ".join([rviz] + (["-d", _shq(cfg_path)] if cfg_path else []))
    if setup:
        cmd = "source {} && exec {}".format(_shq(setup), rviz_cmd)
    else:
        cmd = "exec " + rviz_cmd

    test_api.log("启动 RViz2 验证视图...")
    test_api.log("命令：{}".format(cmd))
    try:
        subprocess.Popen(["bash", "-lc", cmd], env=env)
    except FileNotFoundError:
        test_api.log("启动失败：找不到 bash 或 rviz2（{}）".format(rviz))
        return False
    except Exception as e:
        test_api.log("启动 RViz2 失败：{}".format(e))
        return False

    wait = float(params.get("startup_wait", CONFIG.get("startup_wait", 2.0)))
    if wait > 0:
        time.sleep(wait)
    if rviz_is_running():
        test_api.log("RViz2 已启动（配置文件：{}）".format(cfg_path or "默认"))
        return True
    test_api.log("RViz2 已发起启动，但尚未检测到进程（可稍后在桌面上确认）")
    return True


def wait_rviz_close(params=None):
    """阻塞等待用户关闭 RViz2 窗口（用于需要等待确认的场景，可作 Action 用例）。

    参数：
        timeout_s  等待超时（秒），0 表示一直等
        auto_close 超时后是否自动关闭 rviz2（True 则超时后调用 close_rviz_view）
    """
    params = params or {}
    timeout = float(params.get("timeout_s", 0) or 0)
    auto_close = bool(params.get("auto_close", False))
    test_api.log("等待操作员关闭 RViz2 视图...")
    start = time.time()
    while rviz_is_running():
        if timeout and (time.time() - start) > timeout:
            if auto_close:
                test_api.log("等待超时（{}s），自动关闭 RViz2 视图".format(timeout))
                close_rviz_view()
                return True
            test_api.log("等待超时（{}s），继续执行".format(timeout))
            return False
        time.sleep(0.5)
    test_api.log("RViz2 视图已关闭")
    return True


def capture_rviz_screenshot(params=None):
    """对当前 RViz2 窗口截图并保存为 PNG（供报告使用，可作 Action 用例）。

    参数：
        save_dir    保存目录，留空使用 CONFIG.report_img_dir（默认 data/reports）
        filename    文件名，留空自动用时间戳
        display     图形显示，留空使用当前环境
    返回：截图文件的绝对路径（str），失败返回 ""。
    """
    params = params or {}
    display = params.get("display") or CONFIG.get("display", "")
    save_dir = params.get("save_dir") or _data_dir("report_img_dir", "reports")
    filename = params.get("filename") or "calib_rviz_{}.png".format(_timestamp())

    if not rviz_is_running():
        test_api.log("RViz2 未运行，跳过截图")
        return ""

    env = os.environ.copy()
    if display:
        env["DISPLAY"] = display

    win = _find_rviz_window(display)
    tmp_xwd = os.path.join(save_dir, ".tmp_calib_rviz.xwd")
    png_path = os.path.join(save_dir, filename)

    if win:
        win_id = win[0]
        proc = _run_capture(["xwd", "-id", win_id, "-silent", "-out", tmp_xwd], env)
        if proc is None or proc.returncode != 0 or not os.path.exists(tmp_xwd):
            win = None

    if win is None:
        # 回退：整屏截图
        test_api.log("未定位到 RViz2 窗口，尝试整屏截图")
        proc = _run_capture(["xwd", "-root", "-silent", "-out", tmp_xwd], env)
        if proc is None or proc.returncode != 0 or not os.path.exists(tmp_xwd):
            test_api.log("截图失败：xwd 不可用或未输出文件")
            return ""

    if not _xwd_to_png(tmp_xwd, png_path):
        test_api.log("截图失败：xwd 转 PNG 失败")
        try:
            os.remove(tmp_xwd)
        except Exception:
            pass
        return ""

    try:
        os.remove(tmp_xwd)
    except Exception:
        pass
    test_api.log("已保存截图：{}".format(png_path))
    return png_path


def save_calib_result(params=None):
    """保存标定结果数据（外参矩阵 + 残差 + 时间戳）为 JSON，可作 Action 用例。

    参数（均可选）：
        sn          序列号
        passed      是否通过（bool）
        rmse        残差
        threshold   判定阈值
        extrinsic   外参矩阵 dict（x/y/z/roll/pitch/yaw 或自定义）
        note        备注
        save_dir    保存目录，留空使用 CONFIG.calib_data_dir（默认 data/calib）
        filename    文件名，留空自动用时间戳
    返回：保存的 JSON 文件绝对路径（str）。
    """
    params = params or {}
    save_dir = params.get("save_dir") or _data_dir("calib_data_dir", "calib")
    filename = params.get("filename") or "calib_result_{}.json".format(_timestamp())

    resolved = _resolve_calib_params(params)
    sn = resolved.get("sn") or test_api.get_sn() or ""
    extrinsic = resolved.get("extrinsic") or {}
    if not isinstance(extrinsic, dict):
        extrinsic = {"value": extrinsic}

    data = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sn": sn,
        "passed": bool(resolved.get("passed", False)),
        "rmse": resolved.get("rmse"),
        "threshold": resolved.get("threshold"),
        "extrinsic": extrinsic,
        "note": resolved.get("note", ""),
    }

    path = os.path.join(save_dir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        test_api.log("已保存标定结果：{}".format(path))
        return path
    except Exception as e:
        test_api.log("保存标定结果失败：{}".format(e))
        return ""


def collect_calib_result(params=None):
    """汇总当前标定结果（供 Action 用例返回，自动进报告/逐用例上报）。

    返回 dict：
        sn / passed / rmse / threshold / note / extrinsic / result(文件路径)

    参数同上 save_calib_result（save_dir/filename 控制保存路径；
    calib_input_file 指定标定算法原始 JSON）。返回前自动保存一份 JSON。
    """
    params = params or {}

    resolved = _resolve_calib_params(params)
    sn = resolved.get("sn") or test_api.get_sn() or ""
    extrinsic = resolved.get("extrinsic") or {}
    if not isinstance(extrinsic, dict):
        extrinsic = {"value": extrinsic}

    save_params = dict(params)
    save_params.setdefault("sn", sn)
    save_params.setdefault("passed", resolved.get("passed", False))
    save_params.setdefault("rmse", resolved.get("rmse"))
    save_params.setdefault("threshold", resolved.get("threshold"))
    save_params.setdefault("extrinsic", extrinsic)
    save_params.setdefault("note", resolved.get("note", ""))
    result_path = save_calib_result(save_params)

    return {
        "sn": sn,
        "passed": bool(resolved.get("passed", False)),
        "rmse": resolved.get("rmse"),
        "threshold": resolved.get("threshold"),
        "extrinsic": extrinsic,
        "note": resolved.get("note", ""),
        "result": result_path,
    }


def verify_and_save(params=None):
    """Step4「结果自检与保存」一键流程（可作单个 Action 用例）。

    流程：等待操作员查看 → 截图存档 → 保存标定数据 → 自动关闭 RViz2。

    参数：
        wait_s        等待查看时间（秒，默认 10；0 表示等待操作员手动关闭）
        screenshot    是否截图（默认 True）
        auto_close    等待结束后是否自动关闭（默认 True）
        其余参数透传给 save_calib_result：sn / passed / rmse / threshold / extrinsic / note
    返回：dict，含 screenshot 与 result 两个保存路径。
    """
    params = params or {}
    wait_s = float(params.get("wait_s", 10))
    do_shot = bool(params.get("screenshot", True))
    auto_close = bool(params.get("auto_close", True))

    if wait_s > 0:
        time.sleep(wait_s)
    else:
        # 等待操作员手动关闭
        if rviz_is_running():
            wait_rviz_close({"timeout_s": 0})

    out = {"screenshot": "", "result": ""}
    if do_shot:
        out["screenshot"] = capture_rviz_screenshot(params)
    else:
        out["screenshot"] = ""

    result = collect_calib_result(params)
    out.update(result)

    if auto_close:
        close_rviz_view()
    return out


# if __name__ == "__main__":
#     # 直接运行本脚本时，启动 RViz2 视图
#     open_rviz_view()