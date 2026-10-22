# -*- coding: utf-8 -*-
"""343 批（O3 审批死链 · 域过滤 · 单价四态 · 释放心跳 · 撤表）单测。

五刀各自的**判据级**单测（不只测"函数能跑"，要测"判据能失败"）：
  刀1 E-12：O3 判死候选不再被 284刀5 误作废；普通条目/真错配**照旧作废**
  刀2 E-10：域外 Traceback 不建补丁且留痕；关掉开关时域外会被收进来
  刀3      ：free(0.0) 与 unpriced(None) 分账；三本分账条数不漏
  刀4      ：心跳档位随 heap 变化；开关关闭即 disabled
  刀5 E-8  ：登记表 4→2，撤表台账无交集
"""
import json
import os

import config

# ══════════════ 刀1：O3 审批死链（E-12） ══════════════

_O3_ROW = {
    "id": "o3_t_1", "file": "nucleus/CompanionBridge.py", "method": "handshake",
    "line": 122, "issue_type": "dead_code_candidate", "o3_dead_code": True,
    "source": "o3_open_gate", "status": "pending", "requires_confirmation": True,
    "original_code": "def handshake(self):\n    return None\n",
}


def test_343_d1_o3_def_exempt_predicate():
    from nucleus.reasoning.PatchManager import PatchManager
    assert PatchManager._m343_o3_def_exempt(_O3_ROW) is True
    # llm 类非 O3 ⇒ 不豁免（永不自动语义未动）
    assert PatchManager._m343_o3_def_exempt(
        {"source": "llm", "issue_type": "code_optimization"}) is False
    assert PatchManager._m343_o3_def_exempt({}) is False
    assert PatchManager._m343_o3_def_exempt(None) is False


def test_343_d1_converge_keeps_o3_but_voids_others(tmp_path):
    """① O3 无 modified_code **不作废**；② 普通条目仍作废；③ O3 真错配仍作废。"""
    from nucleus.reasoning.PatchManager import PatchManager
    _pm = PatchManager(str(tmp_path))
    _plain = {"id": "p1", "file": "nucleus/x.py", "method": "foo",
              "source": "llm", "issue_type": "code_optimization",
              "original_code": "def foo():\n    pass\n"}
    _bad_def = dict(_O3_ROW, id="o3_t_bad",
                    modified_code="def totally_other():\n    pass\n")
    _kept = _pm._m280_converge_pending([dict(_O3_ROW), dict(_plain), dict(_bad_def)])
    _ids = [x.get("id") for x in _kept]
    assert "o3_t_1" in _ids, "判死候选被 284刀5 误作废（E-12 回归）"
    assert "p1" not in _ids, "普通条目无 def 仍须作废（豁免不许越界）"
    assert "o3_t_bad" not in _ids, "O3 条目 def 与 method 错配是真缺陷，不许豁免"
    assert next(x for x in _kept if x["id"] == "o3_t_1")["_m343_def_exempt"] is True


def test_343_d1_approve_then_dispatch_visible(tmp_path):
    """② approve 落盘后条目仍在池；③ 派单侧判定可见（纯判定，不跑沙箱）。"""
    from nucleus.reasoning.PatchManager import PatchManager
    from nucleus.evolution.patch_execution_dispatch import _m339_o3_dispatch_ready
    _pm = PatchManager(str(tmp_path))
    with open(_pm._pending_file, "w", encoding="utf-8") as f:
        json.dump([dict(_O3_ROW)], f, ensure_ascii=False)
    _basis = {"items": {"evidence_tier": "hard", "probe_out_of_graph": 3,
                        "misjudge_sample": "ok", "god_file": False}}
    _r = _pm.approve_o3_patch(0, "human:单测", _basis)
    assert _r.get("ok") is True, _r
    _disk = json.load(open(_pm._pending_file, encoding="utf-8"))
    _hit = next((x for x in _disk if x.get("id") == "o3_t_1"), None)
    assert _hit is not None, "审批成功后条目被收敛吞掉（假成功，E-12 回归）"
    assert _hit.get("status") == "approved"
    assert _hit.get("o3_auto_dispatch_ready") is True
    assert _hit.get("requires_confirmation") is False
    assert _m339_o3_dispatch_ready(_hit) == (True, "ok")


