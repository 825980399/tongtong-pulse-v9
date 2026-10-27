# -*- coding: utf-8 -*-
"""★375批刀9：当日入池限额的「撤销 / 作废」回退口径。

## 票面与实测的差

票面：「44 撤销/作废后当日入池限额是否回退需钉死」。
实测两半**不对称**：

* **撤销**（`status ∈ _O3_REVOKED_STATUSES`）—— 354刀5 已钉死：撤销只改 `status`、
  不动入池标记 ⇒ 当日计数**不减**，另给 `revoked_no_rollback` 显式计数。
  本刀只补判据锁住（原来 `tests/test_m354_batch.py` 里只有「已撤销」一条）。
* **作废**（284刀5 结构缺陷补丁）—— **没堵**。`PatchManager._m280_converge_pending`
  对作废条目是 `continue` 掉：**不写 status、不进 history、条目直接从 pending 池消失**。
  于是作废一条，`_m333_intake_rate` 的当日计数就少一条 ⇒ 「每天 ≤1 条」可被
  「生成 → 作废 → 再生成」无限绕开。

## 本刀的堵法（两条生产改动）

1. `PatchManager` 写台账时补 `issue_type` / `o3_dead_code` / `generated_at`
   —— 台账得能自证「这条是 O3 入池、哪天入的池」；
2. `_m333_intake_rate` 增**第三轨**读台账，把作废条目补算回当日计数，另给
   `voided_no_rollback` / `voided_candidates` / `voided_dup_skipped` / `voided_ledger`。

三条去重防虚增（虚增会让限额变严、把正常入池卡死）：
① 只认台账**自带 O3 标记**的条目（存量 170 条无标记 ⇒ 一律不计，宁可漏报）；
② 同 `patch_id` 多条台账只算 1 条；③ `patch_id` 仍在两轨里则不双计。

## 基准一致性（这个口最容易踩）

第三轨的路径基准必须与上两轨**同一个** `_root`：`converge_voided_ledger_path()`
钉的是「本模块上溯三层」的项目根、**不看 `project_root` 参数** ⇒ 照它读，
`project_root=临时目录` 的隔离调用仍会读到**生产台账**（铁律158）。
`test_12` 专门用「tmp 有标记条 + 生产台账无标记」的对比把这条钉住。
"""
import ast
import json
import os
import time

import pytest

from nucleus.self_inspector import (
    _O3_INTAKE_MAX_PER_DAY,
    _O3_QUOTA_EXEMPT_STATUSES,
    _O3_RESTORE_NO_QUOTA_FIELD,
    _O3_REVOKED_STATUSES,
    _m333_intake_rate,
)

#: 豁免集合的小写形态（`_m348_quota_exempt` 内部就是 `.strip().lower()` 后比对）
_EXEMPT_LC = frozenset(str(x).strip().lower() for x in _O3_QUOTA_EXEMPT_STATUSES)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SI_PATH = os.path.join(ROOT, "nucleus", "self_inspector.py")
PM_PATH = os.path.join(ROOT, "nucleus", "reasoning", "PatchManager.py")
VOIDED_REL = os.path.join("data", "reports", "converge_voided.jsonl")


def _mkroot(tmp_path):
    (tmp_path / "data" / "patches").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "reports").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _pending(root, rows):
    _p = root / "data" / "patches" / "pending_patches.json"
    _p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return str(_p)


def _voided(root, rows):
    _p = root / VOIDED_REL
    _p.parent.mkdir(parents=True, exist_ok=True)
    with open(_p, "w", encoding="utf-8", newline="\n") as _f:
        for _r in rows:
            if isinstance(_r, str):
                _f.write(_r + "\n")          # 原样写（造坏行用）
            else:
                _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
    return str(_p)


def _o3_row(_id, _ts, status="pending", **extra):
    _r = {"id": _id, "file": "nucleus/x.py", "method": "foo",
          "issue_type": "dead_code_candidate", "o3_dead_code": True,
          "generated_at": _ts, "status": status}
    _r.update(extra)
    return _r


def _void_row(_id, _ts, **extra):
    _r = {"ts": _ts, "patch_id": _id, "file": "nucleus/x.py", "method": "foo",
          "reason": "284刀5 结构缺陷补丁作废", "issue_type": "dead_code_candidate",
          "o3_dead_code": True, "generated_at": _ts}
    _r.update(extra)
    return _r


