# -*- coding: utf-8 -*-
"""★401批刀1（P1 / L5）：快照补四块 + 阈值归一 判据单测。

守三件事（都跟"看得见"有关，不跟措辞有关）：
  ① 四块（`memory` / `gc` / `threads` / `handles`）真进了快照，且取不到时是 `None` 不是 `0`；
  ② `_collect_memory_pressure()` 在**一次快照里只调一次** —— 它有副作用（记峰值 / 跑三闸 /
     可能真触发 GC），补字段若顺手再调一次，等于给活体多插一刀（用 AST 守，不认措辞）；
  ③ 阈值只有一个定义源：`config.RUNTIME_PARAMS` 派生出的两个模块常量，
     且 `PulseMetricsCollector.py` 里**不再有** 6144 / 8192 字面量（第二源已拔）。

为什么不测"快照每 300s 更新"：那是**活体运行期**判据，离线单测拿不到，
只能由重启后核验（`data/monitor/health_snapshot.json` 的 mtime 差）。本文件不伪造它。
"""
from __future__ import annotations

import ast
import gc
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config as _cfg  # noqa: E402
from organs.core.PulseMetricsCollector import PulseMetricsCollector  # noqa: E402

_PMC_PATH = os.path.join(_ROOT, "organs", "core", "PulseMetricsCollector.py")

#: 裸实例构快照所需的缓存属性（都是 `getattr(self, ...)` 读的旁路块）
_STUB_ATTRS = (
    "_cached_organ_stats", "_cached_heartbeat", "_cached_current_user",
    "_dream_count", "_dream_last_topic", "_reflection_count",
    "_reflection_last_domain", "_liver_compress_count", "_liver_fuse_count",
    "_instinct_upgrade_count", "_headless_stats", "_cached_social_emotions",
    "_cached_top_interests", "_cached_life_state", "_cached_emotion_state",
)


def _bare_collector():
    """不经 `__init__` 的实例：本刀只测读数装配，不起器官总线。"""
    _c = object.__new__(PulseMetricsCollector)
    _c._beat_count = 0
    _c.info_field = None
    for _a in _STUB_ATTRS:
        setattr(_c, _a, {})
    return _c


def _fixed_mp() -> dict:
    """确定的 memory_pressure 读数（含增长速率与 GC 触发标志）。"""
    return {
        "process_rss_mb": 1234.5,
        "system_percent": 62.0,
        "gc_triggered": True,
        "growth_alarm": {"growth_mb_per_min": 7.5},
    }


def _calls_in(func_name: str, attr: str) -> list:
    """某函数体内 `self.<attr>(...)` 的调用次数（AST，不认注释/docstring）。"""
    _tree = ast.parse(open(_PMC_PATH, encoding="utf-8").read())
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.FunctionDef) and _n.name == func_name:
            return [x for x in ast.walk(_n)
                    if isinstance(x, ast.Call)
                    and isinstance(x.func, ast.Attribute)
                    and x.func.attr == attr
                    and isinstance(x.func.value, ast.Name)
                    and x.func.value.id == "self"]
    return []


# ---------------------------------------------------------------- ① 四块进快照
def test_01_four_blocks_in_snapshot(monkeypatch):
    """四块都在，且子键齐全（缺键 = 消费方读到 KeyError 或猜 0）。"""
    monkeypatch.setattr(_cfg, "ENABLE_M401_SNAPSHOT_EXTRA_FIELDS", True)
    _c = _bare_collector()
    monkeypatch.setattr(_c, "_collect_memory_pressure", _fixed_mp)

    _s = _c._build_observability_snapshot()

    for _k in ("memory", "gc", "threads", "handles"):
        assert _k in _s, "快照缺块：%s" % _k
    assert set(_s["memory"]) >= {"process_rss_mb", "system_percent", "growth_mb_per_min"}
    assert set(_s["gc"]) >= {"count", "last_gc_at", "auto_gc_triggered"}
    assert "n" in _s["threads"] and "n" in _s["handles"]
    # 复用同一次采样：memory 三值与 memory_pressure 一致
    assert _s["memory"]["process_rss_mb"] == 1234.5
    assert _s["memory"]["system_percent"] == 62.0
    assert _s["memory"]["growth_mb_per_min"] == 7.5
    assert _s["memory"]["source"] == "memory_pressure"
    # GC 触发标志与告警链单轨（不另起判据）
    assert _s["gc"]["auto_gc_triggered"] is True
    # 线程数就是解释器口径，不许"估"
    import threading
    assert _s["threads"]["n"] == threading.active_count()