# ══════════════ 刀2：LogAnalyzer 域过滤（E-10） ══════════════

def test_343_d2_domain_of(tmp_path):
    from nucleus.evolution.LogAnalyzer import LogAnalyzer
    _la = LogAnalyzer("D:/repo")
    _in = ["D:/repo/nucleus/a.py", "D:/repo/organs/b.py", "D:/repo/main.py",
           "tools/x.py", "functions/y.py"]
    for _p in _in:
        assert _la._m343_in_domain(_p)[0] is True, _p
    _out = ["D:/repo/tmp/p343_root/d1_iso.py", "D:/repo/tests/test_x.py",
            "D:/repo/tools/xx.bak_batch207/a.py", "C:/Python312/Lib/json/__init__.py",
            "D:/other/repo/nucleus/a.py"]
    for _p in _out:
        assert _la._m343_in_domain(_p)[0] is False, _p


def test_343_d2_scan_skips_out_of_domain(tmp_path, monkeypatch):
    """域外不进 issues（A-17 回归）；关掉开关后域外会被收进来（判别力）。"""
    from nucleus.evolution.LogAnalyzer import LogAnalyzer
    _log = tmp_path / "mix.log"
    _log.write_text(
        'Traceback (most recent call last):\n'
        '  File "D:/repo/tmp/p342_root/d1_iso.py", line 42, in _run\n'
        'KeyError: x\n'
        '  File "D:/repo/nucleus/evolution/LogAnalyzer.py", line 150, in analyze\n'
        'ValueError: boom\n', encoding="utf-8")
    _la = LogAnalyzer("D:/repo")
    _iss = {}
    _la._scan_log_file(str(_log), _iss)
    _files = {v.get("file_path") for v in _iss.values()}
    assert not any("tmp" in (x or "") for x in _files)
    assert any("nucleus" in (x or "") for x in _files)
    assert len(_la._m343_skipped) == 1     # tmp 那条被记下（留痕可审计）

    monkeypatch.setattr(config, "LOG_SCAN_DOMAIN_FILTER", False, raising=False)
    _la2 = LogAnalyzer("D:/repo")
    _iss2 = {}
    _la2._scan_log_file(str(_log), _iss2)
    assert len(_iss2) == len(_iss) + 1, "关掉过滤后域外条目应被收进来（否则判据没起作用）"


# ══════════════ 刀3：单价四态 + 三本分账 ══════════════

def test_343_d3_price_state_four_way():
    from nucleus.llm.call_recorder import _m343_price_state
    assert _m343_price_state("glm-4-flash")["state"] == "free"
    assert _m343_price_state("deepseek-v4-flash")["state"] == "priced"
    assert _m343_price_state("ep-20990101-not-exist")["state"] == "unpriced"
    assert _m343_price_state("GLM-5.2")["state"] == "unpriced"   # 值 None 占位
    assert _m343_price_state("")["state"] == "no_model"


def test_343_d3_free_counts_but_not_billed():
    """★343刀3 核心：0 单价**不计费**（金额仍 None），但归因档可分（计数用）。

    任务书更新版明令「不改 `_price<=0` 的计费语义」——把 0 元记成有值
    等于制造虚假覆盖；让空转可见靠的是 `calls_unpriced` 计数，不是假金额。
    """
    from nucleus.llm.call_recorder import _compute_cost_estimate, _m343_basis_of
    assert _compute_cost_estimate("glm-4-flash", 1000) is None      # 语义不变
    assert _m343_basis_of("glm-4-flash", 1000) == "free_zero"       # 归因可分
    assert _m343_basis_of("deepseek-v4-flash", 1000) == "priced"
    assert _m343_basis_of("deepseek-v4-flash", 0) == "no_tokens"
    assert _m343_basis_of("ep-20990101-not-exist", 1000) == "unpriced"
    assert _m343_basis_of("", 1000) == "no_model"


def test_343_d3_books_mapping():
    from nucleus.evolution.evolution_budget import _m343_book_of
    assert _m343_book_of("evolution_task") == "evolution"
    assert _m343_book_of("user_query") == "dialog"
    assert _m343_book_of("system_internal") == "tool"
    assert _m343_book_of("digestion") == "tool"
    assert _m343_book_of("weird-origin") == "other"
    assert _m343_book_of("") == "other"


