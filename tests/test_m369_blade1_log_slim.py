# -*- coding: utf-8 -*-
"""369 刀1（E9 剩 9 族日志瘦身第一批）定向单测。

覆盖任务书要求的 5 例：
  ① 每族分级处置后级别/聚合行为可复现（按族参数化，AST 判定）；
  ② 真事件族级别维持不变；
  ③ 聚合行含次数/命中/跳过汇总字段；
  ④ 异常分支仍单列可见；
  ⑤ 既有 JsonRepair 族处置不被破坏。
外加 3 例运行时验证（判据件语义 / telemetry 聚合 / 分诊未到时降级）。

★判定一律走 AST（★227）：文本断言会被修复注释命中（本项目已踩过两次）。
不依赖框架运行（曈曈停机态亦可跑），不写生产 data/。
"""
import ast
import os
import sys
import threading
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.log_state import changed, reset, sig, suppressed  # noqa: E402

# --------------------------------------------------------------------- AST 工具


def _src(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), "rb") as f:
        return f.read().decode("utf-8-sig")


def _const_text(node: ast.Call) -> str:
    """把一次调用里所有字符串常量拼起来（含 f-string 的常量片段）。"""
    parts = []
    for a in node.args:
        for sub in ast.walk(a):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                parts.append(sub.value)
    return "".join(parts)


def _log_calls(rel: str):
    """返回 [(lineno, level, text)] —— 该文件内所有 `logger.debug/info/...` 与 `self._log(LogLevel.X, ...)` 调用。"""
    tree = ast.parse(_src(rel))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        lvl = None
        if isinstance(fn, ast.Attribute):
            if fn.attr in ("debug", "info", "warning", "error", "critical"):
                lvl = fn.attr
            elif fn.attr == "_log" and node.args:
                a0 = node.args[0]
                if isinstance(a0, ast.Attribute):
                    lvl = a0.attr.lower()
        if lvl is None:
            continue
        out.append((node.lineno, lvl, _const_text(node)))
    return out


def _calls_named(rel: str, name: str):
    """返回所有 `name(...)` 调用的 lineno 列表（含 from-import 别名后的名字）。"""
    tree = ast.parse(_src(rel))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]


def _attr_names(rel: str):
    """返回该文件所有属性名集合（用于判定「变化判据字段」是否落地）。"""
    tree = ast.parse(_src(rel))
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def _pick(rel: str, marker: str, level: str | None = None):
    return [c for c in _log_calls(rel) if marker in c[2] and (level is None or c[1] == level)]


# --------------------------------------------------------------------- ① 九族分级处置可复现

FAMILIES = [
    # (族, 文件, 特征串, 期望级别, 期望判据落地形式)
    ("F1-time.tick-Heart", "organs/body/PulseHeart.py", "[time.tick]", "debug", None),
    ("F1-time.tick-Lung", "organs/body/PulseLung.py", "[time.tick]", "debug", None),
    ("F1-time.tick-Cortex", "organs/brain/PulseCortex.py", "[time.tick]", "debug", None),
    ("F2-cache", "nucleus/llm/semantic_cache.py", "[semantic_cache_l2] telemetry 汇总", "info", "_m369_changed"),
    ("F3-dedup", "nucleus/field/request_dedup_observe.py", "[request_dedup_observe] snapshot", "info", "_m369_changed"),
    ("F4-motivation", "organs/core/PulseMotivationCycle.py", "动机循环#", "info", "_m369_changed"),
    ("F5-event-pattern", "nucleus/field/InfoField.py", "事件模式发现", "info", "_m369_changed"),
    ("F6-distill-liver", "organs/body/PulseLiver.py", "总量压缩跳过:", "debug", "_last_code_learning_l1_skip"),
    ("F6-distill-hub", "nucleus/mnemosyne/verification_learning_hub.py", "[终身学习引擎] 当前累计", "info",
     "_m369_last_entry_total"),
    ("F7-daily-skip", "nucleus/self_awareness/DailyScheduler.py", "已生成报告，跳过本次", "debug", "_m369_changed"),
    ("F8-time-refresh", "organs/brain/PulseCortex.py", "[345时] 时间节点刷新(就地)", "debug", None),
    ("F9-dispatch-not-due", "nucleus/evolution/patch_execution_dispatch.py", "钩子未到时", "debug", None),
]


@pytest.mark.parametrize("fam,rel,marker,want_level,want_guard", FAMILIES)
def test_family_disposition_reproducible(fam, rel, marker, want_level, want_guard):
    """① 每族：特征串仍在（不是删日志点），且现行级别 == 判定后的级别，判据已落地。"""
    hits = _pick(rel, marker)
    assert hits, "族 %s 的日志点被删掉了（本刀禁止删日志点）：%s :: %s" % (fam, rel, marker)
    # 分级处置：该族的重复输出行必须落在期望级别
    assert any(c[1] == want_level for c in hits), \
        "族 %s 现行级别不含 %s：实际=%s" % (fam, want_level, [c[1] for c in hits])
    if want_guard:
        if want_guard.startswith("_m369_changed"):
            assert _calls_named(rel, "_m369_changed"), "族 %s 缺变化判据调用" % fam
        else:
            assert want_guard in _attr_names(rel), "族 %s 缺变化判据字段 %s" % (fam, want_guard)


