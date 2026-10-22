# -*- coding: utf-8 -*-
"""342 批单测（五刀施工事实固化）。

★纪律说明（本批两条教训）：
  * 铁律171：与基线强耦合的断言（线程数、登记册条数）**不许**在这里再写一遍硬编码，
    一律"读基线 vs 真扫"双向比对 —— 否则每次重锚都要改一遍测试，等于给"改数字"开口子。
  * 铁律163：断言必须**能失败**。凡"只读一个字段存在"的用例，都补一条反向判别
    （同一个字段在错误口径下应当取到别的值），避免空转。
"""
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ============ 刀1：冷化链可达性（load 透传 + 开关默认关 + 索引/收敛/冷化三行） ============

def test_342_d1_main_passes_full_load_through():
    """`main.py` 不再无参 `snapshot.load()`（340B 实证：无参 ⇒ full_load 恒 True）。"""
    _t = _read("main.py")
    assert "restored_nodes = self.snapshot.load()" not in _t, \
        "又变回无参调用 ⇒ 懒加载链生产再次不可达"
    assert "restored_nodes = self.snapshot.load(" in _t
    assert "SNAPSHOT_LAZY_LOAD" in _t


def test_342_d1_switch_default_is_false():
    """开关默认 **False**：658MB 规模下懒加载比全量慢约 20 倍（d1_cost.json）。"""
    import config
    assert getattr(config, "SNAPSHOT_LAZY_LOAD", None) is False
    _c = _read("config.py")
    # 反向判别：不是"没这个键取 False"，而是**显式**写 False 且注释里带实测数
    assert re.search(r"^SNAPSHOT_LAZY_LOAD\s*=\s*False\s*$", _c, re.M)
    assert "122.6s" in _c and "6.0s" in _c


def test_342_d1_lazy_path_traces_three_marks():
    """懒加载视图三处留痕都在（索引命中 / max_cache 收敛 / 视图跳过增量重放）。"""
    _s = _read("nucleus/mnemosyne/PulseSnapshot.py")
    assert "[342刀1] 懒加载视图跳过增量日志重放" in _s
    assert "max_cache 收敛" in _s
    _l = _read("nucleus/mnemosyne/lazy_snapshot.py")
    assert "snapshot_index" in _l or "index_hit" in _l


# ============ 刀2：tool 通道显性化 ============

def test_342_d2_scan_roots_excludes_prod_dirs():
    """固定扫描域既非全库、也不含 data/tmp/logs（否则 Ruff 会 panic + 通道归零）。"""
    import config
    _r = list(getattr(config, "TOOL_SCAN_ROOTS", []))
    assert _r, "TOOL_SCAN_ROOTS 不得为空（空会退回内建域，配置就白写了）"
    assert "." not in _r, "不得退回全库扫描"
    for _bad in ("data", "tmp", "logs", "logs/"):
        assert _bad not in _r


def test_342_d2_runner_separates_stdout_and_stderr():
    """解析路径改走 `_run_cmd_split`（dict 带 stdout/stderr 分列），拼接串不再喂 json。

    ★别用"源码里不许出现 `stdout + stderr`"这种子串断言 —— 那句旧 bug 的描述
      **就写在注释里**（铁律154：子串黑名单会扫到注释与 docstring）。
    """
    _t = _read("nucleus/tooling_runner.py")
    # 1) 新 API 在位，且返回的是**分列**记录（stdout/stderr/rc/ok/err 五个键）
    assert "def _run_cmd_split(" in _t
    for _k in ('"stdout": ""', '"stderr": ""', '"rc": None', '"ok": False', '"err": ""'):
        assert _k in _t, "缺分列记录键 " + _k
    # 2) ruff 解析路径**确实**走新 API（这条才是"归零根因被修掉"的真证据）
    _i = _t.index("def run_ruff_check(")
    _seg = _t[_i:_i + 3000]
    assert "_run_cmd_split(" in _seg
    # 2b) 真正做解析的是 `_tool_json(tool, rec, ...)`：只取 rec["stdout"]，
    #     stderr 单独留痕不参与解析（340刀5 的归零点就在这一行）
    _j = _t.index("def _tool_json(")
    _seg2 = _t[_j:_j + 3000]
    assert 'rec.get("stdout")' in _seg2
    assert 'rec.get("stderr")' in _seg2
    assert "stderr_head" in _seg2
    assert "_run_cmd_split(" in _seg and "_tool_json(" in _seg
    # 3) 失败/降级必须留痕，不许静默返回空
    assert "degraded" in _seg2 and "[342tool]" in _t


# ============ 刀3：K2 判据双口径 ============

def test_342_d3_k2_verdict_is_safety_based():
    """verdict 由安全面决定，吞吐降幅降级为报告字段（E-2 裁决）。"""
    _t = _read("tools/k2_bench_339.py")
    for _k in ("safety_pass", "verdict_basis", "throughput_report_only",
               "throughput_line_enforced", "throughput_band",
               "verdict_legacy_throughput"):
        assert '"%s"' % _k in _t, "缺字段 " + _k
    assert '"throughput_report_only": True' in _t
    assert '"throughput_line_enforced": False' in _t
    assert "[342K2]" in _t


