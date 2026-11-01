# -*- coding: utf-8 -*-
"""418 刀2 单测：渠道负载均衡排序。

四组判据：①选路（最空闲的先打）②熔断（沉底但不删）③免费优先付费兜底
④开关（关闭逐字回旧顺序）。另加两条：读数缺失不许当「不可用」、付费口径对齐。
"""
import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.llm import channel_balance as CB  # noqa: E402


# ============================================================ 假件
class _Sem:
    def __init__(self, mx, used=0):
        self.max_value = mx
        self._used = used

    def available(self):
        return max(0, self.max_value - self._used)

    @property
    def in_use(self):
        return self._used


class _Mgr:
    def __init__(self, spec):
        self._spec = spec
        self.enabled = True

    def get_semaphore(self, name):
        _s = self._spec.get(name)
        return None if _s is None else _Sem(*_s)


class _Health:
    def __init__(self, succ=None, fails=None, broken=None):
        self._succ = succ or {}
        self._fails = fails or {}
        self._broken = set(broken or ())

    def success_rate(self, name):
        return self._succ.get(name, 1.0)

    def consecutive_fails(self, name):
        return self._fails.get(name, 0)

    def is_available(self, name):
        return name not in self._broken


class _Quota:
    def __init__(self, rem=None):
        self._rem = rem or {}

    def get_remaining_ratio(self, name):
        return self._rem.get(name)


def _rows(*names):
    return [{"name": n, "priority": i + 1} for i, n in enumerate(names)]


def _names(rows):
    return [r.get("name") for r in rows]


FREE = ("ch-a", "ch-b", "ch-c")
PAID = {"deepseek", "advanced"}


# ============================================================ ① 选路：最空闲的先打
def test_10_busy_first_channel_gets_skipped():
    """priority=1 的那条并发吃满 ⇒ 起点必须让给有空位的 ch-b。"""
    _mgr = _Mgr({"ch-a": (1, 1), "ch-b": (4, 0), "ch-c": (2, 0)})
    _new, _info = CB.order_by_load(_rows(*FREE), manager=_mgr, quota=_Quota(),
                                   health=_Health(), paid_names=PAID)
    assert _info["applied"] is True
    assert _info["moved"] is True
    assert _names(_new)[0] == "ch-b", _names(_new)
    assert _names(_new)[1] == "ch-c"
    assert _names(_new)[2] == "ch-a"          # 满的排最后（仍在免费组内，不删）
    assert _info["rows"][0]["headroom"] == 1.0


def test_11_headroom_ratio_drives_score():
    """都是「有空位」时，按余量**比例**排（4/10 应排在 1/2 之后）。"""
    _mgr = _Mgr({"ch-a": (2, 1), "ch-b": (10, 4)})   # 0.5 vs 0.6
    _new, _ = CB.order_by_load(_rows("ch-a", "ch-b"), manager=_mgr, quota=_Quota(),
                               health=_Health(), paid_names=PAID)
    assert _names(_new) == ["ch-b", "ch-a"]
    _mgr2 = _Mgr({"ch-a": (2, 1), "ch-b": (10, 8)})  # 0.5 vs 0.2
    _new2, _ = CB.order_by_load(_rows("ch-a", "ch-b"), manager=_mgr2, quota=_Quota(),
                                health=_Health(), paid_names=PAID)
    assert _names(_new2) == ["ch-a", "ch-b"]


def test_12_success_rate_breaks_headroom_tie():
    """余量相同 ⇒ 成功率高的先打。"""
    _mgr = _Mgr({"ch-a": (4, 0), "ch-b": (4, 0)})
    _h = _Health(succ={"ch-a": 0.2, "ch-b": 0.9})
    _new, _ = CB.order_by_load(_rows("ch-a", "ch-b"), manager=_mgr, quota=_Quota(),
                               health=_h, paid_names=PAID)
    assert _names(_new) == ["ch-b", "ch-a"]


