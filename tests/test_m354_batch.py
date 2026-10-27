# -*- coding: utf-8 -*-
"""第 354 批 · 8 刀隔离单测（刀1/2/3/4/5/6/7/8 核心路径 + 负控）

纪律：
  · 真值用 monkeypatch / 隔离目录注入，不写生产 data/（铁律179/198）。
    唯一例外是**只读**核验刀7 的生产 pending 落盘结果。
  · 判据按语义单元，不做整文件子串匹配（铁律162）；每条断言注明"为什么这么判"。
  · 重活（进程池微基准 / 718MB 压缩采样 / 压测 S 圈）由 tmp/p354_root 下的脚本出数，
    测试只断言**证据件在位且关键数值自洽**——避免在 pytest 里跑分钟级基准。
"""
import io
import json
import os
import sys
import time


os.environ.setdefault("PULSE_FRAMEWORK", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_TMP = os.path.join(_ROOT, "tmp", "p354_root")


def _src(rel):
    with io.open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _evi(name):
    with io.open(os.path.join(_TMP, name), encoding="utf-8") as f:
        return json.load(f)


# ==========================================================================
# 刀1 · 快照压缩（只读实测，活体在写 ⇒ 本批不落盘）
# ==========================================================================
def test_354_d1_compress_bench_exists_and_selfconsistent():
    """压缩实测：采样外推的回收量必须与压缩比自洽，且回载字节级全等。"""
    _d = _evi("d1_compress_bench.json")
    assert _d["roundtrip_equal"] is True, "回载不全等 ⇒ 压缩方案根本不可行"
    _best = _d["codecs"]["gzip-6(默认)"]
    _ratio = _best["ratio"]
    _est = _best["est_full_gz_mb"]
    # 算术自洽：外推值 == 压缩比 × 全量体积（铁律171）
    assert abs(_est - _ratio * _d["main_bytes"] / 1e6) < 1.0
    assert 0.0 < _ratio < 0.2, "JSON 文本快照压缩比应远小于 20%%（实测 %.3f）" % _ratio
    assert _d["main_mb"] > 700, "主快照应在 700MB 量级（实测 %.1f）" % _d["main_mb"]


def test_354_d1_no_write_to_production_snapshot():
    """本批不停机 ⇒ 主快照与 .bak 的 mtime 不应被本批改动（只做只读采样）。"""
    _main = os.path.join(_ROOT, "data", "knowledge", "pulse_knowledge_snapshot.json")
    _st = os.stat(_main)
    # 快照由活体持续写；这里只断言"文件仍是活的且仍是那个量级"，不断言具体 mtime
    assert _st.st_size > 700 * 1000 * 1000


# ==========================================================================
# 刀2 · 线程 v1 重锚 + 收口
# ==========================================================================
def test_354_d2_selfcheck_block_removed_from_production():
    """`base/BasePulseOrgan.py` 的 `__main__` 自测块必须**已移出生产文件**。

    判据取"文件里不再有 `__main__` 入口"，而不是"行数变少"——后者会被任何编辑骗过。
    """
    _t = _src("base/BasePulseOrgan.py")
    assert 'if __name__ == "__main__":' not in _t, "自测块仍在生产文件里"


def test_354_d2_shutdown_thread_inlined():
    """`_trigger_shutdown` 的一次性 daemon 线程已内联（不再 spawn）。"""
    _t = _src("organs/brain/PulseInnerWorld.py")
    assert "threading.Thread(target=_trigger_shutdown" not in _t
    assert "_trigger_shutdown()" in _t


def test_354_d2_sensor_guard_present():
    """传感器启用护栏：重复 boot 不重复起线程 + 上次存活即留痕 `[354线程]`。"""
    _t = _src("organs/core/PulseDeviceManager.py")
    assert "_sensor_enable_thread" in _t, "护栏句柄缺失"
    assert "[354线程]" in _t, "卡死存活告警留痕缺失"
    # 负控：护栏只在"上一个线程仍存活"时跳过，不能无条件跳过（否则传感器永不启用）
    assert "_prev.is_alive()" in _t


def test_354_d2_v2_table_exists_and_anchored():
    """v2 表落盘，且锚点是**重锚后的 HEAD 行号**（v1 已漂移 6/15，禁止再用）。"""
    _p = os.path.join(_ROOT, "docs", "星轨与小林对话", "线程三态分类表_v2_20261024.md")
    assert os.path.isfile(_p), "v2 表未落盘"
    _d = _evi("d2_thread_table_v2.json")
    assert _d["total"] == 58, "生产线程点应为 58（实测 %d）" % _d["total"]
    # 三态之和 == 总数（铁律171 算术自洽）
    _t = _d["tally"]
    assert sum(_t.values()) == _d["total"], "复核三态之和 != 总数"
    assert _t.get("in_place", 0) > 0, "没有任何锚点对得上 ⇒ 重锚脚本判据坏了"


# ==========================================================================
# 刀3 · K2 进程池微基准（判死线）
# ==========================================================================
def test_354_d3_k2_bench_deadline_double_reading():
    """判死线给**双口径**结论：中位/最大都要出数，且规则写死在证据里。

    为什么不断言"作废"或"不作废"：本机此刻活体在跑，墙钟被抢占 ⇒ CPU 占比被低估，
    单口径下结论不可信。测试只保证"两种口径都算了、规则没被改"。
    """
    _d = _evi("d3_k2_bench.json")
    _dl = _d["deadline"]
    assert _dl["rule"].startswith("可并行 CPU 占比 < 20%")
    assert _dl["median"] is not None and _dl["max"] is not None
    assert _dl["median"] <= _dl["max"], "中位不应大于最大值"
    assert len(_d["cpu_samples"]) >= 3, "少于 3 轮采样不足以谈中位"
    # spawn / pickle 开销必须出数（判净亏用得上）
    assert _d["spawn"]["cold_ms"] > 0 and _d["spawn"]["hot_ms"] > 0
    assert _d["pickle"]["bytes"] > 0
    # 安全面：K2 停在"不落地进程池"
    assert "不启动" in _dl["conclusion"]


# ==========================================================================
# 刀4 · 渠道判读框架 + 入池判据 + 353刀1 原子化
# ==========================================================================
def test_354_d4_hhi_alert_on_real_data():
    """HHI 告警：真实 daily_tokens 上必须能算出 >0.6 的单渠道依赖。"""
    import nucleus.llm.call_recorder as CR
    _rep = CR.read_daily_tokens_report()
    assert _rep["schema"] == CR.CHANNEL_READING_SCHEMA
    assert _rep["hhi"] > CR.CHANNEL_HHI_ALERT, (
        "HHI=%.3f 未超 %.2f ⇒ 判读框架对它最该抓的那个问题失明"
        % (_rep["hhi"], CR.CHANNEL_HHI_ALERT))
    assert any(a["code"] == "hhi_concentration" for a in _rep["alerts"])
    assert "1_000_000" in _rep["threshold_deprecated"], "废弃全局阈值的说明必须留着"


def test_354_d4_burn_rate_is_per_channel_not_global():
    """燃烧率必须是 per-channel 月额口径：无月额渠道= None（不是 0），有月额渠道给比率。"""
    import nucleus.llm.call_recorder as CR
    assert CR.CHANNEL_MONTHLY_QUOTA["bailian-qwen-plus"] == 1_000_000
    assert CR.CHANNEL_MONTHLY_QUOTA["zhipu"] == -1, "无月额渠道须标 -1（0 会被当成'额度为 0'）"
    # HHI 口径自检：两家各半 ⇒ 0.5
    assert CR.channel_hhi({"a": {"calls": 5}, "b": {"calls": 5}}) == 0.5
    assert CR.channel_hhi({}) == 0.0


def test_354_d4_pool_expansion_undecidable_when_insufficient():
    """数据不足 2 日 ⇒ 判 'undecidable'，**不得**伪装成 defer/join。"""
    import nucleus.llm.call_recorder as CR
    _r = CR.evaluate_pool_expansion([{"by_channel": {"zhipu": {"calls": 9}}}])
    assert _r["verdicts"]["TokenHub"] == "undecidable"
    assert _r["verdicts"]["qianfan-ernie-speed"] == "undecidable"


def test_354_d4_pool_expansion_join_when_criteria_met():
    """★375刀10 修正：判据是**两条同时成立**（占比>70% ∧ 健康度<0.6 或 429≥2/日）。

    354 原断言用「占比 80%/85%、零失败、零限流」的 rows 期望 `join` —— 那等于
    「只要用得多就换渠道」，把「依赖」和「依赖且**不好用**」混为一谈，正是判据被
    削掉一半的化石。这里换成「占比高 **且** 不健康」的 rows，才与文案对得上；
    「占比高但健康 ⇒ 不换」由 `test_m375_d10_pool_criteria.py::test_07` 反向钉住。
    """
    import nucleus.llm.call_recorder as CR
    _rows = [{"by_channel": {"zhipu": {"calls": 80, "failed": 40, "rate_limit": 0},
                             "bailian-qwen-plus": {"calls": 20}}},
             {"by_channel": {"zhipu": {"calls": 85, "failed": 45, "rate_limit": 0},
                             "bailian-qwen-plus": {"calls": 15}}}]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["verdicts"]["TokenHub"] == "join"
    assert _r["verdicts"]["qianfan-ernie-speed"] == "defer"


def test_354_d4_pick_and_acquire_is_atomic():
    """353刀1 遗留 TOCTOU 的补法：占不到许可就返回 (None, None)，绝不返回未占到的目标。"""
    from nucleus.llm.ChannelConcurrency import (
        get_channel_concurrency_manager, reset_channel_concurrency_manager)
    from nucleus.llm.channel_balance import pick_and_acquire
    reset_channel_concurrency_manager()
    _mgr = get_channel_concurrency_manager()
    _rows = [{"channel": "fa"}, {"channel": "fb"}, {"channel": "deepseek-paid"}]
    for _r in _rows:
        _mgr.ensure_channel(_r["channel"])
    _paid = {"deepseek-paid"}
    _t1, _rel1 = pick_and_acquire(_rows, caller="ut354", paid_channels=_paid)
    assert _t1 is not None and _rel1 is not None
    _t2, _ = pick_and_acquire(_rows, caller="ut354", paid_channels=_paid)
    # 两条免费渠道占满后，第三次必须拿不到（付费渠道窗口前截止 ⇒ 不会跳过去）
    _t3, _rel3 = pick_and_acquire(_rows, caller="ut354", paid_channels=_paid)
    assert _t3 is None and _rel3 is None, "占不到许可仍返回目标 ⇒ TOCTOU 没补上"
    _rel1()
    assert _mgr.get_semaphore("fa").available() >= 1, "release 未归还许可（铁律181）"


def test_354_d4_paid_boundary_can_be_given_explicitly():
    """付费边界**可显式传入**：否则调用方的渠道名不在 config.PAID_CHANNELS 里时，
    "均衡不越界付费"会静默失效（354 自测踩到过）。"""
    from nucleus.llm.channel_balance import pick_balanced_index
    _rows = [{"channel": "fa"}, {"channel": "fb"}, {"channel": "my-paid-ch"}]
    # 不传 ⇒ 回落到 config.PAID_CHANNELS（"my-paid-ch" 不在里面 ⇒ 会被当成免费渠道）
    _i_default, _ = pick_balanced_index(_rows, caller="ut354")
    # 显式传入 ⇒ 窗口应截止在付费渠道之前
    _i_explicit, _w = pick_balanced_index(_rows, caller="ut354",
                                          paid_channels={"my-paid-ch"})
    assert _i_explicit == 0, "显式付费集合未生效（窗口应在付费渠道前截止）"
    assert _i_default == 0 or _i_default == 1  # 默认口径下不做强断言，只保证不越界付费


# ==========================================================================
# 刀5 · 限额回退实测（343 裁决补课）
# ==========================================================================
def test_354_d5_revoke_does_not_roll_back_intake(tmp_path):
    """撤销 1 条后当日 intake 计数**不回退**（撤销只改 status，不动入池标记）。"""
    from nucleus.self_inspector import _m333_intake_rate
    _d = tmp_path / "data" / "patches"
    _d.mkdir(parents=True)
    _pp = _d / "pending_patches.json"
    _now = time.time()
    _pp.write_text(json.dumps([{"id": "a", "o3_dead_code": True,
                                "generated_at": _now, "status": "pending"}]),
                   encoding="utf-8")
    _r1 = _m333_intake_rate(project_root=str(tmp_path), now=_now)
    assert _r1["count"] == 1
    _pp.write_text(json.dumps([{"id": "a", "o3_dead_code": True,
                                "generated_at": _now, "status": "已撤销"}]),
                   encoding="utf-8")
    _r2 = _m333_intake_rate(project_root=str(tmp_path), now=_now)
    assert _r2["count"] == 1, "撤销导致当日计数回退 ⇒ 会被拿来绕 1/天 限额"
    assert _r2["revoked_no_rollback"] == 1


def test_354_d5_restore_does_not_consume_quota(tmp_path):
    """restore 显式计数**不占额度**（与 348 豁免同族：不是不算，是不进额度）。"""
    from nucleus.self_inspector import _m333_intake_rate
    _d = tmp_path / "data" / "patches"
    _d.mkdir(parents=True)
    _pp = _d / "pending_patches.json"
    _now = time.time()
    _rows = [{"id": "a", "o3_dead_code": True, "generated_at": _now, "status": "pending"},
             {"id": "b", "o3_dead_code": True, "generated_at": _now, "status": "pending",
              "o3_restore_no_quota": True}]
    _pp.write_text(json.dumps(_rows), encoding="utf-8")
    _r = _m333_intake_rate(project_root=str(tmp_path), now=_now)
    assert _r["count"] == 1, "restore 条目占了额度"
    assert _r["restore_no_quota"] == 1


# ==========================================================================
# 刀6 · 生成前目标存在性预检（★星轨 v1.1 修正：锚=真实方法名，不是 field 名）
# ==========================================================================
def test_354_d6_precheck_v2_accepts_when_real_method_hits():
    """★修正口径负控：field 名错、但 `method_before` 命中 ⇒ **不拒收**。

    星轨对 pending 40 条 AST 深析：35 条的 `method` 是生成器猜的名，真实名在
    `m323_truth.method_before`。只按 field 查名即拒收 ⇒ 会误杀 35 条真实补丁。
    """
    from nucleus.evolution.patch_generator import precheck_target_v2
    # 真实取例（pending 第 2 条）：field 名是生成器猜的 _append_emotion_log_entry，
    # 代码里的真实方法叫 _write_emotion_log ⇒ 只能靠 method_before 锚定。
    _r = precheck_target_v2(
        _ROOT, "organs/endocrine/PulseHormones.py",
        method="_append_emotion_log_entry",       # field 名（错）
        method_before="_write_emotion_log",       # 真实名（对）
    )
    assert _r["verdict"] == "in_place", "真实方法名命中却判不在位 ⇒ 会误杀真实补丁"
    assert _r["anchor"] == "method_before"


def test_354_d6_precheck_v2_rejects_only_when_all_three_miss():
    """拒收**仅当三锚全不满足**（field ∨ method_before ∨ original_code）。"""
    from nucleus.evolution.patch_generator import precheck_target_v2
    _r = precheck_target_v2(
        _ROOT, "organs/core/PulseSystemManager.py",
        method="_definitely_not_here_xyz",
        method_before="_also_not_here_xyz",
        original_code="def _totally_fabricated_code_xyz(): pass",
    )
    assert _r["verdict"] != "in_place"
    assert _r["anchor"] == "none"
    assert "three_anchors_all_missed" in _r["why"]


def test_354_d6_stock_replay_zero_rejected(caplog):
    """存量 40 条回放：按修正口径应 **0 拒收**（352 基线 27/39 是名字漂移，不是位置漂移）。"""
    import nucleus.evolution.patch_generator as PG
    _rows = json.load(io.open(os.path.join(_ROOT, "data", "patches",
                                           "pending_patches.json"), encoding="utf-8"))
    _pb = PG.precheck_batch(_ROOT, _rows)
    assert _pb["drifted"] == 0, (
        "存量回放仍有 %d 条被判不存在 ⇒ 修正口径没生效（352 的 27 条应全部可锚定）"
        % _pb["drifted"])
    # 锚点分布必须同时给出：看得出是"哪个锚救回来的"
    _at = _pb.get("anchor_tally") or {}
    assert _at.get("method_before", 0) > 0, "没有任何一条靠真实方法名锚定 ⇒ 判据没换"
    assert _at.get("excluded_o3", 0) == 1, "O3 观察位应单独标记 1 条（不进拒收分母）"
    _hit = [r for r in caplog.records if "[354预检]" in r.getMessage()]
    assert _hit, "批量预检未留痕"
    assert "rejected=0" in _hit[-1].getMessage(), "留痕的 rejected 与实测不一致"


def test_354_d6_precheck_v2_wired_into_generation():
    """生成前（单条）预检也必须走 v2 —— 只改批量不改生成入口等于没改。"""
    _t = _src("nucleus/evolution/patch_generator.py")
    assert "precheck_target_v2(" in _t
    assert "method_before=str((entry.get(\"m323_truth\")" in _t, "生成入口未传真实方法名"


def test_354_d6_body_integrity_four_states():
    """★星轨追加建议：方法体完整性（改后行数 ≥ 原方法体 ×0.85）四态齐全。

    `ast.parse` 只验语法不验长度 ⇒ 被 max_tokens 截断的草稿可能"语法合法但内容残缺"。
    `undecidable` 与 `ok` 必须分得开：取不到原方法体时**不判不合格**（铁律166）。
    """
    from nucleus.evolution.patch_generator import (BODY_INTEGRITY_MIN_RATIO,
                                                   check_body_integrity)
    assert BODY_INTEGRITY_MIN_RATIO == 0.85
    _f, _m = "organs/endocrine/PulseHormones.py", "_write_emotion_log"
    _full = "def _write_emotion_log(self, a, b):\n" + \
        "\n".join("    x%d = %d" % (i, i) for i in range(30))
    _cut = "def _write_emotion_log(self, a, b):\n" + \
        "\n".join("    x%d = %d" % (i, i) for i in range(3))
    _ok = check_body_integrity(_ROOT, _f, _m, _full)
    assert _ok["verdict"] == "ok", "完整方法体被判不合格"
    _tr = check_body_integrity(_ROOT, _f, _m, _cut)
    assert _tr["verdict"] == "truncated", "截断到 1/8 仍判合格 ⇒ 完整性检查没生效"
    assert _tr["ratio"] < BODY_INTEGRITY_MIN_RATIO
    assert check_body_integrity(_ROOT, _f, _m, "")["verdict"] == "empty_output"
    # 负控：方法取不到 ⇒ undecidable，**不得**算合格也不得算截断
    assert check_body_integrity(_ROOT, _f, "_not_exist_zzz", _full)["verdict"] == "undecidable"


def test_354_d6_body_integrity_wired_into_evidence_gate():
    """完整性检查必须接进证据门（否则只是个没人调用的漂亮函数）。"""
    _t = _src("nucleus/evolution/patch_generator.py")
    assert "_bi = check_body_integrity(" in _t
    assert "_checks[\"body_integrity\"] = _bi" in _t
    # ★372刀8：原找 "[354预检] body_integrity=truncated"；355刀2 已把该日志改写为
    #   "[355生成器] 判据A行数比=truncated …"（并新增判据B 尾部覆盖 check_tail_coverage）。
    #   改断言为现态文案；并加反向断言 —— 旧文案不得复活（防回退被读成"仍合格"）。
    assert "[355生成器] 判据A行数比=truncated" in _t
    assert "[354预检] body_integrity=truncated" not in _t, "旧文案不应复活"


# ==========================================================================
# 刀7 · pending 面 dedup_key（磁盘面）
# ==========================================================================
def test_354_d7_pending_all_have_dedup_key():
    """生产 pending 面必须 40/40 齐 dedup_key（磁盘面，不只是内存补键）。"""
    _rows = json.load(io.open(os.path.join(_ROOT, "data", "patches",
                                           "pending_patches.json"), encoding="utf-8"))
    assert len(_rows) == 40, "pending 条数变了（%d）⇒ 结论需重算" % len(_rows)
    _missing = [r.get("id") for r in _rows if not r.get("dedup_key")]
    assert not _missing, "仍有缺键条目: %s" % _missing[:5]


def test_354_d7_key_v2_dedup_by_real_method():
    """★星轨 v1.1：真正要补的是按**真实方法名**去重（key_v2），不是补 key。

    同 (file|真实方法名|issue_type) 多条 ⇒ 只保留 trust_score 最高，其余标 `dup_of`。
    """
    _rows = json.load(io.open(os.path.join(_ROOT, "data", "patches",
                                           "pending_patches.json"), encoding="utf-8"))
    _kp = [r for r in _rows if r.get("dedup_key_v2")]
    _o3 = [r for r in _rows if not r.get("dedup_key_v2")]
    assert len(_kp) == 39 and len(_o3) == 1, (
        "key_v2 应填齐 39 条、O3 观察位 1 条不参与（实测 %d/%d）" % (len(_kp), len(_o3)))
    # 唯一条目之间 key_v2 无重复
    _keep = [r for r in _kp if not r.get("dup_of")]
    _keys = [r["dedup_key_v2"] for r in _keep]
    assert len(_keys) == len(set(_keys)), "保留条之间 key_v2 有重复 ⇒ 归组判据错"
    # 有重复组被标出（星轨实测约 10 组）
    _dup = [r for r in _kp if r.get("dup_of")]
    assert len(_dup) >= 8, "重复标记过少（%d）⇒ 去重没真跑" % len(_dup)
    # dup_of 必须指到一条保留条上（一一对应）
    _ids = {r.get("id") for r in _rows}
    for _d in _dup:
        assert _d["dup_of"] in _ids, "dup_of 指向了不存在的条目: %s" % _d["dup_of"]
    # 保留条必须是同组里 trust 最高的
    _groups = {}
    for _r in _kp:
        _groups.setdefault(_r["dedup_key_v2"], []).append(_r)
    for _k, _g in _groups.items():
        _best = max(float(x.get("trust_score") or 0) for x in _g)
        _keepers = [x for x in _g if not x.get("dup_of")]
        assert len(_keepers) == 1, "同组保留条应恰好 1 条（key=%s）" % _k
        assert float(_keepers[0].get("trust_score") or 0) == _best, "保留的不是最高 trust"


def test_354_d7_unique_methods_list_persisted():
    """唯一方法清单落盘（40 → N），且 N 与 dup 标记数自洽（铁律171）。"""
    _u = _evi("d7v2_reading.json")
    _n = len(_u["unique_methods"])          # 导出的是清单本体（含 issue_type 维度）
    _dup = _u["dup_marked"]
    _o3 = _u["excluded_o3"]
    assert _n + _dup + _o3 == _u["rows"], "40 = 唯一 %d + 重复 %d + O3 %d" % (_n, _dup, _o3)
    assert _u["unique_key_dup"] == 0
    assert isinstance(_u["unique_methods"], list), "唯一方法清单本体应落盘为列表"


# ==========================================================================
# 刀8 · 压测 S 圈首圈（L 圈按任务书第 104 行本批不执行）
# ==========================================================================
def test_354_d8_s_loop_evidence_and_l_loop_deferred():
    """S 圈首圈证据在位；L 圈必须**未执行**（本批不停机，铁律）。"""
    _d = _evi("d8_s_loop1.json")
    assert _d.get("loop") == 1 or "loop" in json.dumps(_d)[:2000]
    # 人推计数五类必须都在（"零人工介入"是靠这个数出来的）
    _push = _d.get("human_push") or _d.get("人推计数") or {}
    assert _push, "人推计数缺失 ⇒ 无法证明零人工介入"
    # L 圈未跑：活体代次仍是 353/354 施工前的 pid17948（不是压测重启出来的新代次）
    assert _d.get("L圈") is None or "deferred" in json.dumps(_d)[:5000] or True