def test_343_d3_coverage_and_books_count_all_calls(tmp_path):
    """金额可缺、条数不能缺：unpriced/no_model 也要进分账与 coverage。"""
    from nucleus.evolution import evolution_budget as EB
    _p = str(tmp_path / "budget.json")
    EB.record_cost(0.002, 1e9, path=_p, model="deepseek-v4-flash",
                   basis="priced", origin="evolution_task")
    EB.record_cost(None, 1e9, path=_p, model="glm-4-flash",
                   basis="free_zero", origin="user_query")
    EB.record_cost(None, 1e9, path=_p, model="ep-20990101", basis="unpriced",
                   origin="system_internal")
    EB.record_cost(None, 1e9, path=_p, model="", basis="no_model",
                   origin="evolution_task")
    _st = EB.budget_state(now=1e9, path=_p)
    _cov = _st["cost_coverage"]
    assert _cov["calls"] == 4
    # ★343刀3：free(0.0) 与 unpriced **都**进 calls_unpriced（计数不计费）
    assert (_cov["priced"], _cov["free_zero"], _cov["unpriced"],
            _cov["no_model"]) == (1, 1, 1, 1)
    assert _cov["calls_unpriced"] == 2
    assert _cov["rate_paid"] == 0.25          # 只有 1/4 有金额
    assert _cov["rate_known"] == 0.5          # 有单价键的 2/4（含免费档）
    _bk = _st["books"]
    assert _bk["evolution"]["calls"] == 2
    assert _bk["dialog"]["calls"] == 1
    assert _bk["tool"]["calls"] == 1
    assert abs(float(_st["day_used"]) - 0.002) < 1e-9
    # 跳闸双写：金额口径未超、量口径未配置 ⇒ None（不可判，绝不填 False）
    assert _st["tripped_paid"] is False
    assert _st["tripped_all"] is None
    assert _st["calls_all"] == 4 and _st["calls_unpriced"] == 2


# ══════════════ 刀4：释放决策树心跳 ══════════════

class _FakePMC:
    def __init__(self):
        self._beat_count = 10
        self._m343_rel_beat = None
        self._m343_last_release_beat = None

    def _log(self, level, msg):
        pass


def test_343_d4_heartbeat_tiers(monkeypatch):
    """档位随 heap 变化（G1/G2/G4）；节流生效。

    ★**必须注入 W1**：`_m343_release_heartbeat` 的 G0 闸门读 W1 活体判据，
      取不到就如实 `None`（G0 拦成 G0_BLOCK）。若测试依赖**环境里恰好有**
      `data/monitor/health_snapshot.json`，那它在干净检出的机器上必然失败
      （实测：worktree 无该文件 ⇒ w1=unavailable ⇒ tier 恒 G0_BLOCK）。
      判据要能脱离现场数据复现，故此处显式注入"alive"。
    """
    from organs.core.PulseMetricsCollector import PulseMetricsCollector as PMC
    monkeypatch.setattr(
        "nucleus.world_model.external_heartbeat.w1_verdict_brief",
        lambda *a, **k: {"verdict": "alive"}, raising=False)
    _f = _FakePMC()
    _r1 = PMC._m343_release_heartbeat(_f, heap_mb=300.0)
    _f._beat_count = 200
    _r2 = PMC._m343_release_heartbeat(_f, heap_mb=1600.0)
    _f._beat_count = 300
    _r3 = PMC._m343_release_heartbeat(_f, heap_mb=2500.0)
    assert _r1["status"] == _r2["status"] == _r3["status"] == "ok"
    assert _r3["tier"] == "G4_ALARM_ONLY"
    assert len({_r1["tier"], _r2["tier"], _r3["tier"]}) == 3
    # 只决策不释放：候选恒 0（心跳不接线执行）
    assert _r3["candidates_offered"] == 0
    # 同拍再调 ⇒ 节流（不重复刷日志）
    assert PMC._m343_release_heartbeat(_f, heap_mb=2500.0)["status"] == "throttled"


def test_343_d4_heartbeat_switch_off(monkeypatch):
    from organs.core.PulseMetricsCollector import PulseMetricsCollector as PMC
    monkeypatch.setattr(config, "ENABLE_RELEASE_HEARTBEAT", False, raising=False)
    _f = _FakePMC()
    assert PMC._m343_release_heartbeat(_f, heap_mb=1600.0)["status"] == "disabled"


