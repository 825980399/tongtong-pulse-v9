# -*- coding: utf-8 -*-
"""383 刀4：O3 判死条目 OPEN 超时 ⇒ stale。

## 这一刀最容易改错的地方

`stale` **不是结案**。它不进 `TERMINAL_STATUSES`，因此：

| 判据 | 对 stale 的值 | 用途 |
|---|---|---|
| `_is_open`（未结案） | **True** | `prune` 用它保护「超期但未结案也留」 |
| `_m383_is_actionable`（可推进） | **False** | 限额与全局截断排序用它「让位」 |

如果把 stale 塞进 `TERMINAL_STATUSES`，会同时踩两个坑：
  ① 结案率口径被污染（烛微口径：status≠verified/wontfix 即未结案）；
  ② `POOL_RETAIN_DAYS` 也是 7 天 ⇒ 转 stale 当天就被 prune 当结案删掉。

本文件用 `test_m383_d4_stale_is_not_terminal` 和
`test_m383_d4_stale_not_actionable_but_still_open` 两条钉死这个分离。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus import audit_problem_pool as ap  # noqa: E402

NOW = 1_800_000_000.0
DAY = 86400.0


def _mk(issue_type="dead_code", status="open", age_days=30.0):
    return {
        "dedup_key": "%s|%s|%s" % (issue_type, status, age_days),
        "issue_type": issue_type,
        "status": status,
        "first_seen_ts": NOW - age_days * DAY,
        "status_history": [{"status": "open", "at": NOW - age_days * DAY}],
    }


# ------------------------------------------------------- ① 核心：三条全中才转
def test_m383_d4_expired_o3_open_becomes_stale():
    """★核心：判死族 + open + 超 7 天 ⇒ 转 stale，并写 status_history。"""
    _rows = [_mk("dead_code", "open", 8)]
    _out, _n = ap.stale_o3_open_entries(_rows, now=NOW)
    assert _n == 1
    assert _out[0]["status"] == "stale"
    assert _out[0]["status_history"][-1]["status"] == "stale"
    assert "383刀4" in _out[0]["status_history"][-1]["note"]


@pytest.mark.parametrize("issue_type,status,age_days,reason", [
    ("dead_code", "open", 3, "未超期"),
    ("dead_code_candidate", "open", 6.9, "差 0.1 天"),
    ("silent_exception", "open", 30, "非 O3 判死族"),
    ("dead_code", "claimed", 30, "有人在推进"),
    ("dead_code", "fixed", 30, "已修"),
    ("dead_code", "stale", 40, "已是 stale"),
])
def test_m383_d4_other_cases_untouched(issue_type, status, age_days, reason):
    """反控：只要有一条不满足就不动（六种不满足各一例）。"""
    _rows = [_mk(issue_type, status, age_days)]
    _out, _n = ap.stale_o3_open_entries(_rows, now=NOW)
    assert _n == 0, reason
    assert _out[0]["status"] == status, reason


def test_m383_d4_mixed_batch_counts_only_eligible():
    """混合批次：只数该转的那几条。"""
    _rows = [_mk("dead_code", "open", 8), _mk("dead_code_candidate", "open", 30),
             _mk("dead_code", "open", 1), _mk("silent_exception", "open", 99),
             _mk("dead_code", "claimed", 99)]
    _out, _n = ap.stale_o3_open_entries(_rows, now=NOW)
    assert _n == 2
    assert [r["status"] for r in _out] == ["stale", "stale", "open", "open", "claimed"]


def test_m383_d4_idempotent():
    """幂等：同一批跑两次，第二次 0 条（可安全每拍调用）。"""
    _rows = [_mk("dead_code", "open", 8)]
    ap.stale_o3_open_entries(_rows, now=NOW)
    _out2, _n2 = ap.stale_o3_open_entries(_rows, now=NOW)
    assert _n2 == 0
    assert len(_out2[0]["status_history"]) == 2, "不该重复追加 history"


def test_m383_d4_explicit_ttl_overrides():
    """TTL 可显式传（测试/回放用），不依赖 config。"""
    _rows = [_mk("dead_code", "open", 3)]
    _out, _n = ap.stale_o3_open_entries(_rows, now=NOW, ttl_days=2)
    assert _n == 1 and _out[0]["status"] == "stale"


# ------------------------------------------- ② stale 不是结案（本刀的核心纪律）
def test_m383_d4_stale_is_not_terminal():
    """★★最关键：stale **不许**进 TERMINAL_STATUSES。

    进了就会被 prune 当结案删掉（POOL_RETAIN_DAYS 同样是 7 天），
    还会污染「status≠verified/wontfix 即未结案」的结案率口径。
    """
    assert ap.STATUS_STALE in ap.ALL_STATUSES
    assert ap.STATUS_STALE not in ap.TERMINAL_STATUSES
    assert ap.TERMINAL_STATUSES == (ap.STATUS_VERIFIED, ap.STATUS_WONTFIX)


def test_m383_d4_stale_not_actionable_but_still_open():
    """★两个语义必须分开：stale 让位（actionable=False）但不结案（is_open=True）。"""
    _e = _mk("dead_code", "open", 8)
    ap.stale_o3_open_entries([_e], now=NOW)
    assert ap._m383_is_actionable(_e) is False, "stale 应让位"
    assert ap._is_open(_e) is True, "stale 仍是未结案（不能被 prune 当结案删）"


def test_m383_d4_prune_keeps_expired_stale():
    """反控：超期 stale 仍被 prune 保留 —— 这是「不结案」的直接后果。"""
    _e = _mk("dead_code", "open", 400)      # 远超 retain 7 天
    ap.stale_o3_open_entries([_e], now=NOW)
    _kept, _dropped = ap.prune([_e], now=NOW, retain_days=7)
    assert len(_kept) == 1 and _dropped == 0, "stale 被 prune 删掉了 ⇒ 说明它被当成结案"


def test_m383_d4_verified_expired_is_pruned_for_contrast():
    """对照：真正结案的超期条目才会被 prune 删（证明上面那条不是 prune 失灵）。"""
    _e = _mk("dead_code", "verified", 400)
    _kept, _dropped = ap.prune([_e], now=NOW, retain_days=7)
    assert len(_kept) == 0 and _dropped == 1


# ------------------------------------------------------------- ③ 流转表
def test_m383_d4_transitions_open_and_back():
    """open→stale 合法；stale→open 也合法（回滚通道）。"""
    assert ap._transition_verdict(ap.STATUS_OPEN, ap.STATUS_STALE) == "ok"
    assert ap._transition_verdict(ap.STATUS_STALE, ap.STATUS_OPEN) == "ok"
    assert ap._transition_verdict(ap.STATUS_STALE, ap.STATUS_WONTFIX) == "ok"
    # 不允许 stale→fixed：它本来就没人在修
    assert ap._transition_verdict(ap.STATUS_STALE, ap.STATUS_FIXED) == "illegal_transition"


def test_m383_d4_bad_status_still_rejected():
    """反控：非法状态值仍被判 bad_status（抽公共判据没放宽校验）。"""
    assert ap._transition_verdict(ap.STATUS_OPEN, "not_a_status") == "bad_status"


# ------------------------------------------------------------- ④ 开关与同源
def test_m383_d4_switch_defaults_off():
    """零回归的根：开关默认 False。"""
    assert config.ENABLE_O3_STALE_TIMEOUT is False
    assert ap._m383_o3_stale_on() is False


def test_m383_d4_default_ttl_is_seven_days():
    assert config.O3_STALE_TTL_DAYS == 7
    assert ap._m383_o3_stale_ttl_days() == 7.0


def test_m383_d4_o3_types_match_dispatch_side():
    """★防分叉：池侧的 O3 判死族必须与调度侧同一集合。"""
    from nucleus.evolution.patch_execution_dispatch import _M339_O3_ISSUE_TYPES
    assert tuple(ap._M383_O3_DEAD_TYPES) == tuple(_M339_O3_ISSUE_TYPES), (
        "池侧 %s 与调度侧 %s 不一致 ⇒ 两边对「什么是 O3 判死」的定义已分叉"
        % (ap._M383_O3_DEAD_TYPES, _M339_O3_ISSUE_TYPES))


# ------------------------------------------------------------ ⑤ 不碰盘
def test_m383_d4_pure_function_no_io(monkeypatch):
    """纯函数：不读盘、不落盘（一旦有人在里面加 load/save，这条会红）。"""
    def _boom(*_a, **_k):
        raise AssertionError("stale_o3_open_entries 不许做 IO")

    monkeypatch.setattr(ap, "load_pool", _boom)
    monkeypatch.setattr(ap, "_atomic_write_json", _boom)
    monkeypatch.setattr(ap, "set_status", _boom)
    _out, _n = ap.stale_o3_open_entries([_mk("dead_code", "open", 8)], now=NOW)
    assert _n == 1
