# -*- coding: utf-8 -*-
"""356 批（自进柱回写接线 + 355 遗留 + 重启首拍新发现）核心路径测试。

阶段A 十刀的代码面：刀1 闭环回写 / 刀2 键口径统一 / 刀3 台账与串行化 /
刀4 检测器白名单 / 刀5 原子写 / 刀6 日报变量覆盖 / 刀7 免费链可见化 /
刀10① 坏 JSON 单次留痕。刀8/刀9 为分析结论（见交付报告），无生产代码面。

纪律：只读生产文件做源码断言；池/日报一律隔离目录或纯函数，不写生产 data/。
"""
import io
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8",
                   newline="").read().replace("\r\n", "\n")


def _method(rel, name):
    import re
    lines = _src(rel).split("\n")
    pat = re.compile(r"\s*(?:async\s+)?def\s+" + re.escape(name) + r"\s*\(")
    for i, l in enumerate(lines):
        if pat.match(l):
            ind = len(l) - len(l.lstrip())
            out = [l]
            for j in range(i + 1, len(lines)):
                s = lines[j]
                if s.strip() and (len(s) - len(s.lstrip())) <= ind:
                    break
                out.append(s)
            return "\n".join(out)
    return ""


# ==========================================================================
# 刀1 · 自进柱回写接线（FIXED / VERIFIED 双写）
# ==========================================================================
def test_356_d1_fixed_wiring_exists_and_safe():
    """FIXED 回写入口存在，且键不在池里时安全返回（不崩、不误写）。"""
    import nucleus.evolution.patch_execution_dispatch as PED
    assert hasattr(PED, "_m356_close_loop_fixed")
    _r = PED._m356_close_loop_fixed({"id": "ut356", "dedup_key": "deadbeefdeadbeef"})
    assert isinstance(_r, dict)
    assert _r.get("changed") is False, "池里没有该键却报 changed=True"


def test_356_d1_fixed_called_only_on_success():
    """接线必须在 success 分支里，不能在失败分支也回写 FIXED。"""
    _t = _src("nucleus/evolution/patch_execution_dispatch.py")
    assert 'if _v.get("success"):\n            _m356_close_loop_fixed(_p)' in _t
    _body = _method("nucleus/evolution/patch_execution_dispatch.py",
                    "dispatch_runtime_verified_patches")
    assert "_m356_close_loop_fixed(_p)" in _body


def test_356_d1_verified_double_write():
    """VERIFIED 与 problem_fixed=verified 同事件双写；失败写 failed。"""
    _b = _method("nucleus/audit_problem_pool.py", "note_verify_feedback")
    assert '_hit["problem_fixed"] = "verified"' in _b
    assert '_hit["problem_fixed"] = "failed"' in _b
    _i = _b.index('_hit["problem_fixed"] = "verified"')
    assert "status_history" in _b[:_i], "双写必须发生在状态翻转之后"


def test_356_d1_no_state_machine_rebuilt():
    """禁止重建状态机：既有 _LEGAL_TRANSITIONS 必须仍在且被 set_status 使用。"""
    _t = _src("nucleus/audit_problem_pool.py")
    assert "_LEGAL_TRANSITIONS = {" in _t
    _b = _method("nucleus/audit_problem_pool.py", "set_status")
    assert "_LEGAL_TRANSITIONS.get(_old, ())" in _b


# ==========================================================================
# 刀2 · 键口径统一（audit 与 generator 同键）
# ==========================================================================
def test_356_d2_key_same_across_path_spellings():
    import nucleus.audit_problem_pool as APP
    _variants = [
        "organs/body/PulseLiver.py",
        "organs\\body\\PulseLiver.py",
        "./organs/body/PulseLiver.py",
        "D:\\xinrenlei\\tongtong-pulse-v9\\organs\\body\\PulseLiver.py",
        "D:/xinrenlei/tongtong-pulse-v9/organs/body/PulseLiver.py",
    ]
    _keys = {APP.make_dedup_key(v, "_on_pulse", "dead_code") for v in _variants}
    assert len(_keys) == 1, "5 种写法没归一到同一键: %s" % _keys


def test_356_d2_audit_generator_keys_match():
    import nucleus.audit_problem_pool as APP
    import nucleus.evolution.patch_generator as PG
    for _v in ("organs\\body\\PulseLiver.py",
               "D:\\xinrenlei\\tongtong-pulse-v9\\organs\\body\\PulseLiver.py",
               "organs/body/PulseLiver.py"):
        assert APP.make_dedup_key(_v, "m", "t") == PG.make_dedup_key(_v, "m", "t"), _v
        assert PG.dedup_key_matches_pool(_v, "m", "t") is True


def test_356_d2_audit_uses_norm_file_field():
    """两侧都必须走归一函数，不能只改一侧（否则绝对路径再次分叉）。"""
    assert "norm_file_field(file)" in _method("nucleus/audit_problem_pool.py",
                                              "make_dedup_key")
    assert "_m356_norm_key_file(file)" in _method("nucleus/evolution/patch_generator.py",
                                                  "make_dedup_key")
    assert "_PROJ_DIRNAME" in _src("nucleus/evolution/patch_generator.py") or \
        "_M356_PROJ_DIRNAME" in _src("nucleus/evolution/patch_generator.py")