# ============================ ① 撤销：口径一致性（不锁「哪种行为对」）
@pytest.mark.parametrize("_status", sorted(_O3_REVOKED_STATUSES))
def test_01_revoked_rollback_matches_set_intersection(tmp_path, _status):
    """回退与否**只由两个集合的交集决定** —— 这是不变量，不是具体值快照。

    `_O3_QUOTA_EXEMPT_STATUSES`（348刀2：豁免 ⇒ 不占额度）与
    `_O3_REVOKED_STATUSES`（354刀5：撤销 ⇒ 不回退）在
    `wont_fix / cancelled / revoked` 上有**交集**，而豁免判定在主流程里
    **先于**撤销分支生效并 `continue` ⇒ 这 3 个值的当日额度实际上**是回退的**，
    与 354 注释「撤销不回退」相反（且 `revoked_no_rollback` 曾对这 3 个值恒 0）。

    本判据不锁「哪种行为才算对」（那是裁决），只钉「读数必须与集合交集一致」：
    交集内 ⇒ `count=0` 且 `revoked_but_exempt=1`；交集外 ⇒ `count=1` 且
    `revoked_no_rollback=1`。裁决改了集合，本件自动跟随，不会变成历史快照。
    """
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [_o3_row("a", _now, status=_status)])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["revoked_total"] == 1, "撤销计数漏了（实现≠生效）"
    if str(_status).strip().lower() in _EXEMPT_LC:
        assert _r["count"] == 0, f"{_status} 在豁免集合里却没被豁免"
        assert _r["revoked_but_exempt"] == 1, "额度回退了但没记账（静默回退）"
        assert _r["revoked_no_rollback"] == 0
    else:
        assert _r["count"] == 1, f"{_status} 不在豁免集合里、额度却回退了"
        assert _r["revoked_no_rollback"] == 1
        assert _r["revoked_but_exempt"] == 0


def test_01b_revoked_readings_are_self_consistent(tmp_path):
    """三条读数必须自洽：`revoked_total == no_rollback + but_exempt`。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [
        _o3_row("a", _now, status="已撤销"),        # 不豁免 ⇒ 不回退
        _o3_row("b", _now, status="wont_fix"),      # 豁免 ⇒ 回退
        _o3_row("c", _now, status="cancelled"),     # 豁免 ⇒ 回退
    ])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["revoked_total"] == 3
    assert _r["revoked_no_rollback"] + _r["revoked_but_exempt"] == 3, _r
    assert _r["count"] == 1, "只有不豁免的那条占额度"


def test_01c_restore_and_revoked_are_counted_separately(tmp_path):
    """`restore` 与 `revoked` 是两件事，不许合流（不同事实不同措辞，铁律166）。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [
        _o3_row("a", _now, status="已撤销"),
        _o3_row("b", _now, **{_O3_RESTORE_NO_QUOTA_FIELD: True}),
    ])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["revoked_total"] == 1
    assert _r["restore_no_quota"] == 1
    assert _r["count"] == 1


def test_02_restore_still_consumes_no_quota(tmp_path):
    """反向：`o3_restore_no_quota` 的条目**不占**额度（不是不算，是不进额度）。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [_o3_row("a", _now, **{_O3_RESTORE_NO_QUOTA_FIELD: True})])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["count"] == 0
    assert _r["restore_no_quota"] == 1


# ============================ ② 作废：不回退（本刀新增的第三轨）
def test_03_voided_entry_counts_back_even_if_gone_from_pool(tmp_path):
    """**缺口本体**：作废条目已从 pending 池消失（不写 status、不进 history），
    当日计数必须靠第三轨补算回来 —— 否则额度白退。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])                      # 池里**没有**它了（作废已出队）
    _voided(_root, [_void_row("v1", _now)])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["count"] == 1, "作废后当日额度回退了 ⇒ 可被「生成→作废→再生成」绕开"
    assert _r["voided_no_rollback"] == 1
    assert _r["voided_candidates"] == 1
    assert _r["voided_ledger"] == "ok"


