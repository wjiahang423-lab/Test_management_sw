"""Built-in API for user test scripts.

The functions are injected into each loaded script's namespace, and are also
available through ``import test_api`` / ``from test_api import *``.

A lazy stub is registered at import time so that user scripts can always be
imported; real implementations are swapped in via :func:`register_api`.
"""
import sys
import types

_registered = {}


def _make_stub_module():
    module = types.ModuleType("test_api")

    def _stub(*args, **kwargs):
        raise RuntimeError("test_api 尚未初始化（软件未完成初始化）")

    def __getattr__(name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _stub

    module.__getattr__ = __getattr__
    return module


def register_api(api_functions):
    """api_functions: dict name -> callable. Injects into 'test_api' module."""
    module = sys.modules.get("test_api")
    if module is None:
        module = _make_stub_module()
        sys.modules["test_api"] = module
    _registered.update(api_functions)
    for name, fn in _registered.items():
        setattr(module, name, fn)


def inject_into_module(module, api_functions):
    """Inject API functions into a loaded script module's globals."""
    for name, fn in api_functions.items():
        setattr(module, name, fn)


# Ensure the stub module exists so scripts can always be imported.
if "test_api" not in sys.modules:
    sys.modules["test_api"] = _make_stub_module()