def test_no_family_uses_plain_info_without_guard():
    """① 附：控制流族不得无任何判据地留在 INFO（禁止"一把梭降级"的反向失守）。"""
    for fam, rel, marker, want_level, _g in FAMILIES:
        hits = _pick(rel, marker, level="info")
        if want_level != "info":
            continue
        # INFO 族必须带判据（changed 或 值变化字段）
        guards = _calls_named(rel, "_m369_changed") or [
            a for a in _attr_names(rel) if a.startswith("_m369_") or a.startswith("_last_")
        ]
        assert guards, "族 %s 留在 INFO 却没有任何变化判据 ⇒ 仍在刷屏" % fam
        assert hits, "族 %s 连 INFO 留痕点都没有 ⇒ 降级过头（留痕消失）" % fam


# --------------------------------------------------------------------- ② 真事件族级别维持不变

REAL_EVENTS = [
    ("分诊挂载起算", "nucleus/evolution/patch_execution_dispatch.py", "挂载起算", "info"),
    ("时间节点建立", "organs/brain/PulseCortex.py", "时间节点建立", "info"),
    ("动机循环异常", "organs/core/PulseMotivationCycle.py", "动机循环异常", "error"),
    ("调度循环异常", "nucleus/self_awareness/DailyScheduler.py", "调度循环异常", "warning"),
    ("跨路径压缩失败降级", "organs/body/PulseLiver.py", "跨路径压缩失败", "warning"),
]


@pytest.mark.parametrize("name,rel,marker,want_level", REAL_EVENTS)
def test_real_event_families_unchanged(name, rel, marker, want_level):
    """② 真事件（异常/降级/一次性建立）维持原级别，不得被"瘦身"误降。"""
    hits = _pick(rel, marker, level=want_level)
    assert hits, "真事件族「%s」的 %s 级留痕不见了：%s :: %s" % (name, want_level, rel, marker)


def test_event_pattern_info_branch_kept():
    """② 事件模式：模式数变化仍走 INFO（重复发现才降 DEBUG），两条分支都在。"""
    hits = _log_calls("nucleus/field/InfoField.py")
    infos = [c for c in hits if "事件模式发现" in c[2] and c[1] == "info"]
    debgs = [c for c in hits if "事件模式发现" in c[2] and c[1] == "debug"]
    assert infos, "模式数变化时的 INFO 分支被误删"
    assert debgs, "模式数无变化时的 DEBUG 分支缺失"


# --------------------------------------------------------------------- ③ 聚合行字段

def test_telemetry_aggregate_line_fields():
    """③ cache 族聚合行含 scenes/hits/misses/neg/stores/明细 汇总字段。"""
    hits = _pick("nucleus/llm/semantic_cache.py", "[semantic_cache_l2] telemetry 汇总", level="info")
    assert len(hits) == 1
    fmt = hits[0][2]
    for field in ("scenes=%d", "hits=%d", "misses=%d", "neg=%d", "stores=%d", "明细=%s"):
        assert field in fmt, "聚合行缺汇总字段 %s：%s" % (field, fmt)


def test_dedup_snapshot_aggregate_suffix():
    """③ 去重快照聚合行含「无变化×N次」计数字段。"""
    hits = _pick("nucleus/field/request_dedup_observe.py", "[request_dedup_observe] snapshot", level="info")
    assert len(hits) == 1
    assert "无变化×%d次" in hits[0][2], "快照行缺抑制计数字段：%s" % hits[0][2]


# --------------------------------------------------------------------- ④ 异常分支仍单列可见

def test_exception_paths_still_visible():
    """④ 降噪不得吞异常：silent_exc / warning / error 分支仍在原位。"""
    # cache 族：日志分支异常仍由 silent_exc 记账（指纹不变）
    src = _src("nucleus/llm/semantic_cache.py")
    assert "where=\"nucleus.llm.semantic_cache::SemanticCacheL2.telemetry.log\"" in src, \
        "telemetry 日志分支的 silent_exc 留痕点不见了"
    # 动机族：except 分支仍 ERROR
    assert _pick("organs/core/PulseMotivationCycle.py", "动机循环异常", level="error")
    # 调度族：循环异常仍 WARNING（且线程继续）
    assert _pick("nucleus/self_awareness/DailyScheduler.py", "调度循环异常", level="warning")