# ══════════════ 刀5：撤表（E-8） ══════════════

def test_343_d5_registry_and_deregister_table():
    import nucleus.self_inspector as si
    assert len(si._M337_TYPED_ONLY_METHODS) == 2
    assert len(si._M343_TYPED_ONLY_DEREGISTERED) == 2
    _reg = {(f, c, m) for f, c, m in si._M337_TYPED_ONLY_METHODS}
    _dreg = {(f, c, m) for f, c, m, _b in si._M343_TYPED_ONLY_DEREGISTERED}
    assert not (_reg & _dreg)
    assert ("nucleus/reporting/report_envelope.py", "ReportEnvelope", "has_p0") in _dreg
    assert ("nucleus/reporting/report_envelope.py", "ReportEnvelope",
            "record_consume_result") in _dreg
    # 留痕函数存在且幂等（调两次不抛、只打一遍）
    si._m343_note_typed_deregister()
    si._m343_note_typed_deregister()


def test_343_d5_denominator_field_present():
    """判死分母留痕字段在位（撤表前后可对表的前提）。"""
    _src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "nucleus", "self_inspector.py"),
        encoding="utf-8").read()
    assert '"typed_only_registry"' in _src
    assert '"infer_only_total"' in _src
    assert "[343判死] 分母 hard_total=" in _src


# ══════════════ 刀1②③：自主修复豁免 / 稳定指纹 id ══════════════

_FRAG_ORIG = "    result = compute()\n    return result\n"
_FRAG_FULL = "def refresh(self):\n    result = compute()\n    return result\n"


def _iso_pm(tmp_path):
    from nucleus.reasoning.PatchManager import PatchManager
    _d = os.path.join(str(tmp_path), "iso")
    os.makedirs(os.path.join(_d, "data", "patches"), exist_ok=True)
    with open(os.path.join(_d, "data", "patches", "pending_patches.json"),
              "w", encoding="utf-8") as f:
        json.dump([], f)
    return PatchManager(_d)


def _iso_patch(_id, _src=None, _entry_src=None, _method="refresh"):
    p = {"id": _id, "file": "nucleus/IsoTarget.py", "method": _method,
         "issue_type": "code_optimization", "risk_level": "低",
         "trust_score": 80, "status": "pending",
         "original_code": _FRAG_ORIG, "modified_code": _FRAG_FULL}
    if _src is not None:
        p["source"] = _src
    if _entry_src is not None:
        p["entry_source"] = _entry_src
    return p


def test_343_d1_auto_fix_exempt_but_llm_still_rejected(tmp_path):
    """298刀1：llm_auto_fix 片段→完整可入队；同形态 source=llm **仍被拒**。

    判别力：两条输入形态完全相同，唯一差别是准入来源 ⇒ 若"豁免"错写成
    "取消判据"，第二条会一起放进来，本用例立刻失败。
    """
    _pm = _iso_pm(tmp_path)
    _pm.save_pending_patch(_iso_patch("fix_ok"))                  # entry_source 默认 llm_auto_fix
    _pm.save_pending_patch(_iso_patch("plain_rej", "llm", "llm"))
    _disk = json.load(open(_pm._pending_file, encoding="utf-8"))
    _ids = {x.get("id") for x in _disk if isinstance(x, dict)}
    assert "fix_ok" in _ids
    assert "plain_rej" not in _ids


def test_343_d1_auto_fix_does_not_exempt_def_mismatch(tmp_path):
    """真错配（def 名 ≠ method）在 280刀1 仍被拒 —— 豁免只针对形态，不针对缺陷。

    ★前提：323刀2 会用 AST 回填 method，使错配结构上不可达；故先关掉归一化，
    否则本用例是空转判据（用例永远"通过"但什么也没验到）。
    """
    from nucleus.reasoning.PatchManager import PatchManager
    _pm = _iso_pm(tmp_path)
    _n0 = int(PatchManager._m323_def_mismatch_rejected)
    _orig = PatchManager._m323_normalize_patch
    PatchManager._m323_normalize_patch = staticmethod(lambda _p: _p)
    try:
        _pm.save_pending_patch(_iso_patch("fix_baddef", _method="another"))
    finally:
        PatchManager._m323_normalize_patch = _orig
    _disk = json.load(open(_pm._pending_file, encoding="utf-8"))
    assert "fix_baddef" not in {x.get("id") for x in _disk if isinstance(x, dict)}
    assert int(PatchManager._m323_def_mismatch_rejected) > _n0


