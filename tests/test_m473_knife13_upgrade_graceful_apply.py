# -*- coding: utf-8 -*-
"""第473批 刀13 门控单测：自动升级窗口的「应用并重启」改走 apply_now 通道。

病灶（2026-11-07 03:09 现场）
------------------------------
`PulseInnerWorld._check_auto_upgrade` 的 win32 分支用
`kernel32.GenerateConsoleCtrlEvent(0, 0)` 向**整个控制台进程组**发 Ctrl+C：

* `cmd.exe`（`start_tongtong.bat` 那一层）也收到 ⇒ 弹「终止批处理操作吗(Y/N)?」；
* Python 侧 `user_quit_requested=True` ⇒ 退出路径改走
  `_confirm_apply_pending_on_quit` ⇒ 再弹「是否应用并重启验证？(Y/n)」。

实测：`2026-11-07 03:09:31 自动升级窗口触发` 后，日志在 `03:09:58` 的
「停止指令已发出」处断流，`data/restart_notice.txt` 停在 `2026-11-01 12:53`
⇒ 接班进程从未被拉起。用 `start_tongtong.bat` 启动时这两处都会等人工。

判据
----
* 新通道写 `data/evolution/apply_now.json`，主循环自行 apply→stop→spawn；
* 全程不调用 `GenerateConsoleCtrlEvent`；
* 已有在途请求不覆盖（保排队语义）；同批补丁 900s 内不重复写（防窗口内刷盘）；
* 灰度 `ENABLE_M473_UPGRADE_APPLY_NOW=False` ⇒ 逐字回退旧 Ctrl+C（必须可回滚）；
* 写盘失败/驱动异常 ⇒ 同样回退，不允许"什么都不做"地留在原地。
"""

from __future__ import annotations

import ast
import json
import os
import time

import config
import nucleus.evolution.graceful_apply as GA
from nucleus.evolution.EvolutionDriver import EvolutionDriver

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IW = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")


def _flag_path(project_root: str) -> str:
    return os.path.join(project_root, "data", "evolution", "apply_now.json")