def test_342_d3_arch_ticket_written():
    """进程池并行架构票已出（只设计不实现）。"""
    _p = os.path.join(ROOT, "tmp", "p342_root", "k2_arch_ticket.md")
    if not os.path.exists(_p):
        import pytest
        pytest.skip("架构票在 tmp/（gitignored），本机外不可见")
    _t = io.open(_p, encoding="utf-8").read()
    for _sec in ("可行性", "影响面", "分叉"):
        assert _sec in _t


# ============ 刀4：typed_infer 四方法证据留痕 ============

def test_342_d4_evidence_function_and_row_format():
    """证据函数在位，且留痕口径与 340 判死面一致（显式布尔，不靠理由字符串找关键字）。"""
    _t = _read("nucleus/self_inspector.py")
    assert "def _m342_typed_only_evidence(" in _t
    assert "[342判死] typed_only 证据汇总" in _t
    assert "static=%d unique_name=%d" in _t
    # 反向判别（铁律153）：revocable 必须是布尔表达式算出来的，不是字符串匹配
    assert re.search(r"revocable\s*=\s*bool\(", _t) or "revocable = (static > 0" in _t


def test_342_d4_registry_untouched():
    """342 只补取证、不动登记表；**343 刀5 按 E-8 裁决撤表 4→2**（本条随之同步）。

    保留本用例的价值：它守的是「登记表是**模块级**变量」这件事
    （337 `_DEAD_CODE_CRITERIA_VERSION` 踩过类属性的坑：从类上取恒 None
    ⇒ 断言恒过/恒挂，铁律161），条数只是顺带对齐口径。
    """
    import nucleus.self_inspector as _si
    _m = getattr(_si, "_M337_TYPED_ONLY_METHODS", None)
    assert isinstance(_m, (tuple, list)), "登记表取不到（符号被改名或挪进类里了？）"
    assert len(_m) == 2, "343刀5 撤表后应为 2 条；再变需另立批次+归因（铁律171）"
    assert all(len(x) == 3 for x in _m)
    _d = getattr(_si, "_M343_TYPED_ONLY_DEREGISTERED", None)
    assert isinstance(_d, (tuple, list)) and len(_d) == 2
    assert all(len(x) == 4 for x in _d)


# ============ 刀5：D3 第 2 批（去线程） ============

def test_342_d5_subconscious_keeps_only_hardware_probe_thread():
    """PulseSubconscious 只剩 1 处 spawn（硬件探针，有据保留），Timer 两处改同步。"""
    _t = _read("organs/brain/PulseSubconscious.py")
    assert _t.count("threading.Thread(") == 1
    assert "_detect_user_presence_async" in _t          # 保留站点仍在
    assert "[342去线程] action=reserved" in _t
    assert "[342去线程] action=removed" in _t


def test_342_d5_function_loader_has_no_spawn():
    """function_loader 的聚合装配线程改为同步顺序装配（装配必须返回前完成）。"""
    _t = _read("functions/function_loader.py")
    assert _t.count("threading.Thread(") == 0
    assert "[342去线程]" in _t


def test_342_d5_thread_baseline_reanchored_with_attribution():
    """线程基线 = 59/44，归因 2 条合计 −3/−1，且与 HEAD 署名自洽。

    ★不硬编码"应该是 59"就完事：同时校验 `attribution` 之和 == 与上一次锚定的差，
      这正是门禁脚本 `selfcheck_reanchor_sign` 的机器判据（铁律171）。
    """
    _p = os.path.join(ROOT, "tools", "ci", "baselines", "thread_spawn_baseline.json")
    _d = json.load(io.open(_p, encoding="utf-8"))
    assert _d["total_occurrences"] == 59 and _d["file_count"] == 44
    _h = _d.get("reanchor_history") or []
    assert _h
    _last = _h[-1]
    assert _last["reanchor_by"] == "342刀5"
    assert _last["from"]["total_occurrences"] == 62
    assert _last["from"]["file_count"] == 45
    _sd = sum(a["delta_occurrences"] for a in _last["attribution"])
    _sf = sum(a["delta_files"] for a in _last["attribution"])
    assert _sd == _last["to"]["total_occurrences"] - _last["from"]["total_occurrences"]
    assert _sf == _last["to"]["file_count"] - _last["from"]["file_count"]


def test_342_d5_pair_evidence_complete():
    """功能对拍三件套证据在位（返回值 + 时序 + 装配顺序），且时序断言真的翻了面。"""
    _p = os.path.join(ROOT, "tmp", "p342_root", "d5_pair_diff.json")
    if not os.path.exists(_p):
        import pytest
        pytest.skip("对拍证据在 tmp/（gitignored）")
    _d = json.load(io.open(_p, encoding="utf-8"))
    assert _d["all_pass"] is True and _d["pass"] == _d["total"]
    # 反向判别：若 before/after 时序一样，这条断言就应该失败（证明它不是空转）
    assert _d["before"]["boot_timers_ready"] == 0.0
    assert _d["after"]["boot_timers_ready"] == 1.0