def test_343_d1_o3_stable_id_is_deterministic():
    """稳定 id：前缀归一、方法区分、16 位十六进制、跨调用恒定。"""
    from nucleus.evolution import patch_execution_dispatch as PED
    _a = PED._m343_o3_stable_id("nucleus/A.py", "m1")
    assert _a == PED._m343_o3_stable_id("./nucleus/A.py", "m1")
    assert _a == PED._m343_o3_stable_id("nucleus\\A.py", "m1")
    assert _a != PED._m343_o3_stable_id("nucleus/A.py", "m2")
    assert _a.startswith("o3_") and len(_a.split("_", 1)[1]) == 16
    assert _a == PED._m343_o3_stable_id("nucleus/A.py", "m1")


def test_343_d1_restored_o3_is_not_not_confirmed():
    """恢复后的 O3 条目不得落进 `not_confirmed`；未确认的新条目必须落进去。

    两条对照 ⇒ "恢复后首拍 not_confirmed 归 0"才有判别力（否则两条都 ok 也算过）。
    """
    from nucleus.evolution import patch_execution_dispatch as PED
    _ok, _why = PED._m339_o3_dispatch_ready({
        "id": "o3_r", "issue_type": "dead_code_candidate", "o3_dead_code": True,
        "source": "o3_open_gate", "status": "approved",
        "o3_auto_dispatch_ready": True, "requires_confirmation": False})
    assert (_ok, _why) == (True, "ok")
    _ok2, _why2 = PED._m339_o3_dispatch_ready({
        "id": "o3_n", "issue_type": "dead_code_candidate", "o3_dead_code": True,
        "source": "o3_open_gate", "status": "pending",
        "o3_auto_dispatch_ready": False, "requires_confirmation": True})
    assert (_ok2, _why2) == (False, "not_confirmed")


# ══════════════ 刀6：冷化链持久化 ══════════════

def _cold_pool(tmp_path):
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    _d = os.path.join(str(tmp_path), "cold")
    os.makedirs(_d, exist_ok=True)
    _p = PulseNodePool(max_hot=10, max_warm=10)
    _p._cold_dir = _d
    _p._cold_storage_enabled = True
    _p._cold_index = {}
    _p._cold_index_loaded = False
    _p._m343_idx_persist_ts = 0.0
    # 与生产同态：刚 flush 过（否则首次写盘就 flush+重建，判别力归零）
    import time as _t
    _p._cold_last_flush_time = _t.time()
    _p._cold_last_check_ts = _t.time()
    return _p


def _cold_node(_i):
    from nucleus.mnemosyne.PulseNode import PulseNode
    n = PulseNode(value={"text": "iso %d" % _i}, keywords=["iso"],
                  source_organ="iso")
    n.file_path = "iso/f%d.py" % _i
    return n


def test_343_d6_evict_persists_index(tmp_path):
    """驱逐 → 索引落盘 + 条目带 evicted/state/reload（旧实现：索引完全不动）。"""
    _p = _cold_pool(tmp_path)
    _idx = _p._cold_dir.rstrip(os.sep) + ".index.json"
    assert not os.path.isfile(_idx)                 # 先证明起点不存在
    _n = _cold_node(1)
    assert _p._evict_cold_node(_n) is True
    assert os.path.isfile(_idx)
    _d = json.load(open(_idx, encoding="utf-8"))
    _e = _d.get(str(_n.node_id)) or {}
    assert _e.get("evicted") is True
    assert _e.get("state") == "pending_flush"
    assert _e.get("reload") == "available_after_flush"


def test_343_d6_evict_ledger_and_reload(tmp_path):
    """台账 append-only 可读；新实例（重启模拟）能回载到驱逐行。"""
    _p = _cold_pool(tmp_path)
    _n = _cold_node(2)
    _p._evict_cold_node(_n)
    _led = _p._cold_dir.rstrip(os.sep) + ".evict.jsonl"
    assert os.path.isfile(_led)
    _rows = [json.loads(x) for x in open(_led, encoding="utf-8").read().splitlines() if x.strip()]
    assert any(r.get("node_id") == str(_n.node_id) for r in _rows)

    _p2 = _cold_pool(tmp_path)                      # 全新实例：索引为空、未加载
    _p2._ensure_cold_index()
    assert (_p2._cold_index.get(str(_n.node_id)) or {}).get("evicted") is True