def test_04_voided_still_in_pool_is_not_double_counted(tmp_path):
    """同 id 既在池里又在台账 ⇒ 只算 1 条（两轨已计过，第三轨不许叠加）。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [_o3_row("dup", _now)])
    _voided(_root, [_void_row("dup", _now)])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["count"] == 1, "同一补丁被两轨各记一次 ⇒ 虚增额度"
    assert _r["voided_dup_skipped"] == 1


def test_05_same_patch_voided_many_times_counts_once(tmp_path):
    """同一条补丁作废多次（台账 append-only 会留多行）仍只占 1 次额度。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _voided(_root, [_void_row("v2", _now), _void_row("v2", _now),
                    _void_row("v2", _now)])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["count"] == 1, f"重复作废被重复计数：count={_r['count']}"
    assert _r["voided_dup_skipped"] == 2


def test_06_voided_without_o3_marker_is_ignored(tmp_path):
    """存量台账（无 O3 标记）**一律不计** —— 宁可漏报不虚增。

    虚增会让限额变严：把非 O3 的作废算成 O3 入池，正常入池就被卡住了。
    本用例同时钉住「不计」和「不是因为没读到」（`voided_ledger == "ok"`）。
    """
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _voided(_root, [{"ts": _now, "patch_id": "old1", "file": "nucleus/x.py",
                     "method": "foo", "reason": "284刀5 结构缺陷补丁作废"}])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["voided_ledger"] == "ok", "台账读到了才算「不计」，读不到是另一回事"
    assert _r["voided_candidates"] == 0
    assert _r["count"] == 0


def test_07_voided_outside_window_is_ignored(tmp_path):
    """窗口外的作废不算今天 —— 否则昨天的账会压着今天。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _voided(_root, [_void_row("v3", _now - 3 * 86400)])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["voided_candidates"] == 1        # 标记命中了
    assert _r["voided_no_rollback"] == 0       # 但落在窗外，不计
    assert _r["count"] == 0


def test_08_absent_ledger_keeps_three_state(tmp_path):
    """台账不存在 = 「从没作废过」，是合法常态：不许把 `source_ok` 拖成不可算。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["voided_ledger"] == "absent"
    assert _r["source_ok"] is True, "缺台账被当成了「数据源不可读」"
    assert _r["count"] == 0


def test_09_bad_line_is_visible_not_silent(tmp_path):
    """台账有坏行 ⇒ 记 `bad_lines`（可 grep），好行照常计入。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _voided(_root, ["{这不是 json", _void_row("v4", _now)])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["voided_ledger"] == "bad_lines"
    assert _r["count"] == 1, "坏行把好行一起吞了"


def test_10_voided_consumes_quota_over_limit(tmp_path):
    """作废补算**真的占额度**：越过限额后 `rate_ok` 必须为 False。

    边界口径是 `_cnt <= limit`（限额 = 每天**允许**入池几条），所以
    「正好 = limit」是达标、「limit+1」才超标 —— 两侧都测，别把 `<=` 当 `<`。
    """
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _voided(_root, [_void_row(f"v{i}", _now)
                    for i in range(_O3_INTAKE_MAX_PER_DAY + 1)])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["count"] == _O3_INTAKE_MAX_PER_DAY + 1
    assert _r["rate_ok"] is False, "作废补算越了限额却仍报达标"


def test_10b_voided_exactly_at_limit_is_ok(tmp_path):
    """正好 = limit ⇒ **达标**（限额语义是 `<=`）。单测一侧就会把 `<=` 写成 `<`。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _voided(_root, [_void_row(f"w{i}", _now)
                    for i in range(_O3_INTAKE_MAX_PER_DAY)])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["count"] == _O3_INTAKE_MAX_PER_DAY
    assert _r["rate_ok"] is True