# ==========================================================================
# 刀3 · 台账 status_counts + 池写串行化
# ==========================================================================
def test_356_d3_pool_write_serialized():
    """三个池写入口都必须串行化（persist/set_status/note_verify_feedback）。"""
    _t = _src("nucleus/audit_problem_pool.py")
    assert "_M356_POOL_LOCK" in _t and "_m356_serialized" in _t
    for _fn in ("persist_pool", "set_status", "note_verify_feedback"):
        _lines = _t.split("\n")
        _idx = [i for i, l in enumerate(_lines) if l.startswith("def %s(" % _fn)]
        assert _idx, _fn
        assert _lines[_idx[0] - 1].startswith("@_m356_serialized"), \
            "%s 未加串行化装饰器" % _fn


def test_356_d3_ledger_has_status_counts():
    """台账指纹必须带 status_counts（此前压根没这个键 ⇒ 读方恒 None）。"""
    _b = _method("nucleus/audit_problem_pool.py", "_fingerprint_of")
    assert '"status_counts"' in _b
    _b2 = _method("nucleus/audit_problem_pool.py", "note_pool_fingerprint")
    assert '"status_counts"' in _b2, "台账顶层没镜像 status_counts"


def test_356_d3_status_counts_not_none():
    import nucleus.audit_problem_pool as APP
    _entries = [{"status": "open"}, {"status": "verified"}, {"status": "open"}]
    _c = APP._status_counts(_entries)
    assert _c.get("open") == 2 and _c.get("verified") == 1
    assert _c is not None


# ==========================================================================
# 刀4 · 检测器白名单（负控）
# ==========================================================================
def test_356_d4_cross_whitelist():
    import nucleus.self_inspector as SI
    _inst = object.__new__(SI.SelfInspector)
    _inst._generate_fix_suggestion = lambda *a, **k: "sug"
    _body = ("def f(self):\n"
             "    get_insight_board().post(t=1)\n"
             "    self.stress_axis.get_stats()\n"
             "    get_event_bus().publish(1)\n"
             "    get_kal().get_node('n1')\n")
    _iss = []
    _inst._check_cross_module_singleton_call(_body, "f.py", "o", "f", 1, _iss)
    assert len(_iss) == 1 and "get_kal" in _iss[0]["description"], \
        "白名单没生效: %s" % [i["description"] for i in _iss]


def test_356_d4_self_call_never_reported():
    """同器官调用（self.xxx）不是跨模块耦合——355 的 15 条假阳性里 4 条栽在这里。"""
    import nucleus.self_inspector as SI
    _inst = object.__new__(SI.SelfInspector)
    _inst._generate_fix_suggestion = lambda *a, **k: "sug"
    _iss = []
    _inst._check_cross_module_singleton_call(
        "def f(self):\n    self.node_pool.get_stats()\n", "f.py", "o", "f", 1, _iss)
    assert not _iss


def test_356_d4_periodic_init_skipped_with_control():
    """__init__ 不报；普通周期方法仍要报（白名单不能连坐）。"""
    import nucleus.self_inspector as SI
    _inst = object.__new__(SI.SelfInspector)
    _inst._generate_fix_suggestion = lambda *a, **k: "sug"
    _body = "def f(self):\n    self._t = threading.Timer(5, self._tick)\n"
    _a, _b = [], []
    _inst._check_periodic_task_no_reentry(_body, "f.py", "o", "__init__", 1, _a)
    _inst._check_periodic_task_no_reentry(_body, "f.py", "o", "_tick", 1, _b)
    assert not _a, "__init__ 仍被误报"
    assert len(_b) == 1, "普通方法漏报（白名单连坐）"


# ==========================================================================
# 刀5 · 两处遗留非原子写
# ==========================================================================
def test_356_d5_atomic_write_both_sites():
    for _rel, _fn in (("organs/motor/PulseLegs.py", "_write_learn_log"),
                      ("organs/senses/PulseVisualCortex.py", "_log_writer_loop")):
        _b = _method(_rel, _fn)
        assert "os.replace(" in _b, "%s 未原子写" % _fn
        assert "os.fsync(" in _b, "%s 未 fsync" % _fn
        assert "mkstemp(" in _b, "%s 未走临时文件" % _fn


def test_356_d5_no_bare_truncating_open():
    """目标文件不得再被裸 open(...,'w') 截断。"""
    for _rel in ("organs/motor/PulseLegs.py", "organs/senses/PulseVisualCortex.py"):
        _t = _src(_rel)
        assert "open(log_path, 'w'" not in _t, _rel
        assert 'open(log_path, "w"' not in _t, _rel


