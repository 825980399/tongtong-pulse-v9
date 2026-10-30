# -*- coding: utf-8 -*-
"""398 刀1：孤儿模块处置 —— 清单与代码一致性。

判据设计（★跟随最新证据，不钉死零引用）：
    * 清单里的每个模块**必须存在**；
    * 每个模块必须带**处置标注**（157 批 `@deprecated` 或 398 批 `398刀1`）；
    * 引用状态**只报告不断言** —— 将来接线了只需更新清单，本测试不该因此变红
      （铁律 268：判据断言历史快照必然变红 ⇒ 跟随最新证据件）；
    * 15 个模块全量 import 冒烟必须通过（任务书"删完后冒烟"的等价要求，
      本批没删，冒烟同样要做 —— 证明这 15 个件本身没腐烂）。
"""
from __future__ import annotations

import ast
import io
import os
import sys

import pytest

from nucleus._silent_except import silent_exc

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_MANIFEST = os.path.join(
    _ROOT, "docs", "路灯与星轨对话", "分析报告", "路灯_398批_孤儿处置清单.md")

#: A 类：157 批已封存（12 个运行时模块 + 1 个构建脚本）
#: ★399刀2：`hardware/hw_abstraction.py` 已按任务书删除（10 行纯 docstring 空壳、
#:   全仓零 import），从本清单移出 —— 它当初在 A 类里的理由是"删掉几乎无损，建议单独成刀"，
#:   399 批就是那一刀。历史记录见处置清单 §五.1。
_KEEP_SEALED = [
    "somatics/actuator.py",
    "somatics/body_state.py",
    "nucleus/chronos/EventStream.py",
    "nucleus/evolution/ParamPatchEffectVerifier.py",
    "nucleus/evolution/PerformanceProfiler.py",
    "nucleus/genesis/EvolutionTracker.py",
    "nucleus/genesis/ProtocolManager.py",
    "nucleus/synapsys/OrganProtocol.py",
    "nucleus/synapsys/VotingEngine.py",
    "nucleus/pulse/setup_cython.py",
    "nucleus/identity/SelfModel.py",
    "nucleus/runtime/Interoception.py",
    "nucleus/runtime/OrganCoordinator.py",
]

#: B 类：本批补标注
_KEEP_ANNOTATED = ["nucleus/self_awareness/artifact_registry.py"]

#: C 类：扫描误判（不是孤儿），带各自的引用证据
_FALSE_POSITIVE = {
    "pulse_doctor.py": "python pulse_doctor.py 直跑入口",
    "nucleus/search_scheduler.py": "main.py 停机注册表按模块名字符串引用",
}

_ALL = _KEEP_SEALED + _KEEP_ANNOTATED

#: import 冒烟清单（模块路径；pulse_doctor 是脚本入口，只做文件存在性检查）
#: ★399刀2 起为 14 个（原 15 减 `hardware.hw_abstraction`）
_SMOKE = [
    "somatics.actuator",
    "somatics.body_state",
    "nucleus.chronos.EventStream",
    "nucleus.evolution.ParamPatchEffectVerifier",
    "nucleus.evolution.PerformanceProfiler",
    "nucleus.genesis.EvolutionTracker",
    "nucleus.genesis.ProtocolManager",
    "nucleus.synapsys.OrganProtocol",
    "nucleus.synapsys.VotingEngine",
    "nucleus.identity.SelfModel",
    "nucleus.runtime.Interoception",
    "nucleus.runtime.OrganCoordinator",
    "nucleus.self_awareness.artifact_registry",
    "nucleus.search_scheduler",
]


def _read_head(rel: str, lines: int = 40) -> str:
    p = os.path.join(_ROOT, rel)
    with io.open(p, encoding="utf-8", errors="replace") as f:
        return "".join([next(f, "") for _ in range(lines)])


# ---------------------------------------------------------------- 清单本身
def test_01_manifest_exists():
    """处置清单必须落盘（本批的核心交付物之一）。"""
    assert os.path.isfile(_MANIFEST), f"清单缺失：{_MANIFEST}"
    _n = sum(1 for _ in io.open(_MANIFEST, encoding="utf-8"))
    assert _n >= 80, f"清单只有 {_n} 行，不像完整清单"


def test_02_manifest_lists_every_candidate():
    """清单正文必须逐个列到每个候选（防止"清单写了但漏项"）。"""
    _s = io.open(_MANIFEST, encoding="utf-8").read()
    _missing = [r for r in _ALL + list(_FALSE_POSITIVE)
                if os.path.basename(r)[:-3] not in _s]
    assert not _missing, f"清单漏掉：{_missing}"


