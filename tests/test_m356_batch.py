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
    """★374刀8（d5 判据改造）：主路径不许裸截断写，**三级兜底层豁免**。

    旧判据是字面扫「文件里不许出现 `open(log_path, 'w'`」——分不清
    「主路径裸写」（真缺陷）与「rename 两次被拒后的兜底裸写」（370 刀1 有意保留：
    不回退就丢整批条目），自 370 起持续红。现按 **AST + 位置** 判：
      · 主路径（不在 except 体内）出现裸 `open(log_path,'w')` ⇒ 红；
      · 裸写只允许在 except 兜底分支，且该 handler 必须留痕；
      · 主路径必须仍是「临时文件 + os.replace」原子写。
    """
    import sys as _sys
    if os.path.dirname(os.path.abspath(__file__)) not in _sys.path:
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_m374_c8_d5_write_path import assert_no_primary_bare_write
    for _rel in ("organs/motor/PulseLegs.py", "organs/senses/PulseVisualCortex.py"):
        assert_no_primary_bare_write(_rel)


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


# ==========================================================================
# 刀11 · pending 单写者仲裁（洞鉴 DJ4-03）
# ==========================================================================
def test_356_d11_three_writers_share_one_gate():
    """三个写者都必须经唯一写口（PatchManager / AutoApprover / dispatch）。"""
    for _rel in ("nucleus/reasoning/PatchManager.py",
                 "nucleus/evolution/PatchAutoApprover.py",
                 "nucleus/evolution/patch_execution_dispatch.py"):
        assert "pending_write import write_pending" in _src(_rel), _rel
    assert "PENDING_WRITE_LOCK" in _src("nucleus/evolution/pending_write.py")


def test_356_d11_autapprover_no_bare_truncating_write():
    """AutoApprover 原先是裸 open(...,'w') 截断写 —— 现在不得再有。"""
    _t = _src("nucleus/evolution/PatchAutoApprover.py")
    assert 'open(self._pending_path, "w"' not in _t


def test_356_d11_atomic_rmw_loses_nothing(tmp_path):
    """RMW 全在锁内 ⇒ 并发追加零丢失（小规模复现压测结论）。"""
    import threading
    from nucleus.evolution.pending_write import update_pending
    _p = str(tmp_path / "pending_patches.json")
    io.open(_p, "w", encoding="utf-8").write("[]")

    def _w(tid):
        for i in range(10):
            update_pending(lambda cur, t=tid, i=i: cur + [{"id": "t%d-%d" % (t, i)}],
                           path=_p, writer="ut356")

    _ts = [threading.Thread(target=_w, args=(i,)) for i in range(4)]
    for t in _ts:
        t.start()
    for t in _ts:
        t.join()
    import json
    _d = json.load(io.open(_p, encoding="utf-8"))
    assert len(_d) == 40, "并发 RMW 丢写：%d/40" % len(_d)


# ==========================================================================
# 刀12 · 日志轮转截断（stream=None 必须仍截断）
# ==========================================================================
def test_356_d12_stream_none_truncates(tmp_path):
    os.environ["PULSE_LOG_ROTATE"] = "1"
    import nucleus.logger as LG
    _base = str(tmp_path / "pulse.log")
    io.open(_base, "w", encoding="utf-8").write("x" * 4000 + "\n")
    _h = LG.SafeRotatingFileHandler(_base, maxBytes=1024, backupCount=2, encoding="utf-8")
    # 这里刻意**不 try/except**：close 失败就让测试红（静默吞会把"流没关掉"
    # 伪装成"stream=None 场景"，测的就不是要测的东西了）。
    _h.stream.flush()
    _h.stream.close()
    _h.stream = None
    _h.doRollover()
    assert os.path.getsize(_base) == 0, "stream=None 时主文件没被截断（253 批缺陷回潮）"