def test_356_d5_learn_log_lock_present():
    _t = _src("organs/motor/PulseLegs.py")
    assert "_M356_LEARN_LOG_LOCK" in _t
    assert "with _M356_LEARN_LOG_LOCK:" in _method("organs/motor/PulseLegs.py",
                                                  "_write_learn_log")


# ==========================================================================
# 刀6 · 日报变量覆盖（真 BUG）
# ==========================================================================
def test_356_d6_retrain_does_not_clobber_report_path():
    _t = _src("nucleus/self_awareness/DailyScheduler.py")
    assert "_rp = _mu.full_retrain()" not in _t, "`_rp` 仍被重训结果覆盖"
    assert "_m356_retrain = _mu.full_retrain()" in _t


def test_356_d6_four_sections_still_wired():
    """四个 section 仍传 _rp（现在它一定是 str）。"""
    _t = _src("nucleus/self_awareness/DailyScheduler.py")
    for _fn in ("append_phase18_section", "append_mastery_section",
                "append_remediation_stats_section", "append_global_awareness_section"):
        assert _fn in _t
    assert _t.count("append_phase18_section(_rp") + \
        _t.count("append_phase18_section(\n                        _rp") >= 1


def test_356_d6_reader_accepts_str_path(tmp_path):
    """四个 section 在 str 路径上必须真能写（隔离目录）。"""
    from nucleus.self_awareness import daily_reader as DR
    _p = str(tmp_path / "report_test.txt")
    io.open(_p, "w", encoding="utf-8").write("# 日报\n")
    DR.append_phase18_section(_p)
    _txt = io.open(_p, encoding="utf-8").read()
    assert "PHASE18" in _txt


# ==========================================================================
# 刀7 · 免费链用量可见化
# ==========================================================================
def test_356_d7_free_by_model_accumulated():
    from nucleus.llm.call_recorder import _m352b_accumulate
    _day = "2026-10-24"
    _d = _m352b_accumulate({}, {"channel": "zhipu", "model": "glm-4.7-flash",
                                "tokens": 120, "ts": 1792755716.0}, _day,
                           paid_channels=frozenset({"deepseek"}))
    assert _d["free_tokens"] == 120
    assert _d["free_by_model"]["glm-4.7-flash"]["tokens"] == 120
    assert _d["paid_tokens"] == 0, "免费链不得计入付费口径"


def test_356_d7_paid_not_mixed_into_free():
    from nucleus.llm.call_recorder import _m352b_accumulate
    _d = _m352b_accumulate({}, {"channel": "deepseek", "model": "deepseek-flash",
                                "tokens": 999, "ts": 1792755716.0}, "2026-10-24",
                           paid_channels=frozenset({"deepseek"}))
    assert _d["paid_tokens"] == 999
    assert _d["free_tokens"] == 0
    assert not _d.get("free_by_model"), "付费调用不该进 free_by_model"


def test_356_d7_free_share_none_without_sample(tmp_path):
    """无样本 ⇒ free_share=None（不是 0），且额度余量明确 unavailable。"""
    from nucleus.llm.call_recorder import free_chain_usage
    _r = free_chain_usage(base_dir=str(tmp_path))
    assert _r["free_share"] is None
    assert _r["quota_hint"]["state"] == "unavailable"
    assert _r["schema"] == "m356_free_chain/356.v1"


def test_356_d7_wired_into_heartbeat():
    _t = _src("nucleus/audit_problem_pool_consumer.py")
    assert "free_chain_usage()" in _t
    assert '"free_chain"' in _t


# ==========================================================================
# 刀10① · 坏 JSON 单次留痕
# ==========================================================================
def test_356_d10_bad_json_warn_once():
    _t = _src("nucleus/self_awareness/capability_ledger.py")
    assert "_M356_BAD_JSON_WARNED" in _t
    _b = _method("nucleus/self_awareness/capability_ledger.py", "reconcile_reports")
    assert "if _rel not in _M356_BAD_JSON_WARNED" in _b
    # 计数不能被 dedupe 连坐：unparsable 仍无条件自增
    assert '_out["unparsable"] += 1' in _b


# ==========================================================================
# 账目
# ==========================================================================
def test_356_batch_accounts():
    """355 终态仍成立：观察位仍在池里；applied_355 为 7 条（刀12 对账对象）。

    ★**不锁 pending 总行数**：活体框架（pid 常驻）随时会往 pending 写新条目
    （本批施工期间就新增了一条 `Traceback`），把行数写死等于让测试每隔几小时
    红一次——那是**测试缺陷**，不是回归。
    """
    import json
    _pend = json.load(io.open(os.path.join(_ROOT, "data", "patches",
                                           "pending_patches.json"), encoding="utf-8"))
    _ids = [str(r.get("id")) for r in _pend]
    assert any(i.endswith("1b31fee790d6e9") for i in _ids), "判死档观察位不见了"
    assert len(_pend) >= 1
    _ap = json.load(io.open(os.path.join(_ROOT, "data", "patches",
                                         "applied_355.json"), encoding="utf-8"))
    assert _ap["count"] == 7
