"""Generate a layered-architecture diagram (JPG) for the test management software."""
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "系统架构图.jpg")

W, H = 1500, 1150

# palette (matching app theme)
BLUE = (31, 90, 168)          # #1F5AA8
DARK_BLUE = (28, 63, 115)     # header bg
LIGHT_BLUE = (227, 236, 247)  # chip bg
BODY_BG = (247, 249, 252)
LINE = (206, 214, 224)
GRAY_TEXT = (80, 90, 105)
WHITE = (255, 255, 255)
ORANGE = (242, 153, 74)

FONT_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


def rounded_box(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill,
                           outline=outline, width=width)


def text_center(draw, cx, cy, s, fnt, fill):
    bbox = draw.textbbox((0, 0), s, font=fnt)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), s, font=fnt, fill=fill)


def draw_arrow_down(draw, x, y1, y2):
    draw.line([(x, y1), (x, y2)], fill=BLUE, width=3)
    draw.polygon([(x - 9, y2 - 14), (x + 9, y2 - 14), (x, y2)], fill=BLUE)


def main():
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    # ---------- title ----------
    text_center(d, W / 2, 42, "测试用例管理与执行软件 —— 系统分层架构图",
                font(34, True), DARK_BLUE)
    text_center(d, W / 2, 78, "开发技术：Python + PyQt5 ｜ 架构原则：UI(.ui) 与业务逻辑彻底分离 ｜ 异步：QThread + 线程池",
                font(18), GRAY_TEXT)

    # ---------- layers ----------
    layer_x0, layer_x1 = 60, 1020   # left panel
    panel_x0, panel_x1 = 1060, 1440 # right cross-cutting panel

    def layer(y0, y1, title, chips):
        rounded_box(d, (layer_x0, y0, layer_x1, y1), 12, BODY_BG, LINE, 2)
        # header band
        rounded_box(d, (layer_x0, y0, layer_x1, y0 + 40), 12, DARK_BLUE)
        d.rectangle((layer_x0, y0 + 22, layer_x1, y0 + 40), fill=DARK_BLUE)
        text_center(d, (layer_x0 + layer_x1) / 2, y0 + 20, title, font(22, True), WHITE)
        # chips
        cy = y0 + 82
        cx = layer_x0 + 20
        for chip in chips:
            tw = d.textlength(chip, font=font(18))
            wch = tw + 40
            if cx + wch > layer_x1 - 20:
                cx = layer_x0 + 20
                cy += 52
            rounded_box(d, (cx, cy - 19, cx + wch, cy + 19), 10, LIGHT_BLUE, BLUE, 1)
            text_center(d, cx + wch / 2, cy, chip, font(18), DARK_BLUE)
            cx += wch + 14
        return (y0 + y1) / 2

    mid1 = layer(110, 210, "用户入口 · 权限层", ["main.py 程序入口", "一键分权(require_login)", "登录窗口(账号/角色校验)", "管理员 / 操作员"])
    draw_arrow_down(d, (layer_x0 + layer_x1) // 2, 212, 264)

    mid2 = layer(264, 364, "界面表现层（UI）", ["login.ui", "execute_page.ui", "manage_page.ui", "case_action/delay/pop/measurement/loop.ui", "QtUiLoader 加载"])
    draw_arrow_down(d, (layer_x0 + layer_x1) // 2, 366, 418)

    mid3 = layer(418, 546, "界面控制器层（app/ui）", ["MainWindow 主窗口", "ExecutePage 执行页", "ManagePage 管理页", "VariablesPage 变量页", "SettingsPage 设置页", "UsersPage 用户页", "CaseEditorDialog 用例弹窗", "PlanSettings 计划设置", "PopDialog 交互弹窗", "DebugRunner 调试", "LogBridge 日志桥", "HelpDialog 帮助"])
    draw_arrow_down(d, (layer_x0 + layer_x1) // 2, 548, 600)

    mid4 = layer(600, 776, "核心业务层（app/core）", ["EngineWorker 执行引擎(QThread)", "线程池 + 超时/暂停/停止", "RuntimeContext 运行时上下文", "内置 API (set_sn_to_Panel 等)", "ScriptLoader 脚本加载/签名解析", "TestPlan 数据模型(PLAN→序列→用例)", "5类用例: Action/Delay/Pop/Measurement/Loop", "VariableManager 全局变量", "UserManager 用户权限", "SettingsManager 设置", "Report 报告生成(HTML/远程)"])
    draw_arrow_down(d, (layer_x0 + layer_x1) // 2, 778, 830)

    mid5 = layer(830, 950, "数据与资源层", ["data/plans/*.plan 测试计划(含变量)", "data/users.json 账号", "data/settings.json 设置", "data/stats.json 统计", "data/logs 日志", "data/reports HTML报告", "scripts/*.py 测试脚本", "scripts/*.yaml Loop数据", "assets 品牌资源", ".ui 设计文件"])

    # ---------- right cross-cutting panel ----------
    rounded_box(d, (panel_x0, 110, panel_x1, 950), 12, BODY_BG, LINE, 2)
    rounded_box(d, (panel_x0, 110, panel_x1, 150), 12, ORANGE)
    d.rectangle((panel_x0, 132, panel_x1, 150), fill=ORANGE)
    text_center(d, (panel_x0 + panel_x1) / 2, 130, "横切机制（Cross-cutting）", font(20, True), WHITE)

    items = [
        ("信号槽通信", "引擎→UI 全部通过 pyqtSignal，严禁工作线程操作控件"),
        ("线程池", "外部函数在线程池执行，界面永不阻塞"),
        ("QThread 异步", "执行引擎独立线程，暂停/停止事件即时响应"),
        ("test_api 内置 API", "脚本可直接 set_sn_to_Panel / get_sn / get_test_result ..."),
        ("异常兜底", "脚本/网络/IO 全链路 try-except，异常只记日志不崩溃"),
        ("7×24 稳定", "每次运行独立线程池并 shutdown，脚本按 mtime 缓存失效"),
    ]
    y = 190
    for title, desc in items:
        rounded_box(d, (panel_x0 + 20, y, panel_x1 - 20, y + 112), 8, LIGHT_BLUE, BLUE, 1)
        d.text((panel_x0 + 36, y + 14), "▸ " + title, font=font(19, True), fill=DARK_BLUE)
        _wrap(d, (panel_x0 + 36, y + 46), desc, font(16), GRAY_TEXT,
              panel_x1 - 56)
        y += 130

    # ---------- footer ----------
    text_center(d, W / 2, 1085,
                "技术要点：Qt 主线程负责渲染，测试任务 QThread + 线程池异步执行；信号槽跨线程通信；UI 与业务代码通过 QtUiLoader 解耦",
                font(17), GRAY_TEXT)

    img.save(OUT, quality=92)
    print("saved:", OUT, img.size)


def _wrap(draw, xy, s, fnt, fill, max_x):
    x, y = xy
    line = ""
    for ch in s:
        test = line + ch
        if draw.textlength(test, font=fnt) > (max_x - x):
            draw.text((x, y), line, font=fnt, fill=fill)
            y += 22
            line = ch
        else:
            line = test
    if line:
        draw.text((x, y), line, font=fnt, fill=fill)


if __name__ == "__main__":
    main()
