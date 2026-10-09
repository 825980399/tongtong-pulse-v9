# -*- coding: utf-8 -*-
"""176批段3 进化保守 + 埋点 隔离单测。

覆盖：
  1) config.EVOLUTION_CONFIG 中 SAFE_TO_AUTO_FIX 默认空列表（零回归）；
  2) PatchManager 自动审批门白名单守门三态（空=放行 / 非空且命中=放行 / 非空且未命中=转人工）；
  3) SafeEvolutionExecutor._clean_llm_code 的 A3 三段计数（进入/处数/仍失败）。

隔离纪律（铁律151）：清理 PYTHONPATH/PYTHONHOME/PYTHONSTARTUP + PYTHONNOUSERSITE；
用 __new__ 构造避开 __init__ 后台线程（否则 pytest 挂起不退出）；
通过 monkeypatch 类级 stub 隔离落盘/审批写入等重量副作用。
SAFE_TO_AUTO_FIX 是 config.EVOLUTION_CONFIG 字典键，故测试改 EVOLUTION_CONFIG 字典。
"""
import os

# 隔离环境（铁律151）：清理可能污染解释器的环境变量
os.environ.pop("PYTHONPATH", None)
os.environ.pop("PYTHONHOME", None)
os.environ.pop("PYTHONSTARTUP", None)
os.environ["PYTHONNOUSERSITE"] = "1"

import config as _cfg_mod  # 真实 config（只读基线），测试内改属性再还原


def _make_patch_manager():
    """用 __new__ 构造 PatchManager，避开 __init__ 后台线程（否则 pytest 挂起）。"""
    from nucleus.reasoning.PatchManager import PatchManager
    mgr = PatchManager.__new__(PatchManager)
    mgr._pending_file = "tmp_x_pending.json"
    mgr._history_file = "tmp_x_history.json"
    return mgr


def _stub_patchmgr(monkeypatch):
    """类级 stub 重量副作用，使 save_pending_patch 只跑白名单判定逻辑。"""
    from nucleus.reasoning.PatchManager import PatchManager
    monkeypatch.setattr(PatchManager, "_load_patch_list",
                        staticmethod(lambda *a, **k: []))
    monkeypatch.setattr(PatchManager, "_save_patch_list",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(PatchManager, "_approve_via",
                        staticmethod(lambda *a, **k: True))
    monkeypatch.setattr(PatchManager, "_m105_try_release_low_risk",
                        lambda self, *a, **k: None)


def _set_evo(monkeypatch, safe_list):
    """以副本替换 config.EVOLUTION_CONFIG，注入 SAFE_TO_AUTO_FIX 与确定性阈值。"""
    _base = dict(getattr(_cfg_mod, "EVOLUTION_CONFIG", {}) or {})
    _base["SAFE_TO_AUTO_FIX"] = safe_list
    _base["auto_apply_max_risk"] = 2
    _base["auto_apply_min_trust"] = 40
    _base["auto_apply_enabled"] = True  # 隔离白名单决策：关闭 T7 总闸门拦截
    monkeypatch.setattr(_cfg_mod, "EVOLUTION_CONFIG", _base)


def _base_patch(issue_type):
    return {
        "file": "some_module.py",
        "method": "some_method",
        "risk_level": "极低",
        "trust_score": 80,
        "issue_type": issue_type,
        "source": "llm",
        "status": "pending",
        "modified_code": "def foo():\n    return 1\n",
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
        "repair_source": "llm_multi_file",
    }


def test_config_safe_to_auto_fix_default_empty():
    _evo = getattr(_cfg_mod, "EVOLUTION_CONFIG", {}) or {}
    assert _evo.get("SAFE_TO_AUTO_FIX", None) == [], \
        "EVOLUTION_CONFIG.SAFE_TO_AUTO_FIX 必须默认空列表（零回归）"


def test_whitelist_empty_allows_auto_approve(monkeypatch):
    _stub_patchmgr(monkeypatch)
    _set_evo(monkeypatch, [])
    mgr = _make_patch_manager()
    p = _base_patch("formatting")
    mgr.save_pending_patch(p)
    assert p.get("auto_approved") is True, \
        "空白名单：risk/trust 达标应免签自动审批（零回归）"


def test_whitelist_blocks_nonlisted_issue_type(monkeypatch):
    _stub_patchmgr(monkeypatch)
    _set_evo(monkeypatch, ["unused_import"])
    mgr = _make_patch_manager()
    p = _base_patch("formatting")  # 不在白名单
    mgr.save_pending_patch(p)
    assert p.get("auto_approved") is not True, \
        "非空白名单且 issue_type 不在其中：应转人工，不得自动审批"
    assert p.get("status") == "pending", "转人工：status 应保持 pending"


def test_whitelist_allows_listed_issue_type(monkeypatch):
    _stub_patchmgr(monkeypatch)
    _set_evo(monkeypatch, ["unused_import"])
    mgr = _make_patch_manager()
    p = _base_patch("unused_import")  # 在白名单
    mgr.save_pending_patch(p)
    assert p.get("auto_approved") is True, \
        "非空白名单且 issue_type 命中：应免签自动审批"


def test_clean_llm_code_increments_a3_counters():
    from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
    _entered0 = SafeEvolutionExecutor._A3_normalize_entered
    _places0 = SafeEvolutionExecutor._A3_normalize_places
    # 带全角标点的代码：初始 ast.parse 失败，归一化后应通过
    bad = "def f()：\n    x＝1\n    y＝2\n"
    inst = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
    out = inst._clean_llm_code(bad)
    assert "：" not in out, "全角冒号应被归一化为半角"
    assert "＝" not in out, "全角等号应被归一化为半角"
    assert SafeEvolutionExecutor._A3_normalize_entered == _entered0 + 1, \
        "进入归一化计数应 +1"
    assert SafeEvolutionExecutor._A3_normalize_places > _places0, \
        "归一化处数应累计"
    # 干净代码（无全角）：初始即 parse 通过，应不进入归一化
    entered1 = SafeEvolutionExecutor._A3_normalize_entered
    good = "def g():\n    return 1\n"
    out2 = inst._clean_llm_code(good)
    assert "def g()" in out2 and "return 1" in out2, "干净代码应原样返回"
    assert SafeEvolutionExecutor._A3_normalize_entered == entered1, \
        "干净代码不应进入归一化（计数不变）"
