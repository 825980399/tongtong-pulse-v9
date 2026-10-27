# -*- coding: utf-8 -*-
"""★375批刀7：条件触发型写面的**条件解除复测** —— 判据自检。

## 为什么刀7 不是「再测一遍写面」，而是「把条件解除掉再测」

`tests/conftest.py` 记了六件「真写 `data/`」的用例，其中 4 件写面**条件触发**。
最典型的是 `unresolved_samples._roll()`：

    for _old in _fs[:-_KEEP]:      # _KEEP = 7
        os.remove(...)

目录里只有 1-2 份样本时，`_fs[:-7]` 是空切片 ⇒ **一个都不删**。
单点实测看到「没删任何文件」，很容易写成「写面已干净」——
但这不是写面消失，是**触发条件没满足**。所以复测必须成对：

    条件未满足（6 份）⇒ dump 后 7 份，**删 0**（假干净就是这样来的）
    条件解除　（8 份）⇒ dump 后 7 份，**删 2**（写面确实还在）

本件锁这条成对判据，并核「复测全程零污染生产 data/」。

## 另附一条实测勘误

票面把 `test_evolution_backup_filter_m60.py` 也记为条件触发，实测
`PatchAutoApprover.record_round()` 每次调用都走 `_save()`（无 if 分支）⇒
它是**无条件写**，只是写面能经 `_base_dir` 注入。条件触发型因此是 **4 件**，不是 5 件。
"""
import ast
import importlib.util
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CI_FILE = os.path.join(ROOT, "tools", "ci", "replay_conditional_writes.py")


def _load():
    _spec = importlib.util.spec_from_file_location("_rcw_375", CI_FILE)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


RCW = _load()

#: 任务书 D13 与 conftest `_M369_DYNAMIC_TARGETS` 记的六件写面
SIX_FILES = {
    "test_dialog_evol_m84.py",
    "test_evolution_backup_filter_m60.py",
    "test_t101a_low_risk_release.py",
    "test_t105a_release_loop.py",
    "test_tool_wiring_m49.py",
    "test_patch_self_apply_m53.py",
}

#: 实测判定的条件触发型（4 件）：靠 unresolved 族滚动 / top_types 滚动
CONDITIONAL_FILES = {
    "test_t101a_low_risk_release.py",
    "test_t105a_release_loop.py",
    "test_tool_wiring_m49.py",
    "test_patch_self_apply_m53.py",
}


# ============================== ① 矩阵完整性
def test_01_matrix_covers_the_six_files():
    """矩阵必须覆盖 conftest 记的六件（漏一件就等于漏一个写面）。"""
    _got = {f["file"] for f in RCW.WRITE_FACES}
    assert _got == SIX_FILES, f"矩阵与六件不一致：多 {_got - SIX_FILES}／少 {SIX_FILES - _got}"


def test_02_conditional_set_is_exactly_four():
    """条件触发型 = 4 件（票面 D13 的数字，用**文件集合**钉，不用裸计数）。

    ★为什么不用 `len(...) == 4`：那只是数字，改一件换一件照样过。
      用集合比，换件就会红，能逼人重新判断「这件到底是不是条件触发」。
    """
    _got = {f["file"] for f in RCW.conditional_faces()}
    assert _got == CONDITIONAL_FILES, f"条件触发型集合变了：{_got}"


def test_03_every_face_declares_port_condition_sandbox():
    """每条写面必须声明「写口 / 生产目标 / 触发条件 / 沙箱注入口」，四位一体。"""
    for _f in RCW.WRITE_FACES:
        for _k in ("file", "port", "target", "condition", "sandbox"):
            assert str(_f.get(_k) or "").strip(), f"{_f.get('file')} 缺字段 {_k}"
        assert _f["target"].startswith("data/"), f"{_f['file']} 的 target 不在 data/ 下"


# ============================== ② 成对判据（本刀的核心）
def test_04_below_keep_deletes_nothing(tmp_path):
    """条件**未满足**：6 份 + 新增 1 = 7 份 ⇒ `_fs[:-7]` 为空 ⇒ 一个都不删。

    这一条就是「单点实测得出假干净」的机器复现 —— 它是**正确行为**，
    不是缺陷；缺陷在于把它当成「写面已消失」的结论。
    """
    _r = RCW.replay_unresolved(str(tmp_path))
    _b = _r["below_keep"]
    assert _b["before"] == 6
    assert _b["deleted"] == 0, f"未达 _KEEP 阈值却删了 {_b['deleted']} 份"
    assert _b["after"] == 7


def test_05_above_keep_deletes_the_oldest(tmp_path):
    """条件**解除**：8 份 + 新增 1 = 9 份 ⇒ 只留 7 份 ⇒ 删 2 份。

    写面仍然在。若这条也「删 0」，说明滚动清理真的失效了（那时才叫干净）。
    """
    _r = RCW.replay_unresolved(str(tmp_path))
    _a = _r["above_keep"]
    assert _a["before"] == 8
    assert _a["deleted"] == 2, f"越过 _KEEP 阈值应删 2 份，实际 {_a['deleted']}"
    assert _a["after"] == 7
    assert _r["consistent"] is True