def test_356_d12_storm_budget():
    import nucleus.logger as LG
    LG._M356_ROLLOVER_STAMPS.clear()
    LG._M356_ROLLOVER_STORM_WARNED = False
    _r = [LG._m356_rollover_budget_ok() for _ in range(5)]
    assert _r[:3] == [True, True, True] and _r[3:] == [False, False]
    assert LG._M356_ROLLOVER_STORM_WARNED is True


def test_356_d12_bridge_reuses_main_handler():
    """树外 bridge 不得再 new 普通 FileHandler 直写同一文件。

    ★434刀2：主 handler 已换成 QueueHandler，真正的文件 handler 由 QueueListener
    持有、存模块级 _FILE_HANDLER，bridge 改为复用它（isinstance(_FILE_HANDLER, ...)），
    判据从「遍历 root.handlers 找 _h」改为「复用 _FILE_HANDLER」。行为本质不变：
    仍复用主 SafeRotatingFileHandler、仍不 new 普通 FileHandler。
    """
    _t = _src("nucleus/logger.py")
    assert "isinstance(_FILE_HANDLER, SafeRotatingFileHandler)" in _t
    assert "logging.FileHandler(_log_file" not in _t


# ==========================================================================
# 刀14 · 存量键迁移（停机窗已执行）
# ==========================================================================
def test_356_d14_pool_keys_unique_and_dialect_clean():
    import json
    import nucleus.audit_problem_pool as APP
    _p = os.path.join(_ROOT, "data", "reports", "audit", "audit_20261024.json")
    _d = json.load(io.open(_p, encoding="utf-8"))
    _e = _d.get("entries") or []
    _ks = [str(x.get("dedup_key")) for x in _e]
    assert len(_ks) == len(set(_ks)), "池里仍有重复键"
    _bad = [x.get("dedup_key") for x in _e
            if x.get("file") and x.get("method") and x.get("issue_type")
            and APP.make_dedup_key(x["file"], x["method"], x["issue_type"])
            != str(x.get("dedup_key"))]
    assert not _bad, "仍有 %d 条键口径不一致" % len(_bad)


def test_356_d14_map_and_merged_archive_exist():
    _m = os.path.join(_ROOT, "data", "reports", "audit", "_m356_key_map.json")
    _a = os.path.join(_ROOT, "data", "reports", "audit", "_m356_merged_rows.json")
    assert os.path.isfile(_m) and os.path.isfile(_a)
    import json
    assert json.load(io.open(_m, encoding="utf-8"))["count"] > 0
    assert json.load(io.open(_a, encoding="utf-8"))["count"] > 0


# ==========================================================================
# 刀15 · 批量对账回写
# ==========================================================================
def test_356_d15_status_distribution():
    import nucleus.audit_problem_pool as APP
    _p = APP.load_pool()
    _c = {}
    for e in _p.get("entries") or []:
        _c[str(e.get("status"))] = _c.get(str(e.get("status")), 0) + 1
    assert _c.get("fixed", 0) >= 7, "355 应用 7 条未回写 FIXED: %s" % _c
    assert _c.get("wontfix", 0) >= 21, "355 拒绝未回写 WONTFIX: %s" % _c


def test_356_d15_wontfix_carries_death_reason():
    import nucleus.audit_problem_pool as APP
    _p = APP.load_pool()
    _wf = [e for e in _p.get("entries") or [] if str(e.get("status")) == "wontfix"]
    _miss = [e.get("dedup_key") for e in _wf
             if "[356对账]" not in str(e.get("closed_reason") or "")]
    assert not _miss, "wontfix 缺死因: %s" % _miss[:3]


# ==========================================================================
# 刀16 · EventBus 接线
# ==========================================================================
def test_356_d16_bus_enabled_and_stop_path_present():
    _c = _src("config.py")
    assert "ENABLE_EVENT_BUS = True" in _c
    _m = _src("main.py")
    assert "停止序列清点[EventBus]" in _m, "停机 drain/stop 分支不见了"
    assert "_eb.drain(" in _m and "_eb.stop(" in _m
    assert "[356刀16]" in _m, "接线对账留痕缺失"
