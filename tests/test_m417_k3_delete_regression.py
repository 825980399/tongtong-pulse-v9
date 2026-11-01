# -*- coding: utf-8 -*-
"""417 刀3 回归单测：删除之后「没删错」。

判据取向（对应任务书「O3删除后回归单测（确保没删错）」）：
  ① 14 条被删方法在源码里**确实不存在**了（不是只删了 import）；
  ② 所在文件仍可 `ast.parse`（删完不是语法坏文件）；
  ③ 所在类仍在、仍可 import（没有连带把类删掉）；
  ④ 全库（含 tests/）不再有任何一处**词边界**引用这些名字；
  ⑤ 所在类里没有因此留下**悬空 self 调用**（族删一半的典型症状）；
  ⑥ 同一批**没有误删**被保留的邻居 —— 名单里的「保留项」必须还在
     （`chat_service._get_recent_context` / `BasePulseOrgan._get_insight_board` 等），
     这一条专治「一刀切多删」。

★ 本文件踩过一次假红，口径别再退回去：
  首版 ④ 用朴素 `name in text`，报出 4 处「删除后仍有引用」，逐条核实**全是子串误伤**：
    `_source_timeliness`  撞 `_source_timeliness_seconds`（另一个活着的同族方法）
    `_ensure_state`       撞 `_m24_ensure_state` / `_m29_ensure_state`（别的器官自己的）
  另一类命中是本文件自己（把 14 个名字当字面量列着）⇒ 扫描必须跳过自身。
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import sys
from pathlib import Path

import pytest

from nucleus._silent_except import silent_exc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: (file, class, method) —— 与 tmp/m417_k3_delete.py 的 WHITELIST 逐字对应
DELETED = [
    ("nucleus/vector_store/faiss_store.py", "FAISSVectorStore", "_ensure_state"),
    ("nucleus/SurvivalOrchestrator.py", "SurvivalOrchestrator", "_maybe_unlock"),
    ("nucleus/pulse/PulseCore.py", "PulseCore", "_clean_expired_fingerprints"),
    ("nucleus/synapsys/ResonanceEngine.py", "ResonanceEngine", "_source_timeliness"),
    ("nucleus/reasoning/SafeEvolutionExecutor.py", "SafeEvolutionExecutor", "_patch_generic"),
    ("nucleus/reasoning/SafeEvolutionExecutor.py", "SafeEvolutionExecutor", "_patch_bare_except"),
    ("nucleus/reasoning/SafeEvolutionExecutor.py", "SafeEvolutionExecutor", "_patch_status_request"),
    ("nucleus/reasoning/SafeEvolutionExecutor.py", "SafeEvolutionExecutor", "_patch_silent_exception"),
    ("nucleus/reasoning/SafeEvolutionExecutor.py", "SafeEvolutionExecutor", "_classify_issue_complexity"),
    ("organs/body/PulseHeart.py", "PulseHeart", "_get_field_value"),
    ("organs/senses/PulseVisualCortex.py", "PulseVisualCortex", "_extract_face_encoding"),
    ("organs/senses/PulseEars.py", "PulseEars", "_update_ear_log"),
    ("nucleus/mnemosyne/PulseSnapshot.py", "PulseSnapshot", "_count_changed_nodes"),
    ("organs/body/PulseLung.py", "PulseLung", "_build_enhanced_prompt"),
]

#: 同一轮筛出来但**有意保留**的（判据放过，人工判定不能删）——必须还在
KEPT = [
    ("functions/chat/chat_service.py", "ChatService", "_get_recent_context",
     "docstring 明确声明「保留作为短期记忆的对外查询接口」"),
    ("nucleus/self_inspector.py", "SelfInspector", "_invalidate_all_caches",
     "docstring 声明「代码修改后调用」"),
    ("base/BasePulseOrgan.py", "BasePulseOrgan", "_get_insight_board",
     "基类给子类的依赖注入 helper，属子类契约面"),
    ("base/BasePulseOrgan.py", "BasePulseOrgan", "_get_trace_summary",
     "docstring 标注「v17.0 D8 预埋」"),
    ("organs/brain/PulseReflection.py", "PulseReflection", "_reflect_on_latest",
     "docstring「保留原有逻辑（心跳批量处理时处理）」"),
    ("organs/brain/PulseCodeLearner.py", "PulseCodeLearner", "_check_organ_completion",
     "删了会掩盖一个真 bug：器官完成 → 生成说明书 这条链从没被触发过"),
]

_SKIP_DIRS = {".git", "__pycache__", "tmp", "node_modules", ".pytest_cache",
              "logs", "data", "_archive", "_quarantine"}

_RE_CACHE: dict = {}


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def _word_re(name: str):
    """词边界正则：`_ensure_state` 不该匹配到 `_m24_ensure_state`。"""
    _r = _RE_CACHE.get(name)
    if _r is None:
        _r = re.compile(r"(?<![\w])%s(?![\w])" % re.escape(name))
        _RE_CACHE[name] = _r
    return _r


def _self_rel() -> str:
    return os.path.relpath(os.path.abspath(__file__), str(ROOT)).replace("\\", "/")


def _iter_py():
    """全库 .py 相对路径（跳过 _SKIP_DIRS / 隐藏目录 / **本单测自身**）。"""
    _me = _self_rel()
    for _dp, _dn, _fn in os.walk(ROOT):
        _dn[:] = [_d for _d in _dn if _d not in _SKIP_DIRS and not _d.startswith(".")]
        for _f in _fn:
            if not _f.endswith(".py"):
                continue
            _rel = os.path.relpath(os.path.join(_dp, _f), str(ROOT)).replace("\\", "/")
            if _rel == _me:
                continue
            yield _rel


def _find_in_class(tree, cls: str, name: str):
    for _n in ast.walk(tree):
        if isinstance(_n, ast.ClassDef) and _n.name == cls:
            for _m in _n.body:
                if isinstance(_m, (ast.FunctionDef, ast.AsyncFunctionDef)) and _m.name == name:
                    return _m
    return None


def _find_class(tree, cls: str):
    for _n in ast.walk(tree):
        if isinstance(_n, ast.ClassDef) and _n.name == cls:
            return _n
    return None


# ============================================================ ① 真没了
@pytest.mark.parametrize("rel,cls,name", DELETED)
def test_10_deleted_method_gone(rel, cls, name):
    _src = _read(rel)
    _tree = ast.parse(_src, filename=rel)
    assert _find_in_class(_tree, cls, name) is None, \
        "%s.%s 还在 %s 里（没删掉或删错位置）" % (cls, name, rel)


# ============================================================ ② 文件仍可解析
@pytest.mark.parametrize("rel", sorted({d[0] for d in DELETED}))
def test_20_file_still_parsable(rel):
    _tree = ast.parse(_read(rel), filename=rel)
    assert isinstance(_tree, ast.Module)


# ============================================================ ③ 类仍在
@pytest.mark.parametrize("mod,cls", sorted({(d[0], d[1]) for d in DELETED}))
def test_30_class_still_importable(mod, cls):
    _name = mod[:-3].replace("/", ".")
    _m = importlib.import_module(_name)
    assert hasattr(_m, cls), "%s 里没有 %s 了" % (_name, cls)


# ============================================================ ④ 全库零引用（词边界）
def test_40_no_reference_anywhere():
    """全库词边界扫描。跳过本文件（它把 14 个名字当字面量列着）。"""
    _names = sorted({d[2] for d in DELETED})
    _hits = []
    for _rel in _iter_py():
        try:
            _txt = (ROOT / _rel).read_text(encoding="utf-8", errors="replace")
        except OSError as _e_rd:
            silent_exc(_e_rd, where="tests.test_m417_k3_delete_regression:read",
                       level="debug")
            continue
        for _n in _names:
            _m = _word_re(_n).search(_txt)
            if _m:
                _ln = _txt.count("\n", 0, _m.start()) + 1
                _hits.append("%s:%d:%s" % (_rel, _ln, _n))
    assert not _hits, "删除后仍有引用（%d 处）：%r" % (len(_hits), _hits[:10])


# ============================================================ ⑤ 无悬空 self 调用
def _attr_assigned(cnode) -> set:
    """类里出现过 `self.X = ...` / `cls.X = ...` 的 X。

    这类 X 可能是可调用属性（回调、partial），AST 分不清 ⇒ 一律放行。"""
    _out = set()
    for _n in ast.walk(cnode):
        if isinstance(_n, ast.Assign):
            for _t in _n.targets:
                if isinstance(_t, ast.Attribute) and isinstance(_t.value, ast.Name) \
                        and _t.value.id in ("self", "cls"):
                    _out.add(_t.attr)
    return _out


def _has_dynamic_dispatch(cnode) -> bool:
    """`setattr` / `__getattr__` / `__getattribute__` 在场 ⇒ 静态判不了，整类跳过。"""
    _names = {m.name for m in cnode.body
              if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if _names & {"__getattr__", "__getattribute__"}:
        return True
    for _n in ast.walk(cnode):
        if isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name) \
                and _n.func.id == "setattr":
            return True
    return False


@pytest.mark.parametrize("rel,cls", sorted({(d[0], d[1]) for d in DELETED}))
def test_41_owning_class_no_dangling_self_call(rel, cls):
    """删掉方法后，类里不许还留着指向它的 `self.xxx()`。

    这是纯文本扫描做不到的一层：`_patch_*` 四兄弟若只删三条，剩下那条里若还调
    `self._patch_generic(...)`，源码里看不到 `def` 了但调用还在 —— 一跑到就
    AttributeError。`dir(类)` 带继承来的方法，够覆盖 `BasePulseOrgan` 这条路。
    """
    _tree = ast.parse(_read(rel), filename=rel)
    _cnode = _find_class(_tree, cls)
    assert _cnode is not None, "%s 里找不到类 %s" % (rel, cls)
    if _has_dynamic_dispatch(_cnode):
        pytest.skip("%s.%s 有 setattr/__getattr__，静态判不了" % (rel, cls))

    _mod = importlib.import_module(rel[:-3].replace("/", "."))
    _klass = getattr(_mod, cls)
    _known = set(dir(_klass)) | _attr_assigned(_cnode)

    _dangling = []
    for _n in ast.walk(_cnode):
        if not isinstance(_n, ast.Call):
            continue
        _f = _n.func
        if isinstance(_f, ast.Attribute) and isinstance(_f.value, ast.Name) \
                and _f.value.id in ("self", "cls") and _f.attr not in _known:
            _dangling.append("L%d self.%s" % (_n.lineno, _f.attr))
    assert not _dangling, \
        "%s.%s 里有悬空 self 调用（方法删了调用没删）：%r" % (rel, cls, _dangling[:8])


# ============================================================ ⑥ 没多删
@pytest.mark.parametrize("rel,cls,name,why", KEPT)
def test_50_kept_methods_survive(rel, cls, name, why):
    _tree = ast.parse(_read(rel), filename=rel)
    assert _find_in_class(_tree, cls, name) is not None, \
        "%s.%s 被误删了（保留理由：%s）" % (cls, name, why)


# ============================================================ ⑦ 家族口径
def test_60_patch_family_fully_removed():
    """`_patch_generic/_patch_bare_except/_patch_status_request/_patch_silent_exception`
    是一整个被淘汰的分派族，四条同批删；不许只删一半留悬空引用。"""
    _txt = _read("nucleus/reasoning/SafeEvolutionExecutor.py")
    for _n in ("_patch_generic", "_patch_bare_except",
               "_patch_status_request", "_patch_silent_exception"):
        assert not _word_re(_n).search(_txt), "%s 还在（族删了一半）" % _n


def test_61_delete_count_in_range():
    """任务书要求「真删 10-20 条」。"""
    assert 10 <= len(DELETED) <= 20, len(DELETED)
    assert len({(d[0], d[2]) for d in DELETED}) == len(DELETED), "名单里有重复项"


def test_62_whitelist_matches_delete_tool():
    """单测名单必须与删除脚本的白名单逐字一致（否则两边各说各话）。"""
    _p = ROOT / "tmp" / "m417_k3_delete.py"
    if not _p.exists():
        pytest.skip("删除脚本不在了（tmp/ 被清理）")
    _tree = ast.parse(_p.read_text(encoding="utf-8"))
    _wl = None
    for _n in _tree.body:
        if isinstance(_n, ast.Assign) and getattr(_n.targets[0], "id", "") == "WHITELIST":
            _wl = ast.literal_eval(_n.value)
    assert _wl is not None, "删除脚本里找不到 WHITELIST"
    assert [tuple(x) for x in _wl] == [tuple(x) for x in DELETED], \
        "删除脚本白名单与本单测名单不一致"
