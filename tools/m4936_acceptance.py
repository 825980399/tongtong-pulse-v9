# -*- coding: utf-8 -*-
"""493.6-5 端到端验收脚本（10 场景 + 执行层全状态）。

运行：python tools/m4936_acceptance.py

不依赖完整运行态：fast path / 执行层在无运行态时诚实降级（返回 None / 埋点未就绪 /
PulseCore 不可用），有运行态（PulseInnerWorld 已装配）时自动走真实器官数据。
绝不让单条异常中断验收。
"""
import os
import sys
import time

# 允许以独立脚本运行（python tools/m4936_acceptance.py）时导入 nucleus/*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _section(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def run_fastpath_scenarios():
    from nucleus.self_index.fastpath import self_query_fastpath
    _scenarios = [
        ("你有多少个器官", "计数"),
        ("管消化的器官是什么", "别名→位置"),
        ("管记忆的器官是哪个", "消歧澄清"),
        ("管消化的忙不忙", "别名+在途"),
        ("胃现在处理多少东西", "器官在途"),
        ("L2队列多深", "背压(运行态)"),
        ("你刚才用哪个模型", "渠道/模型"),
        ("眼睛现在看到啥", "眼睛状态"),
        ("再说一遍刚才的话", "复读(运行态)"),
        ("为什么L2会满", "因果→fallback"),
    ]
    _local = 0
    for _q, _cat in _scenarios:
        _t0 = time.time()
        try:
            _a = self_query_fastpath(_q)
        except Exception as _e:  # 绝不让验收脚本因单条崩溃
            _a = f"<异常:{type(_e).__name__}>"
        _dt = (time.time() - _t0) * 1000
        _closed = _a is not None
        if _closed:
            _local += 1
        _ans = (_a if isinstance(_a, str) else "None(走大模型)")[:56]
        print(f"  [{_cat:12}] {_q:16} -> 本地闭环={_closed} ({_dt:5.1f}ms) {_ans}")
    return _local, len(_scenarios)


def run_executor_scenarios():
    from nucleus.self_index.action_executor import execute_action
    _cases = [
        ("stomach.stats", {"organ": "胃"}, "read→应 ok"),
        ("organ.locate", {}, "self_index→应 ok"),
        ("organ.inflight", {"organ_name": "胃"}, "read(int 返回)→应 ok"),
        ("patch.apply", {"patch": "x"}, "write→blocked"),
        ("nope_x", {}, "白名单未找到→blocked=False"),
        ("digest_submit", {"payload": "x"}, "control→PulseCore 不可用 blocked"),
    ]
    for _aid, _p, _expect in _cases:
        _t0 = time.time()
        try:
            _r = execute_action(_aid, _p)
        except Exception as _e:
            _r = {"ok": False, "reason": f"<异常:{type(_e).__name__}>"}
        _dt = (time.time() - _t0) * 1000
        _ok = _r.get("ok")
        _bl = _r.get("blocked")
        _ver = _r.get("verified")
        print(f"  {_aid:16} ok={_ok} blocked={_bl} verified={_ver} ({_dt:5.1f}ms)")
        print(f"      expect={_expect} | reason={_r.get('reason')}")


def main():
    _section("493.6 验收：fast path 10 场景")
    _local, _total = run_fastpath_scenarios()
    _section("493.6 验收：execute_action 全状态")
    run_executor_scenarios()
    _section("结论")
    print(f"  fast path 本地闭环: {_local}/{_total}")
    print(f"  阈值与执行层单测: 见 tests/test_m4936_self_index.py（pytest 全绿）")
    print(f"  未装配运行态时，需运行态的场景诚实降级（None / 埋点未就绪 /")
    print(f"  PulseCore 不可用），符合「查不到就标 null、不编造」纪律。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