def test_13_quota_remaining_breaks_headroom_tie():
    """余量相同 ⇒ 剩余额度多的先打（阿里云两条同 Key 但额度独立计数）。"""
    _mgr = _Mgr({"ch-a": (4, 0), "ch-b": (4, 0)})
    _q = _Quota({"ch-a": 0.05, "ch-b": 0.9})
    _new, _ = CB.order_by_load(_rows("ch-a", "ch-b"), manager=_mgr, quota=_q,
                               health=_Health(), paid_names=PAID)
    assert _names(_new) == ["ch-b", "ch-a"]


def test_14_stable_tie_break_keeps_priority_order():
    """三因子完全打平 ⇒ 必须保持传入顺序（= priority 升序），不许抖动。"""
    _mgr = _Mgr({n: (4, 0) for n in FREE})
    _new, _info = CB.order_by_load(_rows(*FREE), manager=_mgr, quota=_Quota(),
                                   health=_Health(), paid_names=PAID)
    assert _names(_new) == list(FREE)
    assert _info["moved"] is False


def test_15_all_busy_keeps_order():
    """全满 ⇒ 顺序不动（此时切给谁都没用，交给调用侧的「等一轮再试」）。"""
    _mgr = _Mgr({n: (1, 1) for n in FREE})
    _new, _info = CB.order_by_load(_rows(*FREE), manager=_mgr, quota=_Quota(),
                                   health=_Health(), paid_names=PAID)
    assert _names(_new) == list(FREE)
    assert all(r["headroom"] == 0.0 for r in _info["rows"])


# ============================================================ ② 熔断：沉底但不删
def test_20_broken_channel_sinks_but_survives():
    """熔断中的渠道排到免费组末尾，**且仍在列表里**（调用侧靠它留痕/半开重试）。"""
    _mgr = _Mgr({n: (4, 0) for n in FREE})
    _h = _Health(broken=["ch-a"])
    _new, _info = CB.order_by_load(_rows(*FREE), manager=_mgr, quota=_Quota(),
                                   health=_h, paid_names=PAID)
    assert "ch-a" in _names(_new), "熔断渠道被删掉了（应由调用侧跳过，不该在这里消失）"
    assert _names(_new)[-1] == "ch-a"
    assert any(r["broken"] for r in _info["rows"])
    assert min(r["score"] for r in _info["rows"]) == CB.LB_SCORE_BROKEN


def test_21_consecutive_fails_recorded_in_why():
    """连续失败次数要进 `why`（运维要能从留痕看到「为什么它被排后」）。

    ★判据只钉 `why` 与排序结果，**不钉「fails 直接扣分」**：真实
      `ChannelHealthTracker` 里连续失败必然同时拉低滑动窗口成功率，
      两个读数不是独立变量。用「fails=2 但成功率 1.0」的假件去要求降序，
      等于在测一个真实系统到不了的状态（自己造自己的基准）。
    """
    _mgr = _Mgr({"ch-a": (4, 0), "ch-b": (4, 0)})
    _h = _Health(succ={"ch-a": 0.5}, fails={"ch-a": 2})
    _new, _info = CB.order_by_load(_rows("ch-a", "ch-b"), manager=_mgr, quota=_Quota(),
                                   health=_h, paid_names=PAID)
    _why = {r["name"]: r["why"] for r in _info["rows"]}
    assert "fails:2" in _why["ch-a"]
    assert _names(_new) == ["ch-b", "ch-a"]


# ============================================================ ③ 免费优先、付费兜底
def test_30_paid_never_moves_ahead_of_free():
    """付费渠道即使余量最高、成功率最高，也**不许**排到免费前面。"""
    _mgr = _Mgr({"ch-a": (1, 1), "ch-b": (1, 1), "deepseek": (10, 0)})
    _h = _Health(succ={"deepseek": 1.0, "ch-a": 0.1, "ch-b": 0.1})
    _new, _info = CB.order_by_load(_rows("ch-a", "ch-b", "deepseek"),
                                   manager=_mgr, quota=_Quota(), health=_h,
                                   paid_names=PAID)
    assert _names(_new)[-1] == "deepseek", _names(_new)
    assert _info["paid_tail"] == ["deepseek"]
    assert set(_info["order"]) == {"ch-a", "ch-b"}


