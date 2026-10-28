# -*- coding: utf-8 -*-
"""★377批刀5：占位符素材入口拦 ＋ 路径级熔断指纹口径。

被测面（全部纯本地，不写生产 `data/`、不跑融合全链）：
  · `_m377_is_placeholder` 与 `PlaceholderSanitizer.contains_placeholder_literal`
    **同源不漂移**（四类：`[器官别名]` / `以 关联知识：。 开头` / `[归纳升华]` / `[数据流·`）；
  · `_m377_drop_placeholder_nodes` 在**选材入口**整组剔除，开关关掉时零行为变化；
  · 全组皆占位符 ⇒ `_fuse_group` 返回 False（喂给 364A4 路径级熔断记账，不再每轮空转）；
  · 指纹口径改数「非占位符」节点 ⇒ 拦截生效后指纹必变，熔断才会允许重试一次。

★本批 T0 实测：存量占位符节点 **360 条**（裸父路径 `/自我理解/器官别名` 309 ＋
  `/综合`、`/自我理解/代码` 等 51），与任务书「存量 360 条」一致；
  降权/重写须停机窗执行（工具自带「框架在跑就拒落盘」守卫），本批**不执行改库**。
"""
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.knowledge.PlaceholderSanitizer import contains_placeholder_literal

#: 四类占位符 ＋ 一批**必须不命中**的正常前缀（防止判据被泛化成误伤）
_PH_TRUE = [
    "[器官别名] [器官别名] 内在世界是框架中的仿生器官。",
    "前缀文字里夹着 [器官别名] 字面量",
    "关联知识：。",
    "关联知识：。补充说明",
    "[归纳升华] 这段话是模板残留",
    "[数据流·PulseLiver] 管道说明",
]
_PH_FALSE = [
    "[综合] 这是一条正常的综合归纳内容，长度足够不触发别的判据。",
    "[代码关联·PulseLiver] 正常的关联标记",
    "[主动学习·主题] 正常的主动学习标记",
    "[自我理解·PulseLiver.is_placeholder_alias] 正常的方法级节点",
    "我了解到你在问内存治理的问题，简要回答如下。",
    "",
]


def _liver():
    import config as _c
    _c.KNOWLEDGE_FUSION_SKIP_PLACEHOLDER = True
    from organs.body.PulseLiver import PulseLiver
    return PulseLiver(organ_name="肝377d5")


def _node(value, trust=60.0, path="/自我理解/测试"):
    """最小节点替身：只带 `_fuse_group` / 指纹真正读到的四个属性。"""
    return SimpleNamespace(value=value, trust_score=trust, space_path=path,
                           importance="B", keywords=["测试"], node_id="n%08x" % id(value))


# ---------------------------------------------------------------- 判据同源
def test_m377_d5_predicate_is_same_source_as_sanitizer():
    """★反向：判据不许在本文件另写一份 —— 逐个样本与 PlaceholderSanitizer 对齐。"""
    _lv = _liver()
    for _s in _PH_TRUE:
        assert _lv._m377_is_placeholder(_s) is True, "漏判: %r" % (_s,)
        assert contains_placeholder_literal(_s) is True
    for _s in _PH_FALSE:
        assert _lv._m377_is_placeholder(_s) is False, "误伤: %r" % (_s,)
        assert contains_placeholder_literal(_s) is False


def test_m377_d5_predicate_survives_import_failure(monkeypatch):
    """判据取不到时**退化但留痕**：仍能拦 `[器官别名]`，且只告警一次（不静默缩小判据面）。"""
    import organs.body.PulseLiver as PL
    _lv = _liver()
    _msgs = []
    monkeypatch.setattr(_lv, "_log", lambda _lvl, _m, *a, **k: _msgs.append(str(_m)))
    # 让 "from ... import contains_placeholder_literal" 失败
    monkeypatch.setitem(sys.modules, "nucleus.knowledge.PlaceholderSanitizer", None)
    assert _lv._m377_is_placeholder("[器官别名] xx") is True
    assert _lv._m377_is_placeholder("[归纳升华] xx") is False, "退化态只认单字面量"
    assert _lv._m377_is_placeholder("[器官别名] yy") is True
    assert len([m for m in _msgs if "377刀5" in m]) == 1, "退化告警必须只打一次"
    assert PL is not None


