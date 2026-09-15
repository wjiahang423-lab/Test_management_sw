# -*- coding: utf-8 -*-
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import os

OUTPUT_DIR = "/home/jyzn/Test_management_sw/docs"

def sf(run):
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(10.5)

def ah(doc, text, lvl=1):
    h = doc.add_heading(text, level=lvl)
    for r in h.runs:
        r.font.name = "Microsoft YaHei"
        r._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        r.font.color.rgb = RGBColor(0x1F, 0x5A, 0xA0)
    return h

def ap(doc, text, bp=None):
    p = doc.add_paragraph()
    if bp:
        r = p.add_run(bp); r.font.bold=True; sf(r); p.add_run(text)
    else:
        r = p.add_run(text); sf(r)
    return p

def at(doc, headers, rows, widths=None, hc="1F5AA0"):
    t = doc.add_table(rows=1+len(rows), cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.style = "Table Grid"
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]; c.text = h
        tc = c._tc; tp = tc.get_or_add_tcPr()
        sh = OxmlElement("w:shd")
        sh.set(qn("w:val"), "clear"); sh.set(qn("w:color"), "auto"); sh.set(qn("w:fill"), hc)
        tp.append(sh)
        for pp in c.paragraphs:
            pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in pp.runs:
                r.font.bold = True; r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                sf(r); r.font.size = Pt(9)
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            if ci < len(t.rows[ri+1].cells):
                c = t.rows[ri+1].cells[ci]; c.text = str(val)
                if ri % 2 == 1:
                    tc = c._tc; tp = tc.get_or_add_tcPr()
                    sh = OxmlElement("w:shd")
                    sh.set(qn("w:val"), "clear"); sh.set(qn("w:color"), "auto"); sh.set(qn("w:fill"), "F0F4FA")
                    tp.append(sh)
                for pp in c.paragraphs:
                    pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for r in pp.runs:
                        sf(r); r.font.size = Pt(9)
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                if i < len(row.cells):
                    row.cells[i].width = Cm(w)
    return t

def ac(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.5)
    r = p.add_run(text); r.font.name = "Consolas"; r.font.size = Pt(8.5)
    return p

doc = Document()
s = doc.styles["Normal"]; s.font.name = "Microsoft YaHei"; s.font.size = Pt(10.5)
s.element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

for _ in range(5): doc.add_paragraph()
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("测试用例管理与执行系统"); r.font.size = Pt(28); r.font.bold = True
r.font.color.rgb = RGBColor(0x1F, 0x5A, 0xA0); sf(r)
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("详细设计说明书"); r.font.size = Pt(36); r.font.bold = True
r.font.color.rgb = RGBColor(0x1F, 0x5A, 0xA0); sf(r)
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("\n\n版本：V1.0\n日期：2026-09-11"); r.font.size = Pt(14); sf(r)
doc.add_page_break()

ah(doc, "1. 设计概述", 1)
ap(doc, "本文档详细描述测试用例管理与执行系统的内部设计，包括数据结构、算法逻辑、接口规范和模块边界。")
at(doc, ["文档信息", "内容"], [
    ["版本号", "V1.0"], ["更新日期", "2026-09-11"],
    ["代码总量", "约6981行Python + 11个.ui文件"],
], widths=[3, 13])

ah(doc, "2. 数据结构设计", 1)
ah(doc, "2.1 TestCase 用例数据模型", 2)
at(doc, ["字段名", "类型", "默认值", "说明"], [
    ["id", "str", "uuid4前12位", "全局唯一标识符"],
    ["name", "str", "''''", "用例名称"],
    ["type", "str", '"action"', "用例类型：action/delay/pop/measurement/loop"],
    ["timeout_ms", "int", "5000", "超时阈值（毫秒）"],
    ["retry", "int", "0", "失败重试次数"],
    ["fail_policy", "str", '"continue"', "失败策略：continue/pause"],
    ["skip", "bool", "False", "是否跳过此用例"],
    ["config", "dict", "{}", "类型相关配置参数"],
], widths=[3, 2, 2, 7])

ah(doc, "2.2 TestPlan 计划数据模型", 2)
at(doc, ["字段名", "类型", "说明"], [
    ["name", "str", "计划名称"],
    ["sequences", "List[TestSequence]", "测试序列列表（有序）"],
    ["variables", "dict", "计划级变量"],
    ["settings", "dict", "计划设置（storage_mode/continuous/json_upload_url等）"],
    ["file_path", "str|None", ".plan文件绝对路径"],
], widths=[3, 3, 8])

ah(doc, "2.3 RuntimeContext 运行时上下文", 2)
at(doc, ["属性", "类型", "说明"], [
    ["current_sn", "str", "当前产品SN码"],
    ["current_result", "bool|None", "当前用例判定结果"],
    ["robot_status", "dict", "{temperature, current, voltage, battery}"],
    ["measure_result", "dict|None", "Loop会话自描述测量结果"],
    ["upload_key", "str", "逐用例上报密钥"],
    ["station_id/name", "str", "工位ID和名称"],
    ["log_callback", "callable|None", "日志回调函数（GUI线程）"],
], widths=[3.5, 3, 7.5])

