"""Load Python test scripts, parse function signatures, and call functions."""
import importlib.util
import inspect
import os
import sys
import threading

from . import api as _api
from . import syslog
from .api import inject_into_module
from .paths import EXT_PACKAGES_DIR, SCRIPTS_DIR, resolve_root_path

_cache_lock = threading.Lock()
_module_cache = {}
_runtime_paths_ready = False


def ensure_runtime_paths():
    """把脚本可用的第三方包搜索路径挂到 sys.path 上。

    依次加入：
      1. data/ext_packages  —— 外部扩展包目录（执行电脑上可随时添加，无需重打包）
      2. scripts/           —— 测试脚本所在目录（脚本间可互相 import）

    仅执行一次；目录不存在时自动创建 ext_packages 以便用户放包。
    """
    global _runtime_paths_ready
    if _runtime_paths_ready:
        return
    with _cache_lock:
        if _runtime_paths_ready:
            return
        for d in (EXT_PACKAGES_DIR, SCRIPTS_DIR):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass
            if os.path.isdir(d) and d not in sys.path:
                sys.path.insert(0, d)
        _runtime_paths_ready = True


def load_script_module(script_path):
    """Load a .py file as a module. Cached. Returns module or None on error."""
    ensure_runtime_paths()
    script_path = resolve_root_path(script_path)
    if not os.path.isfile(script_path):
        return None
    mtime = os.path.getmtime(script_path)
    with _cache_lock:
        cached = _module_cache.get(script_path)
        if cached and cached[0] == mtime:
            return cached[1]
    spec = importlib.util.spec_from_file_location("_test_script_" + str(abs(hash(script_path))), script_path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        api_module = sys.modules.get("test_api")
        if api_module is not None:
            inject_into_module(module, {n: getattr(api_module, n) for n in dir(api_module)
                                        if not n.startswith("__") and callable(getattr(api_module, n))})
        with _cache_lock:
            _module_cache[script_path] = (mtime, module)
        return module
    except Exception as e:
        syslog.error("加载脚本失败：{}（{}{}{}）".format(
            script_path, type(e).__name__, ": " if str(e) else "", e))
        syslog.exception()
        return None


def invalidate_script_cache():
    with _cache_lock:
        _module_cache.clear()


def _infer_type_from_annotation(annotation):
    try:
        if isinstance(annotation, type):
            if annotation is int:
                return "int"
            if annotation is float:
                return "float"
            if annotation is str:
                return "str"
            if annotation is list:
                return "list"
            if annotation is dict:
                return "dict"
            if annotation is bool:
                return "str"
            return annotation.__name__
    except Exception:
        pass
    return None


def infer_type(value):
    if value is None:
        return "str"
    if isinstance(value, bool):
        return "str"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "str"


def get_function_info(func):
    """Return dict with params and returns info for a function."""
    params = []
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        sig = None
    if sig is not None:
        for name, p in sig.parameters.items():
            if name in ("self", "cls"):
                continue
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            ann = p.annotation
            default = p.default if p.default is not inspect.Parameter.empty else None
            ptype = _infer_type_from_annotation(ann) or infer_type(default)
            params.append({
                "name": name,
                "type": ptype,
                "default": default,
                "required": p.default is inspect.Parameter.empty,
            })
    returns = []
    try:
        ann = inspect.signature(func).return_annotation
        rtype = _infer_type_from_annotation(ann) or "str"
        returns.append({"item": "return", "type": rtype})
    except Exception:
        returns.append({"item": "return", "type": "str"})
    return {"params": params, "returns": returns}


def list_functions(script_path):
    """Return list of {name, doc} for callable public functions in the script."""
    module = load_script_module(script_path)
    if module is None:
        return []
    builtin_names = set(_api._registered.keys())
    result = []
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if name.startswith("_"):
            continue
        if name in builtin_names:
            continue
        try:
            doc = (inspect.getdoc(obj) or "").strip().splitlines()
            doc = doc[0] if doc else ""
        except Exception:
            doc = ""
        result.append({"name": name, "doc": doc, "params": get_function_info(obj)})
    return result


def load_function(script_path, func_name):
    """Return the callable function object, or None if not found."""
    module = load_script_module(script_path)
    if module is None:
        syslog.error("无法加载脚本函数：{}（脚本加载失败）{}".format(func_name, script_path))
        return None
    func = getattr(module, func_name, None)
    if callable(func):
        return func
    syslog.error("脚本中未找到函数：{}（{}）".format(func_name, script_path))
    return None
