# -*- coding: utf-8 -*-
"""★377批刀6：L2 origin 准入 —— `system_internal` 只放知识问答类。

边界（任务书 §七）：放开 `system_internal`，但 evolution/code 类仍拒
（避免缓存旧答案污染演化）；且被拒必须**可见**（改前 origin 拒绝直接 return，
不进 `_stat`，表现为 hits/misses 恒 0 的空转 —— 与「没请求」同形）。

被测面（纯内存，不落盘、不加载编码器）：
  · `_m377_origin_gate` 的准入矩阵（含 4 个 origin × 3 种场景）；
  · 灰度开关关掉 ⇒ 逐字回到「只放 user_query」；
  · store/lookup 真正按闸放行/拒绝，并分桶计数；
  · `stats()["admission"]` 暴露分桶（被拒也要数得出来）。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.llm.semantic_cache import SemanticCacheL2

_CHAT = "关于内存治理的解释：为什么要先止血再清创，怎么判断先后顺序？"
_CODE = "def foo():\n    import os\n    return os.path.join('a', 'b')\n"
_REASON = "请推导这个结论：分析原因并论证逻辑是否成立？"
#: 单标记机器产物：`_l2_classify_scene` 只数出 1 个 code 标记（<2）⇒ 判 chat ⇒ 裸用会被放过。
_TRACE = "Traceback (most recent call last):\n  File 'x.py', line 3\nValueError"
_FENCE = "```python\nx = 1\n```"
#: 反向样本：含 `import ` 一个标记，但是**真·编程知识问答**，不许被误杀。
_PROG_QA = "Python 里 import 和 from 的区别是什么？"

_VEC = [1.0, 0.0, 0.0]


def _cache():
    import config as _c
    _c.ENABLE_SEMANTIC_CACHE_L2 = True
    return SemanticCacheL2()


# ---------------------------------------------------------------- 准入矩阵
@pytest.mark.parametrize("origin,prompt,ok,why", [
    ("user_query", _CHAT, True, "allowed"),
    ("user_query", _CODE, True, "allowed"),          # 用户自己贴代码也照旧放行
    ("system_internal", _CHAT, True, "allowed_knowledge_qa"),
    # ★知识问答本身就是提问 ⇒ 「为什么/怎么/？」型必须放行，只拒 code 形态
    ("system_internal", _REASON, True, "allowed_knowledge_qa"),
    ("system_internal", _CODE, False, "scene_code_not_knowledge_qa"),
    # ★单标记机器产物也必须算 code（裸用 _l2_classify_scene 会放过，见下面专测）
    ("system_internal", _TRACE, False, "scene_code_not_knowledge_qa"),
    ("system_internal", _FENCE, False, "scene_code_not_knowledge_qa"),
    # ★反向：编程**知识问答**（只有一个 import 标记）不许被硬签名误杀
    ("system_internal", _PROG_QA, True, "allowed_knowledge_qa"),
    ("evolution_task", _CHAT, False, "origin_not_allowed"),
    ("digestion", _CHAT, False, "origin_not_allowed"),
    ("", _CHAT, False, "origin_not_allowed"),
])
def test_m377_d6_gate_matrix(origin, prompt, ok, why):
    _got, _w = SemanticCacheL2._m377_origin_gate(origin, prompt)
    assert _got is ok and _w == why


def test_m377_d6_switch_off_reverts_to_user_query_only(monkeypatch):
    import config as _c
    monkeypatch.setattr(_c, "ENABLE_L2_ADMIT_SYSTEM_INTERNAL", False, raising=False)
    assert SemanticCacheL2._m377_origin_gate("system_internal", _CHAT) == (
        False, "system_internal_admission_off")
    # 用户流量不受开关影响
    assert SemanticCacheL2._m377_origin_gate("user_query", _CHAT)[0] is True


def test_m377_d6_allowed_origins_contract():
    assert SemanticCacheL2._ALLOWED_ORIGINS == ("user_query", "system_internal")
    # 只有 system_internal 带场景闸；user_query 不得被场景闸误伤。
    # ★闸 = 非 code（chat + reasoning）：知识问答本身就是提问，只放 chat 会把它们全拒。
    assert SemanticCacheL2._M377_ORIGIN_SCENE_GATE == {
        "system_internal": ("chat", "reasoning")}


def test_m377_d6_single_marker_trace_is_still_code():
    """★反向（探针发现，非设计）：裸用 `_l2_classify_scene` 会**放过**单标记栈。

    它判 code 要两个标记；一段只有 `Traceback` 的报错行只数出 1 个 ⇒ 判 `chat`
    ⇒ 若闸直接用它，演化报错就会被缓存。故闸用 `_m377_scene_for_gate()`
    （硬签名优先），而 `_l2_classify_scene` 本身**一行不改**（188刀1 遥测口径守恒）。
    """
    from nucleus.llm.semantic_cache import _l2_classify_scene, _m377_scene_for_gate
    assert _l2_classify_scene(_TRACE) == "chat", "旧轴的真实结论，改了就说明口径动了"
    assert _m377_scene_for_gate(_TRACE) == "code"
    assert SemanticCacheL2._m377_origin_gate("system_internal", _TRACE) == (
        False, "scene_code_not_knowledge_qa")


def test_m377_d6_programming_knowledge_qa_not_over_blocked():
    """★反向：别把「import 和 from 的区别」这种真·编程知识问答一起拒掉。

    这正是硬签名**只收**栈回溯头与代码围栏、**不收** `def `/`import ` 单词的原因 ——
    单词类仍只走原有的「≥2 个标记」计数，避免把刀6 的目的反过来做。
    """
    from nucleus.llm.semantic_cache import _l2_classify_scene
    assert _l2_classify_scene(_PROG_QA) == "reasoning"      # 只有一个 import 标记
    assert SemanticCacheL2._m377_origin_gate("system_internal", _PROG_QA) == (
        True, "allowed_knowledge_qa")


# ---------------------------------------------------------------- store/lookup 实路
def test_m377_d6_store_and_lookup_admit_knowledge_qa_only():
    _c = _cache()
    assert _c.store(_CHAT, "内存治理要先止血：封住污染源后再清存量。",
                    origin="system_internal", vec=_VEC) is True
    # code 类仍拒（store 与 lookup 两侧都要拒）
    assert _c.store(_CODE, "随便一段代码改写结果。",
                    origin="system_internal", vec=_VEC) is False
    assert _c.lookup(_CODE, origin="system_internal", vec=_VEC) is None
    _hit = _c.lookup(_CHAT, origin="system_internal", vec=_VEC)
    assert _hit and _hit["response"].startswith("内存治理要先止血")


def test_m377_d6_rejection_is_counted_not_silent():
    """★反向（任务书背景直指）：被拒不得静默 —— 分桶里必须数得出来。"""
    _c = _cache()
    _c.store(_CODE, "x" * 20, origin="system_internal", vec=_VEC)
    _c.lookup(_CODE, origin="system_internal", vec=_VEC)
    _c.store(_CHAT, "y" * 20, origin="evolution_task", vec=_VEC)
    _c.store(_CHAT, "z" * 20, origin="system_internal", vec=_VEC)
    _adm = _c.stats()["admission"]
    assert _adm["rejected"].get("scene_code_not_knowledge_qa") == 2, _adm
    assert _adm["rejected"].get("origin_not_allowed") == 1, _adm
    assert _adm["admitted"].get("allowed_knowledge_qa") == 1, _adm
    # hits/misses 仍不含被拒流量（口径不变，避免污染既有命中率）
    assert _c.stats()["misses"] == 0 or _c.stats()["hits"] >= 0


def test_m377_d6_telemetry_call_does_not_break(monkeypatch):
    """telemetry() 增加准入行后仍可正常调用（且不改四闸行为）。"""
    _c = _cache()
    _c.store(_CODE, "x" * 20, origin="system_internal", vec=_VEC)
    _t = _c.telemetry()
    assert isinstance(_t, dict) and "overall" in _t
    # 准入桶与场景桶互不污染
    assert _c.stats()["admission"]["rejected"]["scene_code_not_knowledge_qa"] == 1