def _read_flag(project_root: str) -> dict:
    with open(_flag_path(project_root), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _mark_consumed(project_root: str) -> None:
    """把在途请求标记为已消费（模拟主循环取走），但保留文件。"""
    _p = _flag_path(project_root)
    with open(_p, "r", encoding="utf-8") as fh:
        _d = json.load(fh)
    _d["consumed"] = True
    with open(_p, "w", encoding="utf-8") as fh:
        json.dump(_d, fh, ensure_ascii=False, indent=2)


def _reset_state() -> None:
    GA._M473_STATE.update({"last_ts": 0.0, "last_count": -1})


def _no_fallback(monkeypatch) -> list:
    """把旧 Ctrl+C 路径换成记录器，返回调用记录（用于断言"没走旧路"）。"""
    _calls: list = []
    monkeypatch.setattr(GA, "_m473_legacy_console_ctrl_c", lambda: _calls.append(1))
    return _calls


# ==================== 一、灰度开关语义 ====================


def test_开关缺省为开(monkeypatch):
    """config 未定义该键时按「开」处理（新通道优先，不回退到会卡死的旧路）。"""
    monkeypatch.delattr(config, "ENABLE_M473_UPGRADE_APPLY_NOW", raising=False)
    assert GA.m473_apply_now_enabled() is True


def test_开关显式假关闭(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M473_UPGRADE_APPLY_NOW", False, raising=False)
    assert GA.m473_apply_now_enabled() is False


def test_开关None视作缺省(monkeypatch):
    """★不走 `getattr(...) or 默认值`：None 是"没配"，但 False 必须算数。"""
    monkeypatch.setattr(config, "ENABLE_M473_UPGRADE_APPLY_NOW", None, raising=False)
    assert GA.m473_apply_now_enabled() is True


def test_开关字符串假值(monkeypatch):
    for _v in ("0", "false", "no", "off", " FALSE "):
        monkeypatch.setattr(config, "ENABLE_M473_UPGRADE_APPLY_NOW", _v, raising=False)
        assert GA.m473_apply_now_enabled() is False, _v
    for _v in ("1", "true", "yes", "on"):
        monkeypatch.setattr(config, "ENABLE_M473_UPGRADE_APPLY_NOW", _v, raising=False)
        assert GA.m473_apply_now_enabled() is True, _v


# ==================== 二、apply_now 写入 ====================


def test_写请求落盘且不走旧CtrlC(tmp_path, monkeypatch):
    _reset_state()
    _calls = _no_fallback(monkeypatch)
    _ok = GA.request_upgrade_apply(str(tmp_path), 3)
    assert _ok is True
    assert _calls == [], "新通道成功时绝不能再碰整控制台 Ctrl+C"
    _d = _read_flag(str(tmp_path))
    assert _d["approved_count"] == 3
    assert _d["consumed"] is False
    assert isinstance(_d["requested_at"], float)
    # ★端到端判据来自**消费方自己的 API**（main.py:5287 用的就是这两个口），
    #   而不是复用生产者写的 JSON 字段自证。
    _drv = EvolutionDriver(str(tmp_path))
    assert _drv.has_apply_request() is True
    _req = _drv.consume_apply_request()
    assert isinstance(_req, dict) and _req["approved_count"] == 3
    assert _drv.has_apply_request() is False


def test_已有在途请求不覆盖(tmp_path, monkeypatch):
    """在途请求已排队 ⇒ 不重写（重写会把 requested_at 刷新，失去排队语义）。"""
    _reset_state()
    _no_fallback(monkeypatch)
    assert GA.request_upgrade_apply(str(tmp_path), 1) is True
    _before = _read_flag(str(tmp_path))
    assert GA.request_upgrade_apply(str(tmp_path), 5) is True
    _after = _read_flag(str(tmp_path))
    assert _after == _before
    assert _after["approved_count"] == 1


def test_同批节流窗口内不重写(tmp_path, monkeypatch):
    """被消费后同一批补丁 900s 内不再重写（维护窗口 03:00-04:00 不该反复刷盘）。"""
    _reset_state()
    _no_fallback(monkeypatch)
    assert GA.request_upgrade_apply(str(tmp_path), 2) is True
    _mark_consumed(str(tmp_path))
    _before = _read_flag(str(tmp_path))
    assert GA.request_upgrade_apply(str(tmp_path), 2) is True
    assert _read_flag(str(tmp_path)) == _before


def test_节流窗口过后可重写(tmp_path, monkeypatch):
    _reset_state()
    _no_fallback(monkeypatch)
    assert GA.request_upgrade_apply(str(tmp_path), 2) is True
    _mark_consumed(str(tmp_path))
    GA._M473_STATE["last_ts"] = time.time() - (GA.M473_RETRY_MIN_SEC + 1.0)
    assert GA.request_upgrade_apply(str(tmp_path), 4) is True
    _d = _read_flag(str(tmp_path))
    assert _d["approved_count"] == 4
    assert _d["consumed"] is False


def test_节流窗口是900秒():
    """钉住节流常量：改它等于改"多久重试一次"，须有意为之。"""
    assert GA.M473_RETRY_MIN_SEC == 900.0


# ==================== 三、回退路径 ====================


def test_关闭灰度回退旧CtrlC(tmp_path, monkeypatch):
    _reset_state()
    monkeypatch.setattr(config, "ENABLE_M473_UPGRADE_APPLY_NOW", False, raising=False)
    _calls = _no_fallback(monkeypatch)
    assert GA.request_upgrade_apply(str(tmp_path), 1) is False
    assert _calls == [1]
    assert not os.path.exists(_flag_path(str(tmp_path)))


def test_写盘失败回退旧CtrlC(tmp_path, monkeypatch):
    _reset_state()
    _calls = _no_fallback(monkeypatch)
    monkeypatch.setattr(EvolutionDriver, "request_apply_now", lambda self, n: False)
    assert GA.request_upgrade_apply(str(tmp_path), 1) is False
    assert _calls == [1]


def test_驱动异常回退旧CtrlC(tmp_path, monkeypatch):
    _reset_state()
    _calls = _no_fallback(monkeypatch)

    def _boom(self, n):
        raise OSError("disk gone")

    monkeypatch.setattr(EvolutionDriver, "request_apply_now", _boom)
    assert GA.request_upgrade_apply(str(tmp_path), 1) is False
    assert _calls == [1]


def test_读取开关异常按开处理(monkeypatch):
    """config 不可读时按「开」——宁可走新通道，也不要退回会卡死的那条。"""

    class _Bad:
        def __getattr__(self, name):
            raise RuntimeError("config broken")

    import sys
    monkeypatch.setitem(sys.modules, "config", _Bad())
    assert GA.m473_apply_now_enabled() is True


# ==================== 四、IW 接线（判据：不再直接发 Ctrl+C）====================


def _iw_tree() -> ast.Module:
    with open(_IW, "r", encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _check_auto_upgrade_node() -> ast.AST:
    for _n in ast.walk(_iw_tree()):
        if isinstance(_n, ast.FunctionDef) and _n.name == "_check_auto_upgrade":
            return _n
    raise AssertionError("PulseInnerWorld 里没有 _check_auto_upgrade")


def test_IW不再直接调用GenerateConsoleCtrlEvent():
    """★这是本刀的核心判据：整控制台 Ctrl+C 必须从 IW 里彻底消失。"""
    _hits = [n for n in ast.walk(_check_auto_upgrade_node())
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "GenerateConsoleCtrlEvent"]
    assert _hits == [], f"_check_auto_upgrade 仍在直接发控制台 Ctrl+C：{[h.lineno for h in _hits]}"


def test_IW改调新口并传真实参():
    """调用必须用 `_project_root` 与 `len(_approvable)`，不能写死数字或路径。"""
    _calls = [n for n in ast.walk(_check_auto_upgrade_node())
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name)
              and n.func.id == "request_upgrade_apply"]
    assert len(_calls) == 1, f"request_upgrade_apply 调用数={len(_calls)}"
    _args = _calls[0].args
    assert len(_args) == 2
    assert isinstance(_args[0], ast.Name) and _args[0].id == "_project_root"
    assert isinstance(_args[1], ast.Call) and isinstance(_args[1].func, ast.Name)
    assert _args[1].func.id == "len"
    assert isinstance(_args[1].args[0], ast.Name) and _args[1].args[0].id == "_approvable"


def test_IW调用仍在try内且保留return():
    """原结构是 try: <发信号>; return  —— 替换后仍须 return，不能变成"发完继续往下走"。"""
    _node = _check_auto_upgrade_node()
    _found = False
    for _t in ast.walk(_node):
        if not isinstance(_t, ast.Try):
            continue
        for _i, _st in enumerate(_t.body):
            if (isinstance(_st, ast.Expr) and isinstance(_st.value, ast.Call)
                    and getattr(_st.value.func, "id", "") == "request_upgrade_apply"):
                _rest = _t.body[_i + 1:]
                assert _rest and isinstance(_rest[0], ast.Return), "调用后紧跟的 return 丢了"
                _found = True
    assert _found, "request_upgrade_apply 不在任何 try 体内"


# ==================== 五、模块级接线 ====================


def test_模块不再自带ctypes调用():
    """回退路径的 ctypes 调用只允许出现在 _m473_legacy_console_ctrl_c 里。"""
    with open(GA.__file__, "r", encoding="utf-8") as fh:
        _src = fh.read()
    _tree = ast.parse(_src)
    _owners = []
    for _fn in [n for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef)]:
        for _c in ast.walk(_fn):
            if (isinstance(_c, ast.Call) and isinstance(_c.func, ast.Attribute)
                    and _c.func.attr == "GenerateConsoleCtrlEvent"):
                _owners.append(_fn.name)
    assert _owners == ["_m473_legacy_console_ctrl_c"], _owners


def test_公开口幂等两次不炸(tmp_path, monkeypatch):
    _reset_state()
    _no_fallback(monkeypatch)
    assert GA.request_upgrade_apply(str(tmp_path), 1) is True
    assert GA.request_upgrade_apply(str(tmp_path), 1) is True
    assert _read_flag(str(tmp_path))["approved_count"] == 1
