from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QTextBrowser,
                             QVBoxLayout)


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("帮助 - 使用说明")
        self.resize(860, 640)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setHtml(self._content())
        layout.addWidget(browser)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.button(QDialogButtonBox.Close).setText("关闭")
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.reject)
        layout.addWidget(btns)

    def _content(self):
        return """<style>
h2 { color: #2c5aa0; margin-top: 18px; }
h3 { color: #34495e; margin-top: 14px; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #e1e4e8; padding: 6px 8px; font-size: 13px; vertical-align: top; }
th { background: #f8f9fa; }
code { background: #f0f2f5; padding: 1px 5px; border-radius: 3px; }
.note { background: #fff8e1; border-left: 4px solid #f39c12; padding: 8px 12px; margin: 8px 0; }
.ok { background: #e8f5e9; border-left: 4px solid #27ae60; padding: 8px 12px; margin: 8px 0; }
</style>

<h2>一、测试脚本内置 API 函数</h2>
<p>测试脚本（Action / Measurement / Loop）中可以直接使用以下函数（已自动注入脚本命名空间），
也可以 <code>import test_api</code> 后使用。</p>
<table>
<tr bgcolor='#f8f9fa'><th>函数</th><th>说明</th><th>示例</th></tr>
<tr><td><b>set_sn_to_Panel(sn)</b></td><td>脚本把SN传给软件，显示到报告与执行页面（适合脚本弹窗取SN的场景）</td><td><code>set_sn_to_Panel(sn)</code></td></tr>
<tr><td><b>get_sn()</b></td><td>获取操作人员在执行页面输入的SN</td><td><code>sn = get_sn()</code></td></tr>
<tr><td><b>get_test_result()</b></td><td>获取当前用例的执行结果 (True/False)</td><td><code>r = get_test_result()</code></td></tr>
<tr><td><b>get_last_test_result()</b></td><td>获取上一个用例的执行结果</td><td><code>r = get_last_test_result()</code></td></tr>
<tr><td><b>get_last_test_detail()</b></td><td>获取上一个用例的结果详情文本</td><td><code>d = get_last_test_detail()</code></td></tr>
<tr><td><b>log(msg)</b></td><td>向执行日志输出一条消息</td><td><code>log("测量值=%.3f" % v)</code></td></tr>
<tr><td><b>set_display_info(k, v)</b></td><td>设置执行页面显示的额外信息</td><td><code>set_display_info("电压", "12.5V")</code></td></tr>
<tr><td><b>get_display_info(k)</b></td><td>读取执行页面显示的额外信息</td><td><code>v = get_display_info("电压")</code></td></tr>
<tr><td><b>get_variable(name)</b></td><td>读取全局变量的值</td><td><code>t = get_variable("目标温度")</code></td></tr>
<tr><td><b>set_variable(name, value)</b></td><td>写入全局变量值（不存在会自动创建）</td><td><code>set_variable("结果", True)</code></td></tr>
<tr><td><b>set_robot_temperature(v)</b></td><td>更新执行页底部机器人状态栏：温度</td><td><code>set_robot_temperature(25.5)</code></td></tr>
<tr><td><b>set_robot_current(v)</b></td><td>更新机器人状态栏：电流</td><td><code>set_robot_current(1.2)</code></td></tr>
<tr><td><b>set_robot_voltage(v)</b></td><td>更新机器人状态栏：电压</td><td><code>set_robot_voltage(12.3)</code></td></tr>
<tr><td><b>set_robot_battery(v)</b></td><td>更新机器人状态栏：电池百分比</td><td><code>set_robot_battery(87)</code></td></tr>
<tr><td><b>set_robot_status(**kw)</b></td><td>一次更新多个机器人状态字段</td><td><code>set_robot_status(current=1.2, voltage=12.3)</code></td></tr>
<tr><td><b>get_robot_status(key=None)</b></td><td>读取机器人状态（不传 key 返回全部）</td><td><code>v = get_robot_status("voltage")</code></td></tr>
<tr><td><b>set_measure_result(value, expected, unit, upper, lower, message, passed)</b></td><td><b>Loop 自描述测量</b>：把本次 session 的实际值/期望值/阈值上下限/单位/打印信息/判定写入结果，报告显示并逐条上报后端</td><td><code>set_measure_result(12.2, 12.0, upper=13.0, lower=11.0, passed=True)</code></td></tr>
<tr><td><b>set_measure_value(value, unit=None)</b></td><td>只设置实际测量值（可带单位）</td><td><code>set_measure_value("ok", unit="cmd")</code></td></tr>
<tr><td><b>set_measure_expected(expected)</b></td><td>设置期望值</td><td><code>set_measure_expected(12.0)</code></td></tr>
<tr><td><b>set_measure_range(lower, upper)</b></td><td>设置阈值下限/上限</td><td><code>set_measure_range(11.0, 13.0)</code></td></tr>
<tr><td><b>set_measure_message(message)</b></td><td>设置打印信息（报告/日志显示）</td><td><code>set_measure_message("电压正常")</code></td></tr>
<tr><td><b>set_measure_field(key, value)</b></td><td>写入任意自定义字段（如 <code>code</code>/<code>msg</code>）</td><td><code>set_measure_field("code", 0)</code></td></tr>
<tr><td><b>get_measure_result()</b></td><td>读取本次 session 写入的全部测量字段</td><td><code>d = get_measure_result()</code></td></tr>
<tr><td><b>get_measure_value() / get_measure_expected()</b></td><td>读取实际值 / 期望值</td><td><code>v = get_measure_value()</code></td></tr>
<tr><td><b>get_measure_range()</b></td><td>读取阈值区间，返回 <code>(lower, upper)</code></td><td><code>lo, hi = get_measure_range()</code></td></tr>
<tr><td><b>get_measure_message()</b></td><td>读取打印信息</td><td><code>m = get_measure_message()</code></td></tr>
<tr><td><b>reset_measure_result()</b></td><td>清空本次 session 的测量字段（下个 session 自动清空）</td><td><code>reset_measure_result()</code></td></tr>
<tr><td><b>set_upload_key(key)</b></td><td>设置逐用例 JSON 上报密钥（脚本从产品/服务器获取后回填，上报报文 <code>key</code> 字段自动取该值）</td><td><code>set_upload_key("KEY-123")</code></td></tr>
<tr><td><b>get_upload_key()</b></td><td>读取已设置的上报密钥</td><td><code>k = get_upload_key()</code></td></tr>
</table>

<div class="ok"><b>Loop 中使用示例</b>（脚本函数内调用内置 API，仅适用于 Loop 类型用例）：
<pre>def measure_http(params):
    import requests, json
    resp = requests.post("http://" + params["url"],
                         json={"cmd": "list"}, timeout=5)
    body = resp.json()          # {"code":0, "msg":"ok"}
    code, msg = body.get("code"), body.get("msg")
    # 实际值 / 期望值 / 阈值上下限 / 打印信息 / 判定 全部交给软件
    set_measure_value(msg, unit="cmd")
    set_measure_expected("ok")
    set_measure_field("code", code)
    set_measure_field("msg", msg)
    set_measure_message("%s: 响应 code=%s msg=%s" % (params["name"], code, msg))
    set_measure_result(passed=(code == 0 and msg == "ok"))
    return True   # 返回体不影响测量字段，字段以 set_measure_* 为准
</pre>
优先级：<b>页面「覆盖配置」的期望值阈值优先</b>（与 expected 逐键判定不冲突）；
页面未配置 expected 时，使用 <code>set_measure_*</code> 写入的 value/expected/upper/lower/message 展示并上报。</div>

<div class="note"><b>机器人状态示例</b>（脚本中调用，执行页最底部状态栏实时刷新）：
<pre>def report_robot_status(temperature=25.5, current=1.2, voltage=12.3, battery=87):
    set_robot_temperature(temperature)
    set_robot_current(current)
    set_robot_voltage(voltage)
    set_robot_battery(battery)
    return {"ok": True}</pre>
未调用时对应项显示 <code>--</code>。</div>

<h2>二、全局变量的使用方式（重要）</h2>
<div class="ok"><b>引用全局变量直接写「变量名」，不需要花括号。例如变量名为「目标温度」，
一律写 <code>目标温度</code>，不是 <code>{目标温度}</code>。</b></div>
<div class="note"><b>存储位置：全局变量随测试计划（PLAN）保存</b>。打开计划时自动加载该计划保存的变量；
在【全局变量管理】页面编辑后，<b>保存计划</b>时写入计划文件。不同计划互不影响。</div>

<h3>2.1 三种使用场景</h3>
<table>
<tr bgcolor='#f8f9fa'><th>场景</th><th>操作方法</th></tr>
<tr><td><b>Measurement 参数</b></td>
<td>参数表里：<b>来源</b>列选择「变量」，<b>值/绑定变量</b>列填变量名（如 <code>目标温度</code>）。
执行时自动读取该变量的当前值作为入参。</td></tr>
<tr><td><b>Measurement / Pop 返回值存储</b></td>
<td>「绑定变量」列填变量名，执行完成自动把返回值写入该变量（不存在会自动创建）。</td></tr>
<tr><td><b>脚本内读写</b></td>
<td><code>get_variable("变量名")</code> 读取；<code>set_variable("变量名", 值)</code> 写入。</td></tr>
</table>

<h3>2.2 支持的变量类型</h3>
<p>int / str / float / list / dict。list 与 dict 在页面里用 JSON 文本填写，例如 <code>[1,2,3]</code>、<code>{"a":1}</code>。</p>

<h2>三、返回值：绑定变量 与 判定（重要）</h2>
<div class="ok"><b>「绑定变量」是可选的，留空也可以判定。</b> 判定只看「判定方式 + 阈值/范围」两列。</div>
<table>
<tr bgcolor='#f8f9fa'><th>列</th><th>说明</th></tr>
<tr><td><b>返回项</b></td><td>从返回值中取哪个值，详细规则见下方 3.2。
<b>标量返回值</b>（数字/字符串）写 <code>return</code>；
<b>list/dict 返回值</b>支持整体取用、按键名/下标/点号路径取值。</td></tr>
<tr><td><b>绑定变量</b></td><td>可选。填写后执行完自动把该返回项的值写入全局变量；留空则只记录不存储。</td></tr>
<tr><td><b>判定方式</b></td><td>等于 / 不等于 / 大于 / 小于 / 范围内 / 包含。</td></tr>
<tr><td><b>阈值/范围</b></td><td>判定条件，见下表。</td></tr>
</table>

<h3>3.1 判定阈值写法</h3>
<table>
<tr bgcolor='#f8f9fa'><th>判定方式</th><th>阈值写法</th><th>示例</th><th>含义</th></tr>
<tr><td>等于</td><td>单个值</td><td><code>10</code></td><td>返回值 == 10</td></tr>
<tr><td>不等于</td><td>单个值</td><td><code>0</code></td><td>返回值 != 0</td></tr>
<tr><td>大于</td><td>单个值</td><td><code>200</code></td><td>返回值 &gt; 200</td></tr>
<tr><td>小于</td><td>单个值</td><td><code>0.5</code></td><td>返回值 &lt; 0.5</td></tr>
<tr><td>范围内</td><td>下限~上限（也支持逗号）</td><td><code>100~300</code> 或 <code>100,300</code> 或 <code>100，300</code></td><td>100 ≤ 返回值 ≤ 300</td></tr>
<tr><td>包含</td><td>子串</td><td><code>pass</code></td><td>返回值字符串中包含 pass</td></tr>
</table>
<p><b>阈值留空</b>：该行只记录返回值，不做判定，不影响用例结果。</p>

<div class="note"><b>示例</b>：函数返回 <code>234</code>，要判断它在 100~300 之间：<br/>
返回项=<code>return</code>，判定方式=<code>范围内</code>，阈值=<code>100,300</code>，绑定变量留空 → 判定 PASS，
同时不会写入任何全局变量。</div>

<h3>3.2 返回值为 list / dict 时的处理（重要）</h3>
<p>返回值可能是 list（如 <code>[10, 20, 30]</code>）或 dict（如 <code>{"temp":45, "voltage":12.5}</code>）。
「返回项」决定取哪个值：</p>
<table>
<tr bgcolor='#f8f9fa'><th>返回类型</th><th>返回项写法</th><th>结果</th></tr>
<tr><td rowspan="2">list / dict</td><td><code>return</code> 或 <code>*</code></td><td>取<b>整个</b> list / dict 进行判定或绑定</td></tr>
<tr><td>dict 取键：<code>voltage</code>；嵌套：<code>nested.a.b</code></td><td>取 dict 中该键的值</td></tr>
<tr><td>list</td><td>下标 <code>0</code> <code>1</code> <code>2</code>...</td><td>取 list 对应位置的元素</td></tr>
</table>

<h3>3.3 list / dict 支持哪些判定</h3>
<table>
<tr bgcolor='#f8f9fa'><th>判定方式</th><th>阈值写法</th><th>含义</th></tr>
<tr><td>等于 / 不等于</td><td>JSON 文本，如 <code>[10, 20, 30]</code>、<code>{"a":1}</code></td><td>结构比较整个 list/dict 是否相等</td></tr>
<tr><td>包含</td><td>list：某个元素值；dict：某个键名</td><td>list 中是否存在该元素 / dict 中是否存在该键</td></tr>
<tr><td>长度</td><td><code>3</code>、<code>&gt;2</code>、<code>&gt;=3</code>、<code>&lt;5</code>、<code>2~4</code>、<code>2,4</code></td><td>list/dict 的元素（键）个数比较</td></tr>
</table>
<div class="ok"><b>示例</b>：函数返回 <code>[10, 20, 30]</code><br/>
1) 判断恰好等于该列表：返回项=<code>return</code>，判定=<code>等于</code>，阈值=<code>[10, 20, 30]</code><br/>
2) 判断包含元素 20：返回项=<code>return</code>，判定=<code>包含</code>，阈值=<code>20</code><br/>
3) 判断元素个数在 2~4 之间：返回项=<code>return</code>，判定=<code>长度</code>，阈值=<code>2~4</code><br/><br/>
<b>示例</b>：函数返回 <code>{"temp":45, "voltage":12.5, "nested":{"a":{"b":7}}}</code><br/>
1) 判断温度&gt;40：返回项=<code>temp</code>，判定=<code>大于</code>，阈值=<code>40</code><br/>
2) 判断嵌套值==7：返回项=<code>nested.a.b</code>，判定=<code>等于</code>，阈值=<code>7</code><br/>
3) 判断存在键 voltage：返回项=<code>return</code>，判定=<code>包含</code>，阈值=<code>voltage</code><br/>
4) 把整个 dict 存入全局变量：返回项=<code>return</code>，绑定变量=<code>whole_dict</code>（判定留空）</div>

<h2>四、五类用例使用说明</h2>
<h3>4.1 Action（仅执行）</h3>
<p>仅调用脚本函数，<b>不判定结果</b>。配置脚本路径 + 函数名即可。报告只记录执行了哪个函数。</p>

<h3>4.2 Delay（延时）</h3>
<p>按设定的时长延时（ms级）。<b>注意：延时受系统设置里的「执行速度系数」统一缩放</b>，
如系数 2.0 时 1000ms 的延时实际等待 2000ms。</p>

<h3>4.3 Pop（人机交互弹窗）</h3>
<p>运行时弹出窗口，操作员点按钮返回 <b>True / False</b>。可把结果存入全局变量，供后续用例或脚本使用。
「是否模态阻塞」选否时，窗口在超时后自动继续。</p>

<h3>4.4 Measurement（测量）</h3>
<p>执行脚本函数并取得返回值。流程：选脚本 → 加载函数 → 选函数（自动解析参数与返回值）→
填参数 → 配置返回项判定/绑定。详细见第二、三节。</p>

<h3>4.5 Loop（循环批量）</h3>
<p>同一函数、多组数据批量执行。数据写在 YAML 文件中。<b>session 键名以页面输入为准</b>
（如填 <code>items</code> 就用 <code>items</code>；找不到时自动回退 <code>sessions/items/data/tests/cases</code>，
YAML 顶层本身就是列表时直接使用）。</p>

<h4>YAML 两种格式（都支持）</h4>
<p><b>格式一：扁平式</b>（整条数据直接作为函数入参，函数内部自行判定，如 <code>measure(params)</code>）：</p>
<pre>items:
  - name: "温度_正常值"
    value: 25.0
    unit: "°C"
    min_val: 20.0
    max_val: 30.0</pre>
<p><b>格式二：包装式</b>（<code>input</code> 为入参，<code>expected</code> 为期望值逐项判定）：</p>
<pre>sessions:
  - name: "信号点1"
    input: {signal_ok: true}
    expected: {result: pass}</pre>

<h4>函数调用自适应（兼容各种函数签名）</h4>
<table>
<tr bgcolor='#f8f9fa'><th>函数形式</th><th>调用方式</th></tr>
<tr><td><code>measure(params: dict)</code>（单字典参数）</td><td>整条数据 dict 作为该参数传入</td></tr>
<tr><td><code>check_can(signal_ok=True)</code>（多个形参）</td><td>按形参名关键字传参，键名匹配</td></tr>
<tr><td><code>get_value()</code>（无参数）</td><td>不传参直接调用</td></tr>
<tr><td><code>func(**kwargs)</code> / <code>func(*args)</code></td><td>把整条数据传入</td></tr>
</table>

<h4>判定规则（三层）</h4>
<ol>
<li><b>页面覆盖配置</b>：键名与 expected 同名时优先使用页面值。</li>
<li><b>expected 判定</b>：YAML 定义了 <code>expected</code> 时逐项比较；值写 <code>20~30</code> / <code>20,30</code> 自动按范围判定。</li>
<li><b>自动判定</b>：没有 expected 时，自动从函数返回值中找 <code>pass / passed / ok / success / result / status</code>
字段判定（布尔值或 <code>pass/fail/ok/ng</code> 等字符串）。</li>
</ol>

<h4>自描述测量 API 函数（demo_instrument.py 提供）</h4>
<p>用于 <b>Loop 类型</b>用例，结果字典自带：<b>实际值 <code>value</code>、期望值 <code>expected</code>、
阈值上限 <code>upper</code>、阈值下限 <code>lower</code>、打印信息 <code>message</code></b>
以及判定 <code>pass/ok</code>。这些字段会直接显示在报告中，并逐条上报到后端服务器。</p>
<p><b>生效条件 / 优先级</b>：<b>页面「覆盖配置」输入的阈值优先</b>（键 <code>upper</code>/<code>lower</code>/
<code>expected</code> 等直接注入函数参数）；页面未配置时，YAML 数据中 <code>upper</code>/<code>lower</code>/
<code>value</code>/<code>msg</code> 等字段生效；再没有才用函数默认值。</p>
<table>
<tr bgcolor='#f8f9fa'><th>函数</th><th>用途</th></tr>
<tr><td><code>measure_api_http(params)</code></td><td>通用 HTTP 接口测量（POST/GET）。期望 code==0、msg==ok；
支持 <code>expected_code</code>/<code>expected_msg</code>/<code>upper</code>/<code>lower</code> 覆盖；返回实际响应与判定信息。</td></tr>
<tr><td><code>measure_board_list(params)</code></td><td>板卡硬件检查（list 指令）专用封装。</td></tr>
<tr><td><code>measure_bsp_version(params)</code></td><td>BSP 固件版本检查（describe 指令）专用封装。</td></tr>
<tr><td><code>measure_api_value(params)</code></td><td>静态值/区间测量：按 <code>upper</code>/<code>lower</code> 区间或
<code>value==expected</code> 判定，字段均可由页面覆盖。</td></tr>
<tr><td><code>api_result(value, expected, ...)</code></td><td>构造标准结果字典的辅助函数，供自定义测量函数复用。</td></tr>
</table>
<p><b>YAML 用法示例</b>（扁平式，页面 key 填函数名，session 键名随意）：</p>
<pre># 配合 measure_api_http（expected 为字典 -> 框架逐键判定 + 逐键上报）
-api_http_:
  - name: "板卡硬件检查"
    url: "192.168.88.10:8090/api/request"
    method: "post"
    unit: "cmd"
    param: {"cmd": "list"}
    expected: {"code": 0, "msg": "ok"}

# 配合 measure_api_value（expected 非字典 -> 函数自带 value/upper/lower/message 上报）
-api_value_:
  - name: "电压检查"
    value: 12.2
    expected: 12.0
    unit: "V"
    upper: 13.0
    lower: 11.0
    msg: "电压正常"</pre>
<p><b>页面覆盖优先示例</b>：在 Loop 用例【覆盖配置】里加一行
<code>upper</code>（类型 float，值 <code>14.0</code>），则该上限会覆盖 YAML 里的 <code>13.0</code> 参与判定与上报。</p>

<h2>五、通用配置项说明</h2>
<table>
<tr bgcolor='#f8f9fa'><th>配置项</th><th>说明</th></tr>
<tr><td>超时时间(ms)</td><td>用例最长执行时间。<b>-1 表示无限等待</b>。超时后该用例判为「超时」失败。</td></tr>
<tr><td>失败重试次数</td><td>失败后自动重试的次数，重试次数用完后仍失败才判 FAIL（0 表示不重试）。</td></tr>
<tr><td>失败策略</td><td>「失败后暂停执行」：该用例失败后整个流程暂停，需手动点「继续」；
「失败后继续下一条」：忽略失败继续跑后续用例。</td></tr>
<tr><td>跳过此用例</td><td>勾选后执行测试时<b>不执行</b>该用例，树与报告中标记为「跳过」；
跳过的用例不参与通过/失败统计，也不影响总体结果。</td></tr>
</table>

<h2>六、脚本用到的第三方库（外部扩展包）</h2>
<p>测试脚本（Action / Measurement / Loop）可以用任何标准库（含 <code>requests</code>、<code>json</code>、<code>time</code> 等），
也能用第三方库。软件已内置：<b>PyQt5、PyYAML、requests、paramiko</b> —— 做 HTTP 接口测试时
直接 <code>import requests</code> 即可。</p>
<p>如果你的脚本还需要<b>别的</b>第三方库（例如 <code>pyserial</code>、<code>numpy</code>、<code>cryptography</code>），
不需要重新打包软件，按下面方法加到「外部扩展包目录」即可：</p>
<table>
<tr bgcolor='#f8f9fa'><th>步骤</th><th>操作</th></tr>
<tr><td>1. 找到目录</td><td>程序根目录下 <code>data/ext_packages/</code>（打包程序为 <code>dist/EOL/data/ext_packages/</code>，
源码运行为项目下 <code>data/ext_packages/</code>）。首次启动软件时自动创建。</td></tr>
<tr><td>2. 下载包（联网电脑）</td><td>例：<code>pip download --only-binary=:all: -d 下载目录 pyserial</code>
（<b>必须与产线电脑平台一致</b>，如 Windows <code>win_amd64</code>、Linux<code>manylinux2014_x86_64</code>、ARM64 <code>linux_aarch64</code>）。</td></tr>
<tr><td>3. 放入目录</td><td>把 <code>.whl</code> 文件或解压后的包文件夹复制到 <code>data/ext_packages/</code> 下。</td></tr>
<tr><td>4. 重启软件</td><td>重启后测试脚本即可 <code>import</code> 这些库。</td></tr>
</table>
<div class="note"><b>产线电脑直接联网安装（若允许联网）</b>：在程序目录下执行
<pre>cd dist/EOL/data/ext_packages
pip install --target . pyserial numpy</pre></div>
<div class="ok"><b>HTTP 接口测试快速起步</b>（requests 已内置，无需添加扩展包）：
<pre>import requests
def http_add(a: int, b: int) -&gt; dict:
    resp = requests.post("http://192.168.1.50:8000/add", json={"a": a, "b": b}, timeout=5)
    data = resp.json()
    return {"sum": data.get("sum"), "pass": str(data.get("code")) == "0"}</pre>
按「Measurement」用例如法配置参数与返回值判定即可。</div>
<p><b>注意</b>：跨平台拷贝 C 扩展包（numpy、cryptography、pyserial 等）必须与目标电脑平台一致；
<code>data/ext_packages/</code> 下不要放名为 <code>test_api</code>、<code>app</code>、<code>scripts</code> 的文件夹；
有传递依赖的包要把依赖一起放进去。</p>

<h2>七、逐用例 JSON 数据上报</h2>
<p>在【计划设置 → 逐用例 JSON 数据上报】勾选「启动」并填写<b>上报接口地址</b>后，
每轮测试结束会自动把<b>每个测试用例的结果</b>以 JSON POST 到服务器存储。</p>
<table>
<tr bgcolor='#f8f9fa'><th>项</th><th>说明</th></tr>
<tr><td>上报密钥</td><td>由<b>测试脚本</b>从产品/服务器获取后调用 <code>set_upload_key(key)</code> 回填，上报报文的 <code>key</code> 字段自动关联该值；计划设置里不再手工填写。</td></tr>
<tr><td>上报时机</td><td>一轮测试全部结束后统一 POST 一次；上传成功/失败均写入执行日志，失败不影响本地流程。</td></tr>
</table>
<h3>7.1 报文结构（test_time 为毫秒级）</h3>
<pre>{
  "key": "密钥",
  "sn": "SN号",
  "batch": "批次号",
  "test_time": "2026-08-18 16:11:27.123",
  "records": [
    {
      "test_time": "2026-08-18 16:11:27.123",
      "batch": "批次号",
      "sn": "SN号",
      "case_name": "用例名称",
      "type": "measurement",
      "status": "PASS",
      "value": 12.2,
      "expected": "11~13",
      "threshold_upper": 13.0,
      "threshold_lower": 11.0
    },
    {
      "test_time": "2026-08-18 16:11:27.123",
      "batch": "批次号",
      "sn": "SN号",
      "case_name": "002_Orin状态读取",
      "type": "loop",
      "status": "FAIL",
      "value": null,
      "expected": null,
      "threshold_upper": null,
      "threshold_lower": null,
      "list": [
        {"name": "板卡硬件检查", "status": "PASS", "value": "ok",
         "expected": "code=0, msg=ok", "threshold_upper": null,
         "threshold_lower": null, "message": "判定详情"}
      ]
    }
  ]
}</pre>
<p><code>records</code> 每个测试用例一条，都带 <code>type</code>（用例类型）与 <code>status</code>（PASS/FAIL/跳过）：
Measurement 每个「返回项」一条；<b>Loop 父节点一条 + <code>list</code> 子项数组</b>（子项含 name/status/value/expected/上下限/message）；
其它类型一条（实际值取结果状态）。加字段的位置见软件说明文档 11.3 小节。</p>

<h3>7.2 阈值（上下限）取值规则 —— 固定值</h3>
<table>
<tr bgcolor='#f8f9fa'><th>阈值写法</th><th>threshold_upper</th><th>threshold_lower</th></tr>
<tr><td>单值固定值：<code>5</code>、<code>100</code>（等于判定）</td><td>5 / 100</td><td>5 / 100</td></tr>
<tr><td>范围：<code>11~13</code> 或 <code>11,13</code></td><td>13</td><td>11</td></tr>
<tr><td>大于：<code>200</code></td><td>（空）</td><td>200</td></tr>
<tr><td>小于：<code>0.5</code></td><td>0.5</td><td>（空）</td></tr>
<tr><td>非数值（<code>pass</code>/<code>fail</code>/<code>OK</code> 等）</td><td>（空）</td><td>（空）</td></tr>
</table>
<div class="note"><b>Loop 阈值提示</b>：YAML <code>expected</code> 里写数值即固定值（上下限相同），写 <code>20~30</code>
/ <code>20,30</code> 即范围；非数值文本（如 <code>pass</code>）上下限为空，实际值按该文本上报。</div>

<h2>八、脚本编写注意事项</h2>
<ul>
<li>脚本必须是可被 Python 导入的 <code>.py</code> 文件，无语法错误、无未安装的第三方依赖。</li>
<li>需要访问软件状态时用内置 API（第一节），不要自行读取软件数据文件。</li>
<li>脚本执行在工作线程，避免在脚本内直接操作界面；显示信息请用 <code>set_display_info</code>。</li>
<li>脚本抛出的异常会被软件捕获并记为失败，不会导致软件崩溃，但请尽量自行捕获可控异常。</li>
</ul>

<h2>九、执行与统计注意事项</h2>
<ul>
<li>未输入 SN 直接点开始，软件会一直等待扫描/输入；连续测试模式下同样等待下一个 SN。</li>
<li>执行页面总体状态 PASS/FAIL 会一直保留，直到下一个 SN 开始测试。</li>
<li><b>手动停止的运行不计入生产统计</b>，避免误判不良。</li>
<li>「清零统计」需要密码，初始 <b>0000</b>，可在【系统设置】中修改。</li>
<li>每个测试计划独立配置报告存储方式（本地 / 远程），互不影响。</li>
</ul>
"""
