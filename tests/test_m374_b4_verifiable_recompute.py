# -*- coding: utf-8 -*-
"""374 刀4 单测：存量 `verifiable_rate` 重算机制。

票 `T-存量重算机制-1`。

票面现象：存量补丁的派生字段（`problem_fixed` 等）只在**补丁刚应用完**或
**人工跑回填脚本**时写一次，之后永远不再算 —— 于是后来探针/基线给出了新证据，
也没有任何东西回去把字段重算一遍。**不是判据太严，是没人再算一次。**

验六件事：
① 冻结存量真的被派生、`verifiable_rate` 真的随证据滚动；
② 无变化**一次都不写盘**（读多写少）；6h 节流窗口内不重算；
③ `dry_run` 如实报变化条数且不落盘；写盘守卫/原子写失败都留得住原档；
④ **指纹覆盖 `backfill` 写入的全部派生字段**（漏一个就"字段旧、判没变"⇒ 永不修）；
⑤ 判据与目标值一行未动（重算只是调度者，不是新判据）；不碰 `pending_patches.json`；
⑥ 行尾守恒（真档是 CRLF）、结构保真、接线不是死代码。
"""
import copy
import hashlib
import io
import json
import os
import tokenize

import pytest

import nucleus.evolution.verifiable_recompute as vr
from nucleus.evolution.patch_verification_split import (
    F_NO_REGRESSION,
    F_PF_REASON,
    F_PROBLEM_FIXED,
    F_SPLIT_VERSION,
    F_VERIFIED,
    backfill,
    derive_problem_fixed,
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PF = F_PROBLEM_FIXED


@pytest.fixture(autouse=True)
def _reset_throttle():
    vr.reset_recompute_throttle()
    yield
    vr.reset_recompute_throttle()


# ------------------------------------------------------------------ 夹具
def _p(pid, **kw):
    d = {"id": pid, "file": "organs/body/PulseHeart.py", "method": "m_" + pid,
         "issue_type": "silent_exception", "status": "applied"}
    d.update(kw)
    return d


def _stock():
    """6 条**冻在 None** 的存量补丁（复刻票面：有证据但没人重算）。

    期望派生：a1 True / a2 None / a3 False / a4 None / a5 True / a6 None
    ⇒ 可判定 3 条，`verifiable_rate` = 0.5。
    """
    return [
        _p("a1", reprobe_verdict="true_pass", reprobe_baseline_hits=3,
           verification={"passed": True}),
        _p("a2", reprobe_verdict="true_pass", reprobe_baseline_hits=0),
        _p("a3", reprobe_verdict="false_pass"),
        _p("a4", reprobe_verdict="partial_fix"),
        _p("a5", baseline_errors=9, post_apply_errors=2),
        _p("a6"),
    ]


def _rel(root=None):
    return os.path.join(str(root or _REPO), "data", "patches", "patch_history.json")


def _write_doc(path, doc, eol="\r\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _t = json.dumps(doc, ensure_ascii=False, indent=2)
    if eol != "\n":
        _t = _t.replace("\n", eol)
    with open(path, "wb") as _f:
        _f.write(_t.encode("utf-8"))


def _mk(tmp_path, patches, eol="\r\n", wrap=None):
    p = _rel(tmp_path)
    _write_doc(p, wrap(patches) if wrap else patches, eol=eol)
    return p


def _read(path):
    with open(path, encoding="utf-8") as _f:
        return json.load(_f)


def _md5(path):
    with open(path, "rb") as _f:
        return hashlib.md5(_f.read()).hexdigest()


# ------------------------------------------------------- ① 派生与比率滚动
def test_01_frozen_stock_is_derived_and_rate_moves(tmp_path):
    p = _mk(tmp_path, _stock())
    _before = _read(p)
    assert all(_PF not in _x for _x in _before), "夹具前提：存量确实停在未派生"

    r = vr.recompute(path=p, now=1000.0)
    assert r["state"] == "ok", r
    assert r["changed"] == 6
    assert r["total"] == 6
    assert r["verifiable_count"] == 3
    assert r["verifiable_rate"] == 0.5
    assert r["real_fix_rate"] == round(2 / 3, 4)

    _after = _read(p)
    assert [_x[_PF] for _x in _after] == [True, None, False, None, True, None]
    # 不可判定永远保留 None，绝不为凑布尔折叠成 False
    assert _after[1][_PF] is None and _after[3][_PF] is None


def test_02_uptodate_writes_nothing(tmp_path, monkeypatch):
    p = _mk(tmp_path, _stock())
    assert vr.recompute(path=p, now=1000.0)["state"] == "ok"

    _calls = []

    def _spy(*a, **k):
        _calls.append(a)
        return True

    monkeypatch.setattr(vr, "_atomic_write", _spy)
    _h = _md5(p)
    r = vr.recompute(path=p, now=1000.0 + vr.RECOMPUTE_MIN_INTERVAL_SEC + 1)
    assert r["state"] == "no_change", r
    assert r["changed"] == 0
    assert _calls == [], "无变化却写了盘（读多写少被破坏）"
    assert _md5(p) == _h


def test_02b_recompute_is_idempotent(tmp_path):
    """跑两次一个字节都不变（不抖动 = 不会每 6h 造一次无意义写）。"""
    p = _mk(tmp_path, _stock())
    assert vr.recompute(path=p, now=1.0)["state"] == "ok"
    _h1 = _md5(p)
    vr.reset_recompute_throttle()
    assert vr.recompute(path=p, now=2.0)["state"] == "no_change"
    assert _md5(p) == _h1


# ----------------------------------------------------------- ② 节流与预演
def test_03_throttled_inside_window_returns_before_reading(tmp_path):
    p = _mk(tmp_path, _stock())
    _t0 = 5000.0
    assert vr.recompute(path=p, now=_t0)["state"] == "ok"
    os.remove(p)  # 文件都没了还能判 throttled ⇒ 证明节流在**读盘之前**
    r = vr.recompute(path=p, now=_t0 + 60.0)
    assert r["state"] == "throttled"
    assert "距上次" in r["reason"]
    assert not os.path.isfile(p), "文件已被删掉还能判 throttled ⇒ 确实没去读盘"


def test_04_throttle_expires(tmp_path):
    p = _mk(tmp_path, _stock())
    _t0 = 5000.0
    assert vr.recompute(path=p, now=_t0)["state"] == "ok"
    r = vr.recompute(path=p, now=_t0 + vr.RECOMPUTE_MIN_INTERVAL_SEC + 1)
    assert r["state"] != "throttled", r


def test_05_dry_run_reports_change_but_keeps_file(tmp_path):
    p = _mk(tmp_path, _stock())
    _h = _md5(p)
    r = vr.recompute(path=p, now=1.0, dry_run=True)
    assert r["state"] == "dry_run", r
    assert r["changed"] == 6, "预演必须如实报「会变几条」（否则就是假读数）"
    assert r["verifiable_rate"] == 0.5
    assert _md5(p) == _h
    assert vr._LAST_TS == 0.0, "预演不得占用节流窗口"


# --------------------------------------------------- ③ 两个失败面都留得住档
def test_06_guard_rejected_keeps_file(tmp_path, monkeypatch):
    p = _mk(tmp_path, _stock())
    _h = _md5(p)
    monkeypatch.setattr(vr, "guard_write", lambda *a, **k: False)
    r = vr.recompute(path=p, now=1.0)
    assert r["state"] == "write_rejected", r
    assert _md5(p) == _h
    assert vr._LAST_TS == 0.0, "被拒写不得标记「已算过」"


def test_07_write_failure_keeps_file(tmp_path, monkeypatch):
    p = _mk(tmp_path, _stock())
    _h = _md5(p)
    monkeypatch.setattr(vr, "_atomic_write", lambda *a, **k: False)
    r = vr.recompute(path=p, now=1.0)
    assert r["state"] == "write_failed", r
    assert _md5(p) == _h


def test_08_atomic_write_leaves_no_residue(tmp_path):
    p = _rel(tmp_path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    _write_doc(p, [_p("z")])
    assert vr._atomic_write(p, {"x": object()}) is False, "不可序列化必须返回 False"
    assert not os.path.exists(p + ".tmp_m374"), "失败留下半截临时文件"
    assert _read(p)[0]["id"] == "z", "原子写失败不许动原档"


def test_09_missing_and_unknown_structure(tmp_path):
    r = vr.recompute(root=str(tmp_path), now=1.0)
    assert r["state"] == "no_history" and "不存在" in r["reason"]
    assert vr.history_path(str(tmp_path)) == _rel(tmp_path)

    p = _rel(tmp_path)
    _write_doc(p, {"foo": 1})
    assert vr.recompute(path=p, now=1.0)["state"] == "bad_structure"
    vr.reset_recompute_throttle()
    _write_doc(p, "not-a-patch-file")
    assert vr.recompute(path=p, now=1.0)["state"] == "bad_structure"


def test_10_dict_root_and_other_keys_preserved(tmp_path):
    p = _mk(tmp_path, _stock(), wrap=lambda ps: {"version": 3, "patches": ps})
    assert vr.recompute(path=p, now=1.0)["state"] == "ok"
    _doc = _read(p)
    assert _doc["version"] == 3, "改 patches 不能顺手丢掉文档里别的键"
    assert len(_doc["patches"]) == 6
    assert _doc["patches"][0][_PF] is True


# ------------------- ④ 指纹必须覆盖 backfill 写入的全部派生字段（守门）
def test_11_fingerprint_covers_every_backfill_field():
    """`backfill` 写了第 9 个字段而指纹没盯 ⇒ 字段永远旧、永远判「没变」。

    这是本刀最容易犯且**完全静默**的错，所以钉成断言。
    """
    _one = [_p("x")]
    backfill(_one, apply=True)
    _written = set(_one[0]) - set(_p("x"))
    assert _written, "backfill 居然什么都没写？"
    _missed = _written - set(vr._DERIVED_FIELDS)
    assert not _missed, f"backfill 写了但变化指纹没盯：{sorted(_missed)}"


@pytest.mark.parametrize("field", [
    F_NO_REGRESSION, _PF, F_PF_REASON, F_SPLIT_VERSION, F_VERIFIED,
    "problem_fixed_partial", "verification_granularity", "effectiveness",
    "verification_split_reason",
])
def test_12_each_derived_field_alone_triggers_write(tmp_path, field):
    """逐个字段注入旧值 ⇒ 必须被发现并修正（单字段判别力）。"""
    p = _mk(tmp_path, _stock())
    assert vr.recompute(path=p, now=1.0)["state"] == "ok"
    _doc = _read(p)
    _good = _doc[0][field]
    assert _good != "__stale__"
    _doc[0][field] = "__stale__"
    _write_doc(p, _doc)

    vr.reset_recompute_throttle()
    r = vr.recompute(path=p, now=2.0)
    assert r["state"] == "ok", r
    assert r["changed"] == 1, f"只坏了 1 条却报 {r['changed']} 条"
    assert _read(p)[0][field] == _good


def test_12b_self_referential_verified_converges(tmp_path):
    """`verified` 既是派生输出、又是 `no_regression` 的 legacy 输入（自指）。

    自指字段最怕的是「每拍都改一点」⇒ 每 6h 白写一次盘。这里钉住收敛：
    第一拍把假值改成规范值，第二拍必须 `no_change`。
    """
    _ps = [_p("s1", verified="whatever-truthy"), _p("s2", verified=0)]
    p = _mk(tmp_path, _ps)
    assert vr.recompute(path=p, now=1.0)["state"] == "ok"
    with open(p, encoding="utf-8") as _f:
        _doc = json.load(_f)
    assert _doc[0][F_VERIFIED] is True
    assert _doc[1][F_VERIFIED] is False
    _h = _md5(p)
    vr.reset_recompute_throttle()
    assert vr.recompute(path=p, now=2.0)["state"] == "no_change"
    assert _md5(p) == _h, "自指字段没收敛（写盘死循环）"


# ------------------------------------------- ⑤ 判据/目标值未动、不碰活体
def test_13_result_identical_to_direct_backfill(tmp_path):
    """重算只是 backfill 的调度者：结果必须与直接调它**逐字段一致**。"""
    _ref = copy.deepcopy(_stock())
    _st = backfill(_ref, apply=True)
    p = _mk(tmp_path, _stock())
    r = vr.recompute(path=p, now=1.0)
    assert r["verifiable_rate"] == _st["verifiable_rate"]
    assert r["real_fix_rate"] == _st["real_fix_rate"]
    assert _read(p) == _ref


def test_14_derive_rules_and_target_untouched():
    assert derive_problem_fixed({"reprobe_verdict": "true_pass",
                                 "reprobe_baseline_hits": 1})[0] is True
    assert derive_problem_fixed({"reprobe_verdict": "true_pass",
                                 "reprobe_baseline_hits": 0})[0] is None
    assert derive_problem_fixed({"reprobe_verdict": "false_pass"})[0] is False
    assert derive_problem_fixed({"reprobe_verdict": "ineffective"})[0] is False
    assert derive_problem_fixed({"reprobe_verdict": "partial_fix"})[0] is None
    assert derive_problem_fixed({"reprobe_verdict": "verification_failed"})[0] is None
    assert derive_problem_fixed({"baseline_errors": 5, "post_apply_errors": 1})[0] is True
    assert derive_problem_fixed({"baseline_errors": 0, "post_apply_errors": 0})[0] is None
    assert [n for n in dir(vr) if "TARGET" in n.upper()] == [], "本模块不许藏目标值"


def test_15_pending_patches_never_touched(tmp_path):
    p = _mk(tmp_path, _stock())
    _pend = os.path.join(str(tmp_path), "data", "patches", "pending_patches.json")
    _write_doc(_pend, [_p("z1")])
    _h = _md5(_pend)
    assert vr.recompute(path=p, now=1.0)["state"] == "ok"
    assert _md5(_pend) == _h, "重算面动了 pending 活体"
    assert vr.HISTORY_REL.replace("\\", "/").endswith(
        "data/patches/patch_history.json")

    # 代码面（排注释/docstring，铁律227）不许出现 pending_patches
    with open(vr.__file__, encoding="utf-8") as _f:
        _src = _f.read()
    _code = " ".join(
        _t.string for _t in tokenize.generate_tokens(io.StringIO(_src).readline)
        if _t.type not in (tokenize.COMMENT, tokenize.STRING))
    assert "pending_patches" not in _code


# ------------------------------------------ ⑥ 行尾守恒 / 结构保真 / 接线
def test_16_line_endings_preserved(tmp_path):
    _crlf = _mk(tmp_path, _stock(), eol="\r\n")
    assert vr.recompute(path=_crlf, now=1.0)["state"] == "ok"
    with open(_crlf, "rb") as _f:
        _b = _f.read()
    assert _b.count(b"\r\n") > 0
    assert _b.count(b"\n") == _b.count(b"\r\n"), "CRLF 档被写成了裸 LF"

    _lf = os.path.join(str(tmp_path), "lf", "h.json")
    _write_doc(_lf, _stock(), eol="\n")
    vr.reset_recompute_throttle()
    assert vr.recompute(path=_lf, now=1.0)["state"] == "ok"
    with open(_lf, "rb") as _f:
        _b2 = _f.read()
    assert _b2.count(b"\r\n") == 0, "LF 档被写成了 CRLF"


def test_17_byte_identical_serialization(tmp_path):
    """已是最新的档回写一遍必须**字节相同**（序列化形态与原档同款）。"""
    p = _mk(tmp_path, _stock(), eol="\r\n")
    assert vr.recompute(path=p, now=1.0)["state"] == "ok"
    _h = _md5(p)
    _doc = _read(p)
    assert vr._atomic_write(p, _doc) is True
    assert _md5(p) == _h


def test_18_main_joint_is_not_dead_code():
    with open(os.path.join(_REPO, "main.py"), encoding="utf-8") as _f:
        _src = _f.read()
    assert "from nucleus.evolution import verifiable_recompute" in _src, "没接线"
    _i = _src.find("_vr.maybe_recompute(")
    assert _i >= 0, "生产件没人调 = 死代码"
    _blk = _src[max(0, _i - 500):_i + 200]
    assert "try:" in _blk and "except Exception" in _blk, "主节拍调用必须兜异常"


def test_19_maybe_recompute_never_raises(tmp_path, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(vr, "recompute", _boom)
    r = vr.maybe_recompute(str(tmp_path))
    assert r["state"] == "error"
    assert "boom" in r["reason"]