def test_31_advanced_is_paid_too():
    """`advanced` 也在付费名单里（`PAID_CHANNEL_NAMES`），必须一起垫底。"""
    _mgr = _Mgr({"ch-a": (1, 1), "advanced": (10, 0), "deepseek": (10, 0)})
    _new, _info = CB.order_by_load(_rows("advanced", "ch-a", "deepseek"),
                                   manager=_mgr, quota=_Quota(), health=_Health(),
                                   paid_names=PAID)
    assert _names(_new)[0] == "ch-a"
    assert set(_names(_new)[1:]) == {"advanced", "deepseek"}


def test_32_default_paid_names_align_with_360():
    """默认口径必须含 advanced（`PAID_CHANNEL_NAMES`），不能只认 `PAID_CHANNELS`。"""
    _got = CB._lb_paid_default()
    import config
    _want = frozenset(str(x) for x in (getattr(config, "PAID_CHANNEL_NAMES") or []))
    assert _got == _want, (_got, _want)
    assert "advanced" in _got


def test_33_no_free_group_returns_untouched():
    """候选全是付费 ⇒ 不做均衡（原样返回）。"""
    _mgr = _Mgr({"deepseek": (10, 0), "advanced": (10, 0)})
    _new, _info = CB.order_by_load(_rows("deepseek", "advanced"), manager=_mgr,
                                   quota=_Quota(), health=_Health(), paid_names=PAID)
    assert _info["applied"] is False
    assert _info["reason"] == "no_free_group"
    assert _names(_new) == ["deepseek", "advanced"]


# ============================================================ ④ 开关
def test_40_switch_off_is_verbatim_old_order(monkeypatch):
    """关开关 ⇒ 逐字回到「免费组按 priority 升序」，一个元素都不动。"""
    import config
    monkeypatch.setattr(config, "ENABLE_CHANNEL_LOAD_BALANCE", False, raising=False)
    _mgr = _Mgr({"ch-a": (1, 1), "ch-b": (4, 0), "ch-c": (2, 0)})
    _rows_in = _rows(*FREE)
    _new, _info = CB.order_by_load(_rows_in, manager=_mgr, quota=_Quota(),
                                   health=_Health(), paid_names=PAID)
    assert _info["applied"] is False and _info["reason"] == "switch_off"
    assert [_names(_new)] == [list(FREE)]
    assert _new == _rows_in, "关开关时连对象都不该换"


def test_41_switch_default_true():
    import config
    assert getattr(config, "ENABLE_CHANNEL_LOAD_BALANCE", None) is True


def test_42_single_candidate_noop():
    _new, _info = CB.order_by_load(_rows("ch-a"), paid_names=PAID)
    assert _info["reason"] == "too_few" and _info["applied"] is False


# ============================================================ ⑤ 读数缺失 ≠ 不可用
def test_50_unknown_readings_are_one_not_zero():
    """健康度/额度/信号量全取不到 ⇒ 三因子必须是 1.0，**不许**写 0。

    写 0 的后果：一条没登记并发的新渠道会被判成「彻底不可用」而永远排最后，
    与「未知」这件事完全相反（本仓栽过「0 值当读数」的坑）。
    """
    _ld = CB.channel_load("nobody", health=None, quota=None, manager=None)
    assert _ld["headroom"] == 1.0
    assert _ld["success"] == 1.0
    assert _ld["remaining"] == 1.0
    assert _ld["score"] == 1.0
    assert "no_manager" in _ld["why"]


def test_51_unregistered_channel_counts_as_room():
    _hr, _why = CB.channel_headroom(_Mgr({}), "ghost")
    assert (_hr, _why) == (1.0, "unregistered")


def test_52_zero_max_is_not_full():
    """`max_value<=0`（配置写坏）⇒ 余量 0 但原因写明，不是「满」。"""
    _hr, _why = CB.channel_headroom(_Mgr({"x": (0, 0)}), "x")
    assert _hr == 0.0 and _why == "max_zero"


def test_53_quota_none_means_unknown_not_exhausted():
    """额度监控返回 None（不限量渠道）⇒ 按 1.0，不按 0。"""
    _ld = CB.channel_load("zhipu", health=_Health(), quota=_Quota({}), manager=None)
    assert _ld["remaining"] == 1.0