def test_02_switch_off_restores_old_snapshot(monkeypatch):
    """灰度开关关闭 ⇒ 四块都不写（回到 401 之前的样子，零行为变化）。"""
    monkeypatch.setattr(_cfg, "ENABLE_M401_SNAPSHOT_EXTRA_FIELDS", False)
    _c = _bare_collector()
    monkeypatch.setattr(_c, "_collect_memory_pressure", _fixed_mp)

    _s = _c._build_observability_snapshot()
    for _k in ("memory", "gc", "threads", "handles"):
        assert _k not in _s, "开关关了还在写 %s" % _k
    assert "memory_pressure" in _s, "开关不该连既有读数一起关掉"


# ------------------------------------------------- ② 一次采集多处复用（AST）
def test_03_collect_memory_pressure_called_once():
    """AST：`_build_observability_snapshot` 里 `self._collect_memory_pressure()` 恰好 1 次。

    为什么钉 1 次而不是"有复用"：该函数有副作用（记峰值 / 三闸 / 可能触发 GC），
    补字段的人很容易顺手 `self._collect_memory_pressure()` 再取一次 ——
    那样快照里两个块的读数**不同源**，还会让 GC 状态被自己的观测扰动。
    """
    _calls = _calls_in("_build_observability_snapshot", "_collect_memory_pressure")
    assert len(_calls) == 1, "采集调用 %d 次（应为 1 次，多处复用同一读数）" % len(_calls)


# ------------------------------------------------------------ ③ 取不到给 None
def test_04_none_not_zero_when_unavailable(monkeypatch):
    """没测到 ⇒ `None`。`0 MB` / `0 个句柄` 是"真的测到了 0"，不许拿来冒充"没测到"。"""
    _c = _bare_collector()
    _empty = _c._m401_memory_block({})
    assert _empty["process_rss_mb"] is None
    assert _empty["system_percent"] is None
    assert _empty["growth_mb_per_min"] is None

    def _boom(*_a, **_k):
        raise RuntimeError("psutil 不可用（模拟）")

    monkeypatch.setattr("psutil.Process", _boom)
    assert _c._m401_handle_count() is None, "句柄取不到应为 None"


def test_05_gc_block_shape():
    """gc 块：`count` = 各代 collections 之和（解释器启动以来，单调）；`last_gc_at` 可为 None。"""
    _c = _bare_collector()
    _b = _c._m401_gc_block({"gc_triggered": False})
    _st = gc.get_stats() or []
    assert _b["count"] == sum(int((x or {}).get("collections", 0) or 0) for x in _st)
    assert _b["generations"] == len(_st)
    assert _b["auto_gc_triggered"] is False
    assert _b["last_gc_at"] is None or isinstance(_b["last_gc_at"], float)


def test_06_gc_callback_records_stop_only():
    """`_m401_gc_callback` 只认 phase=="stop"（start 时刻不是"最后回收时刻"）。"""
    from organs.core import PulseMetricsCollector as _m

    _m._M401_GC_LAST["at"] = None
    _m._m401_gc_callback("start", {"generation": 2})
    assert _m._M401_GC_LAST["at"] is None, "start 阶段被误记成回收完成时刻"
    _m._m401_gc_callback("stop", {"generation": 1})
    assert isinstance(_m._M401_GC_LAST["at"], float)
    assert _m._M401_GC_LAST["gen"] == 1


# --------------------------------------------------------- ④ 阈值只有单一源
def test_07_thresholds_derive_from_runtime_params():
    """两个模块常量必须由 `RUNTIME_PARAMS` 派生（改 override 才可能生效）。"""
    _rp = _cfg.RUNTIME_PARAMS
    assert float(_cfg.MEMORY_AUTO_GC_RSS_MB) == float(_rp["memory_auto_gc_rss_mb"])
    assert float(_cfg.HIGH_LOAD_PROCESS_RSS_MB) == float(_rp["memory_high_load_rss_mb"])
    # 热重载有键交集过滤 ⇒ 键必须真在表里，否则 override 被静默丢弃
    assert "memory_high_load_rss_mb" in _rp
    assert "memory_auto_gc_rss_mb" in _rp


def test_08_no_second_source_literals():
    """AST：`PulseMetricsCollector.py` 里不得再出现 6144 / 8192 字面量。

    票面背景是"两个阈值各写一处"，归一的判据就是**第二源已拔**；
    用数字常量扫，而不是 grep 文本 —— 注释里写着这两个数不算违规。
    """
    _tree = ast.parse(open(_PMC_PATH, encoding="utf-8").read())
    _nums = {x.value for x in ast.walk(_tree)
             if isinstance(x, ast.Constant)
             and isinstance(x.value, (int, float))
             and not isinstance(x.value, bool)}
    for _v in (6144, 8192):
        assert _v not in _nums, "硬编码阈值 %s 仍在（应走 config.RUNTIME_PARAMS）" % _v


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