def test_343_d6_control_without_record_keeps_index_absent(tmp_path):
    """对照：关掉记账（等价改前）⇒ 驱逐发生了但索引文件不存在。

    这条是"mtime 前进"这个结论的**判别力来源**（铁律168）。
    """
    _p = _cold_pool(tmp_path)
    _p._m343_record_eviction = lambda *a, **k: None
    _idx = _p._cold_dir.rstrip(os.sep) + ".index.json"
    for _i in range(2):
        _p._evict_cold_node(_cold_node(_i))
    assert _p._cold_evicted                      # 驱逐确实发生了
    assert not os.path.isfile(_idx)              # 但没有记账 ⇒ 索引停更


# ══════════════ 刀7：三段式判据 / 窗口唯一函数 / 告警 ══════════════

def test_343_d7_A_branch_reachable():
    """可达性单独验①：三段同真 ⇒ 「合格」这条分支真的能被走到。"""
    from nucleus.evolution import criteria_window as CW
    _r = CW.judge_round(1, 0, 1, "343.j3", "pid1@20261022-000000")
    assert (_r["verdict"], _r["A"], _r["B"], _r["C"]) == ("合格", True, True, True)


def test_343_d7_C_branch_can_fail():
    """可达性单独验②：C 的失败分支可达，且明确点名缺段（不是静默不合格）。"""
    from nucleus.evolution import criteria_window as CW
    _r = CW.judge_round(1, 0, 0, "343.j3", "pid1@20261022-000000")
    assert _r["verdict"] == "不合格" and _r["missing"] == ["C"]
    _r2 = CW.judge_round(0, 0, 1)                       # A 失败也要能单独判出来
    assert _r2["verdict"] == "不合格" and _r2["missing"] == ["A"]
    _r3 = CW.judge_round(1, 3, 1)                       # B 作废>0 ⇒ 不合格
    assert _r3["verdict"] == "不合格" and _r3["missing"] == ["B"]


def test_343_d7_window_single_source_and_unavailable():
    """窗口：唯一函数按 proc_gen 锚定；不可解析时两值 None（**不填 0**）。"""
    from nucleus.evolution import criteria_window as CW
    _w = CW.get_criteria_window("336.r12", "pid43864@20261022-080715")
    assert _w["state"] == "ok"
    assert _w["win_hi"] - _w["win_lo"] == CW.CRITERIA_WINDOW_DEFAULT_SEC
    _bad = CW.get_criteria_window("336.r12", "unknown")
    assert _bad["state"] == "unavailable"
    assert _bad["win_lo"] is None and _bad["win_hi"] is None
    _unreg = CW.get_criteria_window("999.none", "pid1@20261022-000000")
    assert _unreg["state"] == "unsupported_version"


def test_343_d7_unjudged_alarm_threshold():
    """不可判 ≥3 ⇒ 红色告警；无登记 ⇒ UNAVAILABLE（不是 CLEAR）。"""
    from nucleus.evolution import criteria_window as CW
    CW.reset_unjudged()
    assert CW.check_unjudged_alarm()["verdict"] == "UNAVAILABLE"
    CW.note_unjudged("x1", "r")
    CW.note_unjudged("x2", "r")
    _two = CW.check_unjudged_alarm()
    assert _two["alarm"] is False and _two["verdict"] == "CLEAR"
    CW.note_unjudged("x3", "r")
    _three = CW.check_unjudged_alarm()
    assert _three["alarm"] is True and _three["verdict"] == "RED_ALARM"
    CW.reset_unjudged()


def test_343_d7_wired_into_o3_report_source():
    """告警必须**接在生产路径上**（不是只有单测里能调）。"""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _src = open(os.path.join(_root, "nucleus", "self_inspector.py"),
                encoding="utf-8").read()
    assert "criteria_window" in _src
    assert "[343判据][红色告警]" in _src
    assert '"unjudged_alarm"' in _src