ah(doc, "3. 模块详细设计", 1)
ah(doc, "3.1 EngineWorker 测试引擎（engine.py，1185行）", 2)
at(doc, ["方法", "行号范围", "功能说明"], [
    ["run()", "L90-170", "主执行循环：初始化logger->等待SN->执行计划->生成报告->清理"],
    ["_run_plan()", "L202-245", "连续模式外层循环，遍历sequences x cases"],
    ["_execute_case()", "L306-398", "单用例执行：重试循环->dispatch->超时控制->结果记录"],
    ["_handle_action()", "L426-449", "Action：加载脚本->线程池执行->返回payload"],
    ["_handle_delay()", "L455-471", "Delay：带stop/pause检查的sleep循环"],
    ["_handle_pop()", "L473-488", "Pop：信号到GUI->Event等待->绑定变量"],
    ["_handle_measurement()", "L492-514", "Measurement：参数构建->线程池执行->阈值判定"],
    ["_handle_loop()", "L518-600", "Loop：YAML加载->per-session循环->结果聚合"],
    ["_judge_measurement()", "L828-860", "Measurement判定：逐项提取->阈值比较->变量绑定"],
    ["_judge_loop_session()", "L890-918", "Loop判定：超时/错误检测->逐键比较->自动判定回退"],
    ["_adapt_loop_call()", "L673-710", "Loop参数自适应：签名分析->最优传参"],
    ["_extract()", "L794-824", "值路径提取：支持dict点号路径和list下标"],
], widths=[4, 2.5, 7.5])

ah(doc, "3.2 ExecutePage 执行页面（execute_page.py，774行）", 2)
at(doc, ["方法", "行号", "功能说明"], [
    ["setup(host)", "L83-226", "加载UI、重排布局、连接信号"],
    ["start_run()", "L491-513", "校验计划->构建EngineWorker->启动线程"],
    ["_build_engine()", "L515-527", "创建EngineWorker并connect全部12个信号"],
    ["_on_case_state()", "L562-570", "更新树节点状态颜色和耗时"],
    ["_on_pop_requested()", "L548-558", "在GUI线程创建PopDialog并同步结果"],
    ["_on_run_finished()", "L616-640", "测试结束：记录统计/更新UI/显示节拍"],
    ["apply_font_scale()", "L264-315", "按全局字体设置缩放title/sn_value/btn_manage/overall"],
    ["apply_small_font()", "L229-262", "对小字控件追加内联QSS覆盖UI文件中的font-size"],
    ["_update_stats_display()", "L708-720", "从stats.ini读取并刷新总数量/良品率/不良率"],
    ["record_result()", "L722-730", "累加生产统计并持久化到stats.ini"],
], widths=[4, 1.5, 8.5])

ah(doc, "3.3 脚本加载机制（script_loader.py，203行）", 2)
at(doc, ["函数", "功能"], [
    ["ensure_runtime_paths()", "将 data/ext_packages/ 和 scripts/ 加入 sys.path（单次）"],
    ["load_script_module(path)", "按路径加载.py为module，基于mtime缓存"],
    ["load_function(path, func_name)", "加载脚本并返回指定函数对象"],
    ["list_functions(path)", "扫描脚本中所有公开函数，返回{name, doc, params}"],
    ["register_api(api_functions)", "将API注册到test_api模块"],
], widths=[4, 10])

ah(doc, "3.4 报告生成模块（report.py，600行）", 2)
at(doc, ["类/函数", "功能"], [
    ["RunLogger", "线程安全日志写入器，每行带时间戳和级别前缀"],
    ["ReportBuilder.build_html()", "生成含总览+逐用例表格+Loop子表的HTML报告"],
    ["save_local_report()", "生成HTML报告并追加汇总到运行日志"],
    ["send_json_cases()", "逐用例JSON POST上报，Loop展开为list子项"],
    ["send_remote_report()", "MES格式JSON POST上报"],
    ["upload_reports()", "multipart/form-data上传HTML报告+日志文件"],
    ["cleanup_old_reports()", "删除修改时间早于今天凌晨的文件"],
], widths=[4, 10])

ah(doc, "4. 异常处理设计", 1)
at(doc, ["异常场景", "处理方式", "影响范围"], [
    ["脚本加载失败", "log_error + last_load_error() + 用例FAIL", "单个用例"],
    ["脚本执行超时", "_wait_with_control 返回timeout + 用例FAIL", "单个用例"],
    ["用例fail_policy=pause", "设置pause_event + sig_phase(paused)", "暂停执行"],
    ["stop_flag置位", "_should_abort() 返回True + 终止当前轮", "当前轮次"],
    ["日志文件创建失败", "降级使用NullLogger", "不影响执行"],
    ["HTML报告生成异常", "fallback报告 + syslog.exception", "仅报告"],
    ["远程上报失败", "记录WARN + 不影响本地流程", "仅上报"],
    ["主线程未捕获异常", "sys.excepthook -> system日志 + 弹窗", "进程退出"],
], widths=[4, 6, 4])

ah(doc, "5. 配置文件设计", 1)
at(doc, ["配置项", "key", "类型", "有效范围", "默认值", "说明"], [
    ["font_size", "int", "9~32", "14", "UI主字体大小"],
    ["exec_small_font", "int", "8~24", "12", "执行页小控件字体"],
    ["window_width", "int", "800~4096", "1280", "窗口宽度"],
    ["window_height", "int", "600~4096", "800", "窗口高度"],
    ["speed_factor", "float", "0.01~10.0", "1.0", "延时缩放系数"],
    ["clear_password", "str", "-", "'''0000'''", "清零统计密码"],
    ["station_id", "str", "-", "'''01'''", "工位ID"],
    ["station_name", "str", "-", "'''机器人测试01工位'''", "工位显示名称"],
], widths=[2.5, 2.5, 2, 2, 2, 2.5, 2, 2])

sec = doc.sections[0]
footer = sec.footer; fp = footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = fp.add_run("测试用例管理与执行系统  详细设计说明书  V1.0  2026-09-11")
r.font.size = Pt(8); r.font.color.rgb = RGBColor(0x99,0x99,0x99); sf(r)

doc.save(os.path.join(OUTPUT_DIR, "详细设计.docx"))
print("OK: 详细设计.docx")
