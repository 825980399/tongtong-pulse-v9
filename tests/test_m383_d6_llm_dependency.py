# -*- coding: utf-8 -*-
"""383 刀6：LLM 依赖度「上游 / 投递」分开计数 + 状态分档。

## 这一刀到底在修什么

票面说 `observe_semantic_decision()` 全库零生产调用、要求在「本地规则 vs 外部 LLM」
分叉处补接线。T0 实测**不是这样**：

  · 它**有生产调用点**（`PulseSemanticComprehension.py:291`，178 批刀4 接的）；
  · 埋点开关 `ENABLE_SEMANTIC_CACHE_OBSERVE` 默认 True；
  · 器官已启动（live 18:24 注册到 pid7920）；
  · 但 live `llm_total=0` **恒定**，而同模块 `delegate_browser` 20→27 在涨。

真因是**上游没发事件**：`Event.SEMANTIC_CLASSIFY` 的两处发布点
（`PulseCortex.py:899/1167`）都在**用户对话路径**上，pid7920 自启动以来没有对话输入。
历史证据：`语义理解: intent=…` 全库只有 1 条，来自 pid=26804（老进程 18:05:59）。

⇒ 按票面补一根线，只会得到重复调用。真正缺的是**可观测性**：
   `llm_total=0` 与「埋点坏了」长得一模一样，必须在上游侧留痕才能分开。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.field import external_dependency_observe as o  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    o.reset()
    yield
    o.reset()


# ------------------------------------------------------------------ 六态
def test_m383_d6_no_upstream_when_no_dialog():
    """★当前 live 的真实状态：没对话 ⇒ upstream=0 ⇒ no_upstream（**正常**，不是缺陷）。"""
    s = o.snapshot()
    assert s["semantic_upstream"] == 0 and s["llm_total"] == 0
    assert s["llm_state"] == "no_upstream"


def test_m383_d6_pending_before_async_delivery():
    """★反控：上游刚发 1 次、埋点还没到 ⇒ pending，**不许判成丢失**。

    这是本刀最容易写错的地方：EventBus 可能异步投递，单次差值不足以说明丢失。
    """
    o.note_semantic_upstream()
    s = o.snapshot()
    assert s["llm_state"] == "pending"
    assert s["semantic_undelivered"] == 1


def test_m383_d6_upstream_no_delivery_is_the_real_alarm():
    """上游累计发了 ≥3 次、埋点一次都没收到 ⇒ 这才是真缺陷（投递断了）。"""
    for _ in range(3):
        o.note_semantic_upstream()
    s = o.snapshot()
    assert s["llm_state"] == "upstream_no_delivery"
    assert s["semantic_undelivered"] == 3


def test_m383_d6_local_only_when_all_local():
    """收到了，但全判「本地可处理」⇒ local_only（本地规则够用，与 no_upstream 不同）。"""
    o.observe_semantic_decision(False)
    o.observe_semantic_decision(False)
    s = o.snapshot()
    assert s["llm_state"] == "local_only"
    assert s["llm_used_false"] == 2 and s["llm_total"] == 2


def test_m383_d6_partial_loss():
    """有丢失但仍在收（上游−收到 ≥3）⇒ partial_loss。"""
    for _ in range(5):
        o.note_semantic_upstream()
    o.observe_semantic_decision(True)
    s = o.snapshot()
    assert s["llm_state"] == "partial_loss"
    assert s["semantic_undelivered"] == 4


def test_m383_d6_ok_when_llm_engaged():
    """★票面验收「接线后 llm_total > 0」的等价证明：上游发 + 埋点收 ⇒ ok。"""
    for _ in range(3):
        o.note_semantic_upstream()
    o.observe_semantic_decision(True)
    s = o.snapshot()
    assert s["llm_total"] == 1
    assert s["llm_state"] == "ok"


# --------------------------------------------------------------- reset 覆盖
def test_m383_d6_reset_clears_new_counter():
    """reset() 走 `for _k in _stats` ⇒ 新键自动被覆盖（防漏清导致跨用例串味）。"""
    o.note_semantic_upstream()
    o.observe_semantic_decision(True)
    o.reset()
    s = o.snapshot()
    assert s["semantic_upstream"] == 0 and s["llm_total"] == 0
    assert s["llm_state"] == "no_upstream"


# ------------------------------------------------------- 开关关掉就不计数
def test_m383_d6_switch_off_stops_counting(monkeypatch):
    """灰度：关掉埋点开关 ⇒ 上游探针也不计（与其它 observe_* 行为一致）。"""
    monkeypatch.setattr(config, "ENABLE_SEMANTIC_CACHE_OBSERVE", False, raising=False)
    o.note_semantic_upstream()
    o.observe_semantic_decision(True)
    s = o.snapshot()
    assert s["semantic_upstream"] == 0 and s["llm_total"] == 0


# ------------------------------------------------- 发布侧真的挂了探针（两处）
def test_m383_d6_both_publish_sites_are_instrumented():
    """★核心接线守卫：SEMANTIC_CLASSIFY 的**两处**发布点前面都必须有探针。

    票面担心「埋点没接」，本刀担心的是「接了一处漏了另一处」——
    排队派发路径（_dispatch_queued_dialog）很容易被漏掉。
    """
    import io as _io
    src = _io.open(os.path.join(ROOT, "organs/brain/PulseCortex.py"),
                   encoding="utf-8").read()
    # 发布点数与探针调用数必须相等（各 2）
    assert src.count("self._emit(Event.SEMANTIC_CLASSIFY") == 2
    assert src.count("\n            _m383_note_semantic_upstream()") == 2


def test_m383_d6_publish_probe_never_raises(monkeypatch):
    """发布侧探针即使内部炸了，也不许影响对话主流程。"""
    import organs.brain.PulseCortex as pc

    def _boom():
        raise RuntimeError("模拟埋点故障")

    monkeypatch.setattr(o, "note_semantic_upstream", _boom)
    pc._m383_note_semantic_upstream()  # 不抛即为通过