def test_telemetry_returns_value_unchanged():
    """④ 附：早退分支仍返回遥测快照（返回值语义不被降噪改动）。"""
    from nucleus.llm.semantic_cache import SemanticCacheL2

    obj = SemanticCacheL2.__new__(SemanticCacheL2)  # 不调 __init__：零 IO、不碰 data/
    obj._telemetry = {"overall": {"hits": 0, "misses": 2, "neg": 0},
                      "code": {"hits": 1, "misses": 1, "neg": 0}}
    obj._stat = {"stores": 3}
    obj._lock = threading.Lock()
    reset("semantic_cache_l2.telemetry")
    out = obj.telemetry()
    assert out["overall"]["hit_rate"] == 0.0
    assert out["code"]["hit_rate"] == 0.5
    assert set(out) == {"overall", "code"}


# --------------------------------------------------------------------- ⑤ JsonRepair 族未被破坏

def test_jsonrepair_family_not_regressed():
    """⑤ 367 已收口的 JsonRepair 族（debug 留痕 + silent_exc debug）仍在位。"""
    hits = _pick("nucleus/parsing/JsonRepair.py", "[JSON定位刀] 修复失败", level="debug")
    assert hits, "JsonRepair 族的 debug 留痕点被破坏了"
    src = _src("nucleus/parsing/JsonRepair.py")
    assert 'silent_exc(e, "JsonRepair:261:JSON解析失败", level="debug")' in src, \
        "JsonRepair:261 候选不合法的 debug 级 silent_exc 被改动"


# --------------------------------------------------------------------- 运行时验证

@pytest.fixture(autouse=True)
def _clean_state():
    reset()
    yield
    reset()


def test_log_state_changed_semantics():
    """判据件语义：首次放行 → 相同抑制 → 变化放行；抑制计数可回读。"""
    assert changed("t1", sig({"a": 1, "b": 2})) is True      # 首次：留基线
    assert changed("t1", sig({"b": 2, "a": 1})) is False     # 内容等价（键序无关）
    assert suppressed("t1") == 1
    assert changed("t1", sig({"a": 1, "b": 3})) is True      # 变化：放行
    assert suppressed("t1") == 0
    # 嵌套与浮点
    assert changed("t2", sig([{"x": [1, 2]}, {"y": (3, 4)}])) is True
    assert changed("t2", sig([{"x": [1, 2]}, {"y": (3, 4)}])) is False


def test_log_state_tag_cap_does_not_leak():
    """tag 上限：动态 tag 打穿内存时按插入顺序淘汰，不抛异常。"""
    for i in range(2100):  # > _MAX_TAGS(2048)
        changed("cap-%d" % i, i)
    from nucleus import log_state

    assert len(log_state._STATE) <= log_state._MAX_TAGS
    assert changed("cap-2099", 2099) is False  # 最新 tag 仍在记忆内


def test_dispatch_not_due_downgraded_runtime(monkeypatch):
    """F9 运行时：起算（真事件）仍 INFO；未到时（控制流）走 debug 且不丢返回值语义。"""
    from nucleus.evolution import patch_execution_dispatch as ped

    calls = []
    monkeypatch.setattr(ped._module_logger, "info",
                        lambda *a, **k: calls.append(("info", a[0] if a else "")))
    monkeypatch.setattr(ped._module_logger, "debug",
                        lambda *a, **k: calls.append(("debug", a[0] if a else "")))

    ped._last_dispatch_ts = None
    r1 = ped.dispatch_on_tick()
    assert r1["status"] == "armed"
    assert any(lv == "info" and "挂载起算" in t for lv, t in calls), "真事件（挂载起算）应仍 INFO"

    calls.clear()
    ped._last_dispatch_ts = time.time()  # 刚起算 ⇒ 必然未到时
    r2 = ped.dispatch_on_tick()
    assert r2["status"] == "skipped_not_due"
    assert r2["interval_sec"] > 0
    assert any(lv == "debug" and "钩子未到时" in t for lv, t in calls), "未到时应走 debug 留痕"
    assert not any(lv == "info" for lv, _ in calls), "未到时不得再刷 INFO"


def test_dedup_snapshot_suppresses_repeat(monkeypatch):
    """F3 运行时：读数不变 ⇒ 不重复落 INFO；读数一变 ⇒ 立刻恢复并附抑制计数。"""
    from nucleus.field import request_dedup_observe as obs

    calls = []
    monkeypatch.setattr(obs._log, "info", lambda *a, **k: calls.append(a[0] if a else ""))
    obs.reset()

    obs.log_snapshot()
    assert len(calls) == 1, "首次应留基线"
    obs.log_snapshot()
    obs.log_snapshot()
    assert len(calls) == 1, "读数不变不应重复落 INFO（实测同文本重复 12 条/h）"

    obs.observe_request("u", "p1")  # 制造读数变化
    obs.log_snapshot()
    assert len(calls) == 2, "读数变化应恢复留痕"
    assert "无变化×%d次" in calls[0], "聚合行应带抑制计数字段"
