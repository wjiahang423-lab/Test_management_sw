"""树莓派 mock_api_server 接口巡检脚本（配合“连续”模式使用）。

目标服务器：http://10.5.35.49:5000（Pi 上运行 mock_api_server.py）
接口：
  GET  /api/robot/status  设备(机器人)状态：温度/电流/电压/电量
  GET  /api/voltage|current|temperature|battery  数值测量
  POST /submit            数据上报
所有函数返回 dict，供 Measurement 用例逐项判定（code==0、电压/电流/电量范围等）。
"""
import json
import time

try:
    import requests as _requests
except Exception:
    _requests = None

_BASE = "http://10.5.35.49:5000"


def _request(method, url, timeout_s, body=None):
    try:
        if _requests is not None:
            if method == "GET":
                r = _requests.get(url, timeout=timeout_s)
            else:
                r = _requests.post(url, data=body or b"", timeout=timeout_s,
                                   headers={"Content-Type": "text/plain; charset=utf-8"})
            text = r.text or ""
            http_code = getattr(r, "status_code", 200)
        else:
            import urllib.request
            req = urllib.request.Request(url, data=body, method=method)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                text = resp.read().decode("utf-8", "replace")
                http_code = getattr(resp, "status", 200)
    except Exception as e:
        return {"code": -1, "error": "{}: {}".format(type(e).__name__, e)}
    try:
        j = json.loads(text or "{}")
    except Exception:
        j = {"raw": text}
    if not isinstance(j, dict):
        j = {"raw": text}
    j.setdefault("code", http_code)
    return j


def _base_url(base):
    return str(base or _BASE).rstrip("/")


def device_status(base=_BASE, timeout_s=5):
    """读设备(机器人)状态并回填执行页机器人状态区；返回各字段供判定。"""
    resp = _request("GET", _base_url(base) + "/api/robot/status", timeout_s)
    data = resp.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    result = {
        "temperature": data.get("temperature"),
        "current": data.get("current"),
        "voltage": data.get("voltage"),
        "battery": data.get("battery"),
        "code": resp.get("code"),
    }
    try:
        import test_api
        test_api.set_robot_status(
            temperature=result["temperature"],
            current=result["current"],
            voltage=result["voltage"],
            battery=result["battery"])
    except Exception:
        pass
    return result


def measure_pi(path="/api/voltage", base=_BASE, timeout_s=5):
    """调用 Pi 测量类接口，返回 {value, unit}。"""
    if str(path).startswith("/api/"):
        endpoint = str(path)
    else:
        endpoint = "/api/{}".format(str(path).lstrip("/"))
    resp = _request("GET", _base_url(base) + endpoint, timeout_s)
    data = resp.get("data")
    if isinstance(data, dict):
        return {"value": data.get("value"), "unit": data.get("unit", ""), "code": resp.get("code")}
    return {"value": data, "unit": "", "code": resp.get("code")}


def submit_pi(base=_BASE, payload="pecial-plan", timeout_s=5):
    """向 Pi 上报一条数据，返回接口 code。"""
    body = json.dumps({
        "tag": payload,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False)
    resp = _request("POST", _base_url(base) + "/submit", timeout_s, body=body)
    return {"code": resp.get("code"), "msg": resp.get("msg", ""), "time": time.strftime("%H:%M:%S")}