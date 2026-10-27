# -*- coding: utf-8 -*-
"""第375批 刀12 定向单测：test_33 的红因是**分母不一致**，不是单例缓存污染。

368/372 两批把 `tests/test_log_locate_m91.py::TestT91aCoverage::test_33` 的红
记成「`SelfInspector` 单例索引缓存换干净实例后仍被命中，用例内无法隔离」，
并据此挂了个 blocker 要「产品侧给单例缓存重置口子，或把用例迁独立进程」。

实测三条前提全不成立：
  ① 产品侧**早有**重置口子 —— `nucleus.self_inspector.shutdown_self_inspector()`
     （P0 批次3）已把 `_self_inspector` 置空，本次一行没加；
  ② 换**干净实例**根本不会命中：V3 关闭时 16 个缺口标签命中数恒为 0；
  ③ 真实红因是 test_33 **两个分母不相等**：`_miss` 取自 `self.tags`（活体日志
     TAG），`_still` 却过滤常量 `_GAP_TAGS` ⇒ 日志里暂时没出现的缺口标签必然
     落进 `_still`。372 记录里那 5 个（wecom_chat_bridge / WriteGuard /
     时间中枢 / 胸腺 / 硬件启动器）正是当日日志没出现的，与 V3 开关无关。

本件钉三件事：结论可复现、旧口径必然红、产品口子已存在（不许重复造）。
"""
import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tests"))

import test_log_locate_m91 as T

import nucleus.self_inspector as SI

_M91 = os.path.join(_ROOT, "tests", "test_log_locate_m91.py")
_372_EVID = os.path.join(_ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "372",
                         "刀8_A42依据对照表_20261026.json")

#: 372 刀8 记录里那 5 个「V3 关闭时竟然命中」的标签 —— 复现靶子
_RECORDED_5 = ["wecom_chat_bridge", "WriteGuard", "时间中枢", "胸腺", "硬件启动器"]


def _src():
    with open(_M91, encoding="utf-8") as f:
        return f.read()


# ============================ ① 结论可复现：V3 关闭恒不命中
def test_01_gap_tags_unhittable_with_v3_off():
    """16 个缺口标签在 V3 关闭时**全部**不可定位（用测试件共用实例）。"""
    _insp = T._inspector()
    with T._Switch(T._SWITCH, False):
        _hit = [t for t in T._GAP_TAGS if _insp.locate_issue(t, "").get("file")]
    assert _hit == [], f"V3 关闭仍有命中：{_hit}"


def test_02_fresh_instance_does_not_inherit_cache():
    """★直接反驳「缓存层级比实例更深」：**全新实例**、V3 关闭 ⇒ 两个代表标签不命中。

    只取 2 个（`wecom_chat_bridge` 走 logger 名、`胸腺` 走器官别名，两类各一），
    避免整件重复建全项目类索引（那是秒级）。
    """
    _fresh = SI.SelfInspector()
    _fresh._project_root = _ROOT
    with T._Switch(T._SWITCH, False):
        _hit = [t for t in ("wecom_chat_bridge", "胸腺")
                if _fresh.locate_issue(t, "").get("file")]
    assert _hit == [], f"新实例仍命中（说明真有跨实例缓存）：{_hit}"


# ============================ ② 旧口径必然红（红因复现）
def test_03_old_denominator_goes_red_when_log_misses_tags():
    """复刻 372 时点：日志不含那 5 个标签 ⇒ **旧口径**必然把这 5 个判红。

    旧口径 = `_miss` 取自 tags、`_still` 过滤 `_GAP_TAGS`。这里把 tags 换成
    「只保留其它标签」的列表，算出与 372 记录**同一份**列表。
    """
    _insp = T._inspector()
    _tags_then = [t for t in T._real_tags()[0] if t not in _RECORDED_5]
    with T._Switch(T._SWITCH, False):
        _miss = {t for t in _tags_then if not _insp.locate_issue(t, "").get("file")}
    _still = [t for t in T._GAP_TAGS if t not in _miss]
    assert _still == _RECORDED_5, (
        f"红因复现失败：期望与 372 记录逐字一致的 {_RECORDED_5!r}，实得 {_still!r}")