# ============================================================ ⑥ 留痕
def test_60_log_order_emits_tag_and_order(caplog):
    import logging
    _mgr = _Mgr({"ch-a": (1, 1), "ch-b": (4, 0)})
    _new, _info = CB.order_by_load(_rows("ch-a", "ch-b"), manager=_mgr,
                                   quota=_Quota(), health=_Health(), paid_names=PAID)
    with caplog.at_level(logging.INFO, logger="pulse.llm.balance"):
        CB.log_order("unit", _info)
    _txt = "\n".join(r.getMessage() for r in caplog.records)
    assert CB.LB_TAG in _txt
    assert "ch-b>ch-a" in _txt
    assert "hr=" in _txt and "ok=" in _txt and "q=" in _txt


# ============================================================ ⑦ PulseLung 接线
def _lung():
    """不起整套器官（初始化极重）：只绕过 __new__ 拿一个空壳。"""
    from organs.body.PulseLung import PulseLung
    _o = PulseLung.__new__(PulseLung)
    _o._channel_health = _Health()
    return _o


def test_70_lung_wires_balance():
    _o = _lung()
    assert hasattr(_o, "_m418_balance_candidates")
    _rows_in = _rows(*FREE)
    _new = _o._m418_balance_candidates(_rows_in, caller="unit")
    assert isinstance(_new, list) and len(_new) == len(_rows_in)


def test_71_lung_call_site_present_in_source():
    """调用点必须在 `_m360_order_candidates` **之后**（否则被 360 排序覆盖）。"""
    _src = open(os.path.join(ROOT, "organs", "body", "PulseLung.py"),
                encoding="utf-8").read()
    _i360 = _src.index("_candidates = self._m360_order_candidates(_candidates)")
    _i418 = _src.index("_candidates = self._m418_balance_candidates(_candidates")
    assert _i418 > _i360, "418 均衡排在 360 排序之前了 —— 会被覆盖掉"


def test_72_lung_switch_off_passthrough(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_CHANNEL_LOAD_BALANCE", False, raising=False)
    _o = _lung()
    _rows_in = _rows(*FREE)
    assert _o._m418_balance_candidates(_rows_in, caller="unit") == _rows_in


def test_73_lung_never_raises_on_broken_health():
    """健康度对象坏掉（抛异常）也不许把调用链带崩 —— 必须回落原顺序。"""
    class _Bad:
        def success_rate(self, n):
            raise RuntimeError("boom")

        def consecutive_fails(self, n):
            raise RuntimeError("boom")

        def is_available(self, n):
            raise RuntimeError("boom")

    _o = _lung()
    _o._channel_health = _Bad()
    _rows_in = _rows(*FREE)
    _out = _o._m418_balance_candidates(_rows_in, caller="unit")
    assert [r["name"] for r in _out] == list(FREE)


# ============================================================ ⑧ 行尾 / 结构
def test_80_eol_channel_balance():
    _b = open(os.path.join(ROOT, "nucleus", "llm", "channel_balance.py"), "rb").read()
    assert _b.count(b"\r\n") == _b.count(b"\n") == _b.count(b"\r")


def test_81_eol_pulselung():
    _b = open(os.path.join(ROOT, "organs", "body", "PulseLung.py"), "rb").read()
    assert _b.count(b"\r\n") == _b.count(b"\n") == _b.count(b"\r")


def test_82_weights_sum_to_one():
    assert abs((CB.LB_W0 + CB.LB_W1 + CB.LB_W2) - 1.0) < 1e-9


def test_83_public_api_exported():
    _t = ast.parse(open(os.path.join(ROOT, "nucleus", "llm", "channel_balance.py"),
                        encoding="utf-8").read())
    _all = []
    for _n in _t.body:
        if isinstance(_n, ast.Assign) and any(
                isinstance(_t2, ast.Name) and _t2.id == "__all__" for _t2 in _n.targets):
            _all = [e.value for e in _n.value.elts]
    for _nm in ("order_by_load", "channel_load", "channel_headroom", "log_order", "LB_TAG"):
        assert _nm in _all, _nm


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