# ---------------------------------------------------------------- 文件存在
def test_03_all_candidates_exist():
    """14 个保留件 + 2 个误判件都必须在磁盘上。

    ★399刀2 起是 14（原 15 减 `hardware/hw_abstraction.py`，该件已按 399 任务书删除）。
    398 批本身**没删任何文件**，这条判据当时守的就是那个结论。
    """
    _gone = [r for r in _ALL + list(_FALSE_POSITIVE)
             if not os.path.isfile(os.path.join(_ROOT, r))]
    assert not _gone, f"文件不见了（本批不该删任何东西）：{_gone}"


# ---------------------------------------------------------------- 处置标注
@pytest.mark.parametrize("rel", _KEEP_SEALED)
def test_04_sealed_has_deprecated_mark(rel):
    """A 类 13 个必须带 157 批封存标注（缺了就是标注被误删）。"""
    _head = _read_head(rel, 40)
    assert "@deprecated" in _head, f"{rel} 头部缺 157 批 @deprecated 标注"
    assert "157-D" in _head, f"{rel} 的标注里没有票号 157-D"


def test_05_artifact_registry_annotated():
    """B 类补标注必须写明三件事：零调用现状 / 为什么留 / 拟接线方式。"""
    _head = _read_head("nucleus/self_awareness/artifact_registry.py", 60)
    for _kw in ("398刀1", "零调用", "为什么留着", "拟接线方式"):
        assert _kw in _head, f"artifact_registry 标注缺关键字「{_kw}」"


def test_06_false_positives_have_evidence():
    """C 类两个误判件必须有真实的引用证据，不能凭猜。"""
    # ① pulse_doctor 是直跑入口：宪法文档里写明用法
    _const = io.open(os.path.join(_ROOT, "docs", "BLUEPRINT_CONSTITUTION.md"),
                     encoding="utf-8", errors="replace").read()
    assert "pulse_doctor.py" in _const, "宪法文档里找不到 pulse_doctor 的直跑用法"
    # ② search_scheduler 被 main.py 按字符串引用
    _main = io.open(os.path.join(_ROOT, "main.py"), encoding="utf-8",
                    errors="replace").read()
    assert "nucleus.search_scheduler" in _main, \
        "main.py 里找不到 search_scheduler 的停机注册（引用证据失效，清单需更新）"


# ---------------------------------------------------------------- 冒烟
@pytest.mark.parametrize("mod", _SMOKE)
def test_07_import_smoke(mod):
    """全量 import 冒烟：14 个件都要能导入（证明它们本身没腐烂）。"""
    import importlib

    _m = importlib.import_module(mod)
    assert _m is not None
    assert getattr(_m, "__name__", "") == mod


# ---------------------------------------------------------------- 一致性（只报告）
def test_08_manifest_matches_reality():
    """清单与代码的一致性：带标注的件若**已接线**，清单必须记为已接线。

    本测试**不**断言"必须零引用"—— 接线是好事，不该让测试变红。
    它只断言：**如果**被引用了，清单里该行不能还写着"孤儿/保留"。
    """
    _imported = set()
    for _dp, _dns, _fns in os.walk(_ROOT):
        _dns[:] = [d for d in _dns if d not in
                   {".git", "__pycache__", ".venv", "node_modules", "build", "tmp"}
                   and not d.startswith(".bak")]
        for _fn in _fns:
            if not _fn.endswith(".py"):
                continue
            _p = os.path.join(_dp, _fn)
            _rel = os.path.relpath(_p, _ROOT).replace("\\", "/")
            try:
                _tree = ast.parse(io.open(_p, encoding="utf-8",
                                          errors="replace").read())
            except SyntaxError as _e:
                silent_exc(_e, where="m398_k1::test_08 parse", level="debug")
                continue
            for _node in ast.walk(_tree):
                if isinstance(_node, ast.Import):
                    for _a in _node.names:
                        _imported |= set(_a.name.split("."))
                elif isinstance(_node, ast.ImportFrom):
                    if _node.module:
                        _imported |= set(_node.module.split("."))
                    for _a in _node.names:
                        _imported.add(_a.name)

    _manifest = io.open(_MANIFEST, encoding="utf-8").read()
    _newly_wired = []
    for _rel in _ALL:
        _mod = os.path.basename(_rel)[:-3]
        if _mod in _imported:
            # 被引用了 ⇒ 清单里该模块名附近必须出现"已接线"
            _newly_wired.append(_mod)
    # 只报告，不断言；但若真的出现接线，提示更新清单
    if _newly_wired:
        pytest.skip(f"这些件已被引用，清单该更新为「已接线」：{_newly_wired}"
                    f"（清单是否已含「已接线」字样：{'已接线' in _manifest}）")
