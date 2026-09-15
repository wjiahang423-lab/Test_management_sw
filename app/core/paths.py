import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # 打包运行：只读资源在 bundle 目录（PyInstaller onedir 为 _internal），
    # 可写数据目录放在可执行文件同级（保证升级/重打包不丢数据）
    BUNDLE_DIR = os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # 源码运行
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    BUNDLE_DIR = BASE_DIR

UI_DIR = os.path.join(BUNDLE_DIR, "UI")
SCRIPTS_DIR = os.path.join(BUNDLE_DIR, "scripts")
ASSETS_DIR = os.path.join(BUNDLE_DIR, "assets")

LOGO_FILE = os.path.join(ASSETS_DIR, "logo_zioneer.png")
ICON_FILE = os.path.join(ASSETS_DIR, "icon_eol.png")
ICON_ICO = os.path.join(ASSETS_DIR, "icon_eol.ico")
LOGIN_BG_FILE = os.path.join(ASSETS_DIR, "zioneer_bg.png")

DATA_DIR = os.path.join(BASE_DIR, "data")
PLANS_DIR = os.path.join(DATA_DIR, "plans")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

# 外部扩展包目录：测试脚本可在此放置任意第三方包（无需重新打包）。
# 用法：pip install --target data/ext_packages <包名>
EXT_PACKAGES_DIR = os.path.join(DATA_DIR, "ext_packages")

USERS_FILE = os.path.join(DATA_DIR, "users.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
VARIABLES_FILE = os.path.join(DATA_DIR, "variables.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")
STATS_INI_FILE = os.path.join(DATA_DIR, "stats.ini")

PLAN_EXT = "plan"
PLAN_FILE_FILTER = "测试计划文件 (*.plan);;JSON 文件 (*.json);;所有文件 (*)"


def relpath_from_root(path):
    """把项目根目录(BASE_DIR)下的绝对路径转为相对根目录的路径，否则原样返回。

    用于保存测试计划时把脚本/YAML 路径归一化为相对路径，保证计划可移植。
    """
    if not path:
        return path
    path = os.path.abspath(os.path.expanduser(str(path)))
    try:
        rel = os.path.relpath(path, BASE_DIR)
    except ValueError:
        return path
    if rel == "." or rel.startswith("..") or os.path.isabs(rel):
        return path
    return rel


def resolve_root_path(path):
    """把计划中存储的路径解析为绝对路径（自动识别根目录，保证移植后可找到）。

    规则：
    1. 相对路径 -> 优先相对 BASE_DIR（源码根目录/程序目录），其次相对打包资源目录。
       不依赖当前工作目录（cwd），因此程序从任意位置（如桌面）启动也能找到脚本。
    2. 绝对路径存在 -> 直接用
    3. 绝对路径不存在 -> 按文件名在根目录 scripts/ 下回退查找
    """
    if not path:
        return path
    raw = str(path)
    userexp = os.path.expanduser(raw)
    if not os.path.isabs(userexp):
        for root in (BASE_DIR, BUNDLE_DIR):
            candidate = os.path.join(root, userexp)
            if os.path.exists(candidate):
                return candidate
        return os.path.join(BASE_DIR, userexp)
    expanded = os.path.abspath(userexp)
    if os.path.exists(expanded):
        return expanded
    base = os.path.basename(expanded)
    for cand in (os.path.join(BASE_DIR, "scripts", base),
                 os.path.join(BASE_DIR, base),
                 os.path.join(BUNDLE_DIR, "scripts", base)):
        if os.path.exists(cand):
            return cand
    return expanded

UI_FILES = {
    "login": os.path.join(UI_DIR, "login.ui - 登录页.ui"),
    "execute": os.path.join(UI_DIR, "execute_page.ui - 执行页面.ui"),
    "manage": os.path.join(UI_DIR, "manage_page.ui"),
    "action": os.path.join(UI_DIR, "case_action.ui - Action用例独立表单.ui"),
    "delay": os.path.join(UI_DIR, "case_delay.ui - Delay用例独立表单.ui"),
    "pop": os.path.join(UI_DIR, "case_pop.ui - Pop用例独立表单.ui"),
    "measurement": os.path.join(UI_DIR, "case_measurement.ui - Measurement用例独立表单.ui"),
    "loop": os.path.join(UI_DIR, "case_loop.ui - Loop用例独立表单.ui"),
}

CASE_TYPES = ("action", "delay", "pop", "measurement", "loop")

CASE_TYPE_NAMES = {
    "action": "Action（仅执行）",
    "delay": "Delay（延时）",
    "pop": "Pop（人机交互）",
    "measurement": "Measurement（测量）",
    "loop": "Loop（循环批量）",
}

CASE_STATE_PENDING = "待执行"
CASE_STATE_RUNNING = "运行中"
CASE_STATE_PASS = "PASS"
CASE_STATE_FAIL = "FAIL"
CASE_STATE_SKIP = "跳过"
CASE_STATE_TIMEOUT = "超时"

DEFAULT_CLEAR_PASSWORD = "0000"