# ---------------------------------------------------------------- 入口拦
def test_m377_d5_drop_removes_whole_group_entries():
    _lv = _liver()
    _nodes = [_node("[器官别名] A"), _node("真实内容 A"),
              _node("[归纳升华] B"), _node("真实内容 B"),
              _node("关联知识：。C")]
    _drop, _kept = _lv._m377_drop_placeholder_nodes("/自我理解/器官别名", _nodes)
    assert _drop == 3
    assert [n.value for n in _kept] == ["真实内容 A", "真实内容 B"], "保留顺序被改了"
    assert len(_kept) == 2, "剔除后仍计入素材数 ⇒ 阈值/关键词/『由N条融合而成』会虚高"


def test_m377_d5_switch_off_is_zero_change(monkeypatch):
    import config as _c
    _lv = _liver()
    monkeypatch.setattr(_c, "KNOWLEDGE_FUSION_SKIP_PLACEHOLDER", False, raising=False)
    _nodes = [_node("[器官别名] A"), _node("真实内容 A")]
    _drop, _kept = _lv._m377_drop_placeholder_nodes("/x", _nodes)
    assert (_drop, len(_kept)) == (0, 2), "开关关掉必须逐字回旧行为"


def test_m377_d5_all_placeholder_group_skips_fusion():
    """全组皆占位符 ⇒ `_fuse_group` 返回 False（不产 L3，且进熔断失败记账）。"""
    _lv = _liver()
    _nodes = [_node("[器官别名] A", trust=90.0), _node("[器官别名] B", trust=90.0)]
    assert _lv._fuse_group("/自我理解/器官别名", _nodes, _min_quality_nodes=1) is False


# ---------------------------------------------------------------- 指纹口径
def test_m377_d5_fingerprint_excludes_placeholders():
    _lv = _liver()
    _nodes = [_node("[器官别名] A", trust=90.0), _node("[器官别名] B", trust=90.0),
              _node("真实高质", trust=70.0), _node("真实低质", trust=10.0)]
    assert _lv._m377_fuse_fingerprint(_nodes) == (2, 1)
    # 旧口径（节点数 4 / 达标数 3）—— 正是「素材变了指纹没变」的来源
    assert _lv._m377_fuse_fingerprint(_nodes) != (4, 3)


def test_m377_d5_fingerprint_change_resets_backoff():
    """★反向：拦截生效 ⇒ 指纹变化 ⇒ 364A4 判定从「封顶等演化」变「允许重试一次」。

    这正是本刀补的那个缺口：素材实际变了，熔断必须能看见，否则改动永远不生效。
    """
    from organs.body.PulseLiver import m364_fuse_backoff_plan
    _lv = _liver()
    _before = [_node("[器官别名] A", trust=90.0), _node("[器官别名] B", trust=90.0)]
    _fp_old = (2, 0)                       # 旧口径：2 节点、0 达标
    _fp_new = _lv._m377_fuse_fingerprint(_before)   # 新口径：(0, 0)
    assert _fp_new != _fp_old
    # 指纹未变且已失败 2 次 ⇒ 封顶 6h（停止自动重试）
    _dur, _fc, _why = m364_fuse_backoff_plan(2, _fp_old, _fp_old, 300.0)
    assert _why == "data_unchanged_capped" and _dur == 6 * 3600.0
    # 指纹变了 ⇒ 计数重置、回到起步档（允许重试一次）
    _dur2, _fc2, _why2 = m364_fuse_backoff_plan(2, _fp_new, _fp_old, 300.0)
    assert _why2 == "fingerprint_changed" and _fc2 == 1 and _dur2 == 300.0