def test_06_pair_verdict_is_two_sided(tmp_path):
    """成对判据必须**两侧都成立**才算通过（只验一侧＝又一次假干净）。"""
    _r = RCW.replay_unresolved(str(tmp_path))
    assert _r["below_keep"]["deleted"] == 0
    assert _r["above_keep"]["deleted"] > 0
    assert _r["consistent"] is True


# ============================== ③ 沙箱注入口真的生效
def test_07_artifact_reroutes_when_declared(tmp_path):
    _r = RCW.replay_artifact(str(tmp_path))
    assert _r["rerouted"] is True, "有隔离声明却仍指向 data/ ⇒ 沙箱失效"
    assert _r["under_tmp"] is True, f"改道后没落在沙箱内：{_r['declared_dir']}"
    assert _r["reason"], "改道必须留原因（不留痕就等于静默改道）"


def test_08_artifact_keeps_prod_dir_without_declaration(monkeypatch):
    """**反向**：没有隔离声明时必须原样返回生产目录。

    否则等于「任何进程都被改道」—— 生产自己的自观测产物会凭空消失。
    """
    from nucleus import domain_guard as G

    for _k in G.ISOLATION_ENV_KEYS:
        monkeypatch.delenv(_k, raising=False)
    _actual, _why = G.guard_artifact_dir("data/reports/memory")
    assert _actual == "data/reports/memory"
    assert _why == ""


def test_09_hub_follows_ctor_path(tmp_path):
    _r = RCW.replay_hub(str(tmp_path))
    assert _r["follows_ctor"] is True, "hub 没跟随构造器路径 ⇒ 会写生产"
    assert _r["default_is_prod"] is True, "hub 默认路径应指向生产 data/（正是风险来源）"


# ============================== ④ 复测全程零污染
def test_10_replay_never_writes_prod_data(tmp_path):
    """复测用的预置文件名（`20200101_0000*`）绝不允许出现在生产 data/ 下。

    这是「零污染」的**精确**证据：预置名极独特，不会与活体产物撞名，
    所以比「文件数不变」更稳（活体每 61 分钟会自己写一份图快照）。
    """
    _prod = os.path.join(ROOT, "data", "call_graph")
    _r = RCW.run_replay(str(tmp_path))
    assert _r["all_ok"] is True, f"复测有项未通过：{_r}"
    if os.path.isdir(_prod):
        _leak = [f for f in os.listdir(_prod) if "20200101_0000" in f]
        assert _leak == [], f"预置文件漏进生产 data/call_graph：{_leak}"
    _sandbox = os.path.join(str(tmp_path), "data", "call_graph")
    _there = [f for f in os.listdir(_sandbox) if "20200101_0000" in f]
    # 预置 8 份 → 删 2 份最老 ⇒ 预置剩 6 份；加上 dump 新写的那 1 份，总共 7 份（_KEEP）
    assert len(_there) == 6, f"预置文件应剩 6 份（8 删 2），实际 {len(_there)}"
    _all = [f for f in os.listdir(_sandbox) if f.endswith(".jsonl")]
    assert len(_all) == 7, f"沙箱总份数应等于 _KEEP=7，实际 {len(_all)}"


# ============================== ⑤ 复测件自身的写面约束
def test_11_replay_module_writes_only_under_injected_roots():
    """`open(..., 'w')` 的路径必须来自注入的 root（tmp_root/project_root）。"""
    with open(CI_FILE, encoding="utf-8") as _f:
        _src = _f.read()
    _tree = ast.parse(_src)
    _bad = []
    for _n in ast.walk(_tree):
        if not isinstance(_n, ast.Call):
            continue
        _f = getattr(_n.func, "id", None) or getattr(_n.func, "attr", None) or ""
        if _f != "open":
            continue
        _m = ""
        if len(_n.args) >= 2 and isinstance(_n.args[1], ast.Constant):
            _m = str(_n.args[1].value)
        if not any(c in _m for c in "wax+"):
            continue
        _tgt = ast.unparse(_n.args[0]) if _n.args else ""
        # 允许的写口：显式注入的沙箱根（tmp_root / _seed 的 d）、--json 的显式输出件
        _ok = ("tmp_root" in _tgt or "os.path.join(d," in _tgt
               or "_p" in _tgt or _tgt.endswith("_a.json"))
        if not _ok:
            _bad.append(_tgt)
    assert _bad == [], f"存在不受注入 root 约束的写口：{_bad}"


def test_12_scope_is_declared():
    assert RCW.SCOPE, "口径必须随结果输出（铁律219）"
    assert "条件" in RCW.SCOPE and "沙箱" in RCW.SCOPE


@pytest.mark.parametrize("family", ["unresolved", "artifact", "hub"])
def test_13_each_family_has_replay(family):
    """矩阵里出现的每个族都必须有对应复测函数（声明了却不复测＝空话）。"""
    assert hasattr(RCW, f"replay_{family}"), f"族 {family} 没有复测实现"