def test_04_red_reason_is_set_subtraction_not_locate():
    """★关键反证：给一个**完美定位器**（任何标签都返回命中），那 5 个仍然红。

    证明路子：旧口径下某个缺口标签会不会落进 `_still`，只取决于它**在不在
    `self.tags` 里**，与 `locate_issue` 能不能定位毫无关系 —— 不在分母里的东西，
    定位能力再强也进不了 `_miss`。

    ★不换 V3 开关值来做这条反证：把 V3 打开会同时让另外 11 个缺口标签变成
    「已定位」（`_still` 反而变 16），那是**另一个**现象，混在一起就说不清了。
    """
    _tags_then = [t for t in T._real_tags()[0] if t not in _RECORDED_5]

    class _PerfectLocator:
        """桩：任何入参都返回命中（模拟「定位能力无限好」）。"""

        def locate_issue(self, organ, msg, file_hint=""):
            return {"file": "stub.py", "method": "", "line": 0, "confidence": 0.9}

    _stub = _PerfectLocator()
    _miss = {t for t in _tags_then if not _stub.locate_issue(t, "").get("file")}
    _still = [t for t in T._GAP_TAGS if t not in _miss]
    assert set(_RECORDED_5) <= set(_still), (
        "完美定位器下这 5 个竟然被判成可定位 —— 说明红因不是分母错位")
    assert _still == T._GAP_TAGS, (
        f"完美定位器下 `_miss` 应为空、`_still` 应为全部 16 个；实得 {_still!r}")


# ============================ ③ 改后口径不许回退
def test_05_new_probe_does_not_read_tags():
    """test_33 的探测对象必须是 `_GAP_TAGS` 本体，**不许**再从 `self.tags` 派生。

    源码级守卫 + 行为级守卫（test_03/04）各一层：只靠行为的话，
    「日志恰好包含全部 16 个」的那天，改回去也照样绿，回归就看不见了。
    """
    _tree = ast.parse(_src())
    _fn = next((n for n in ast.walk(_tree)
                if isinstance(n, ast.FunctionDef) and n.name.startswith("test_33_")), None)
    assert _fn is not None, "test_33 不见了"
    _names = {n.attr for n in ast.walk(_fn) if isinstance(n, ast.Attribute)}
    _ids = {n.id for n in ast.walk(_fn) if isinstance(n, ast.Name)}
    assert "tags" not in _names and "tags" not in _ids, (
        "test_33 又去读 self.tags 了 —— 分母会重新错位")


def test_06_regression_guard_exists():
    """必须有一条同时钉「旧口径必红」与「新口径不动」的守卫（test_33b）。"""
    _names = {n.name for n in ast.walk(ast.parse(_src()))
              if isinstance(n, ast.FunctionDef)}
    assert "test_33b_gap_probe_does_not_read_live_log" in _names, (
        "缺少 test_33b：改回去没有守卫会立刻红")


# ============================ ④ 产品口子已存在，不许重复造
def test_07_singleton_reset_hook_already_exists(monkeypatch):
    """`shutdown_self_inspector()` 已能把单例置空 —— 本刀**没加**任何产品代码。"""
    assert hasattr(SI, "shutdown_self_inspector"), "产品侧没有单例重置口子"
    monkeypatch.setattr(SI, "_self_inspector", object(), raising=False)
    SI.shutdown_self_inspector()
    assert SI._self_inspector is None, "调用后单例没被置空（口子形同虚设）"


def test_08_product_source_untouched_by_this_knife():
    """★本刀只改测试件：`self_inspector.py` 里不许出现 375 刀12 的痕迹。

    反过来说：如果这里红了，说明有人为了修测试去动生产定位逻辑 —— 那是
    把「判据错」当成「被测对象错」，方向反了。
    """
    with open(os.path.join(_ROOT, "nucleus", "self_inspector.py"),
                 encoding="utf-8") as f:
        _s = f.read()
    assert "375刀12" not in _s and "375 刀12" not in _s, "生产件被本刀改动了"


# ============================ ⑤ 历史账不改写
def test_09_372_archive_keeps_original_record():
    """372 那笔账仍是 open（历史快照不改写，铁律268）；收口另记在 375 证据件里。"""
    import json
    with open(_372_EVID, encoding="utf-8") as f:
        _ev = json.load(f)
    _open = [r for r in _ev["rows"] if r.get("status") == "open"]
    assert len(_open) == 1, f"372 账的 open 条数被动过：{len(_open)}"
    assert "test_33" in _open[0]["nodeid"], _open[0]["nodeid"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