def test_11_issue_type_marker_also_accepted(tmp_path):
    """O3 标记认两条：`o3_dead_code is True` 或 `issue_type ∈ 类型集合`。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _row = _void_row("v5", _now, issue_type="dead_code")
    _row.pop("o3_dead_code")               # 只有 issue_type
    _voided(_root, [_row])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["voided_no_rollback"] == 1


def test_12_ledger_path_follows_project_root(tmp_path):
    """★基准一致性：第三轨必须跟 `project_root`，不许钉死项目根。

    判别力构造：tmp 台账有 1 条带标记的作废；**生产台账真实存在但存量无标记**。
    若第三轨误用 `converge_voided_ledger_path()`（钉项目根），读到的就是生产台账
    ⇒ count=0（红）；跟 `project_root` 读到 tmp ⇒ count=1（绿）。
    """
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _voided(_root, [_void_row("v6", _now)])
    _prod = os.path.join(ROOT, VOIDED_REL)
    assert os.path.isfile(_prod), "本用例依赖「生产台账存在」这个事实，缺了判别力就弱"
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["count"] == 1, (
        "第三轨读到了生产台账而不是注入的 tmp（隔离破口，铁律158）："
        f"voided_ledger={_r.get('voided_ledger')}")


def test_13_no_hardcoded_ledger_path_in_source():
    """源码里不许出现台账文件名硬字面量（唯一源 = `criteria_window` 的常量）。"""
    with open(SI_PATH, encoding="utf-8") as _f:
        _src = _f.read()
    _tree = ast.parse(_src)
    _bad = []
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.Constant) and isinstance(_n.value, str):
            if "converge_voided" in _n.value and ".jsonl" in _n.value:
                _bad.append((_n.lineno, _n.value))
    assert not _bad, f"硬编码了台账路径：{_bad}"


def test_14_patchmanager_writes_o3_marker_and_time():
    """`note_converge_voided(...)` 必须带 `o3_dead_code=` 与 `generated_at=`。

    缺了它们，第三轨就只能瞎猜（全计 ⇒ 虚增）或全不计（漏报）——两种都是错。
    """
    with open(PM_PATH, encoding="utf-8") as _f:
        _src = _f.read()
    _tree = ast.parse(_src)
    _hits = []
    for _n in ast.walk(_tree):
        if not isinstance(_n, ast.Call):
            continue
        _f = _n.func
        _nm = getattr(_f, "attr", None) or getattr(_f, "id", None)
        if _nm == "note_converge_voided":
            _hits.append({k.arg for k in _n.keywords if k.arg})
    assert _hits, "找不到 note_converge_voided 调用"
    for _kw in _hits:
        assert "o3_dead_code" in _kw, f"台账缺 o3_dead_code 标记：{sorted(_kw)}"
        assert "generated_at" in _kw, f"台账缺入池时间 generated_at：{sorted(_kw)}"


def test_15_relative_const_matches_single_source():
    """`CONVERGE_VOIDED_LEDGER_REL` 与唯一路径口同源（防常量漂移）。"""
    from nucleus.evolution.criteria_window import (
        CONVERGE_VOIDED_LEDGER_REL,
        converge_voided_ledger_path,
    )
    _want = os.path.normpath(os.path.join(ROOT, CONVERGE_VOIDED_LEDGER_REL))
    _got = os.path.normpath(converge_voided_ledger_path())
    assert _want == _got, f"常量与路径口分叉：{_want} != {_got}"


def test_17_revoked_exempt_intersection_is_visible():
    """两集合交集非空 ⇒ 必须能从读数里看出来（不许静默打架）。

    这是本刀最重要的**事实**：354刀5 说「撤销不回退」，但 348刀2 的豁免集合
    把 `wont_fix/cancelled/revoked` 又豁免掉了 ⇒ 这 3 个值额度实际回退。
    不把交集数出来，就只能靠人读两处常量对照 —— 那种「口径」等于没钉。
    """
    from nucleus.self_inspector import _O3_QUOTA_EXEMPT_STATUSES

    _inter = ({str(x).strip().lower() for x in _O3_REVOKED_STATUSES}
              & {str(x).strip().lower() for x in _O3_QUOTA_EXEMPT_STATUSES})
    if not _inter:
        pytest.skip("两集合已无交集（裁决已落地），本判据自动失效")
    with open(SI_PATH, encoding="utf-8") as _f:
        _src = _f.read()
    assert "revoked_but_exempt" in _src, "交集存在却不上报 ⇒ 额度悄悄回退没人知道"
    if _inter != {"wont_fix", "cancelled", "revoked"}:
        pytest.skip(f"交集与 375刀9 记录不同（{sorted(_inter)}），请复核证据件")


def test_16_ts_is_fallback_when_generated_at_missing(tmp_path):
    """台账只有 `ts`（无 `generated_at`）时按 `ts` 落窗 —— 不能直接丢掉。"""
    _root = _mkroot(tmp_path)
    _now = time.time()
    _pending(_root, [])
    _row = _void_row("v7", _now)
    _row.pop("generated_at")
    _voided(_root, [_row])
    _r = _m333_intake_rate(project_root=str(_root), now=_now)
    assert _r["voided_no_rollback"] == 1
    assert _r["count"] == 1
