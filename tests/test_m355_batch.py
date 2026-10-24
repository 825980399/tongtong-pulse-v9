# -*- coding: utf-8 -*-
"""355 批（40 补丁清零专项 · 停框架批）核心路径测试。

覆盖：刀0 快照回收证据、刀1 发布契约、刀2 双判据（含 94e9 尾部覆盖专验）、
刀4 守卫模板（含负控）、刀8/刀9 拒绝死因与清零终态。

纪律：只读生产文件做源码断言；不加载 722MB 主快照（改为读证据件 + 文件头），
不串联器官实例（停机态，避免拉起线程）。
"""
import io
import json
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EVID = os.path.join(_ROOT, "tmp", "p355_root")
_PEND = os.path.join(_ROOT, "data", "patches", "pending_patches.json")
_APPLIED = os.path.join(_ROOT, "data", "patches", "applied_355.json")
_REJECTED = os.path.join(_ROOT, "data", "patches", "rejected_355.json")


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8",
                   newline="").read().replace("\r\n", "\n")


def _method(rel, name):
    """抽取方法源码（按缩进边界）。"""
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


def _json(path):
    return json.load(io.open(path, encoding="utf-8"))


# ==========================================================================
# 刀0 · 快照体积回收（证据件 + 主快照文件头）
# ==========================================================================
def test_355_d0_snapshot_manifest_consistent():
    """被删 bak 记在册、保留 bak 仍在盘上，且回收量算术自洽。"""
    d = _json(os.path.join(_EVID, "d0_reading.json"))
    assert d["applied"] is True
    assert d["deleted_absent_after"] is True, "被删 bak 仍在盘上"
    keep = os.path.join(_ROOT, "data", "knowledge", d["keep"]["name"])
    assert os.path.isfile(keep), "保留的 bak 不见了"
    assert os.path.getsize(keep) == d["keep"]["bytes"]
    drop = os.path.join(_ROOT, "data", "knowledge", d["drop"]["name"])
    assert not os.path.exists(drop), "应删的旧 bak 还在"
    assert d["drop"]["mb"] >= 700.0, "回收量不足 700MB（任务书验收线）"


def test_355_d0_main_snapshot_loadable_head():
    """主快照头部可解析（不整体加载 722MB，避免测试拖死）。"""
    p = os.path.join(_ROOT, "data", "knowledge", "pulse_knowledge_snapshot.json")
    assert os.path.isfile(p)
    head = io.open(p, "rb").read(4096).lstrip()
    assert head[:1] == b"{", "主快照不是 JSON object 开头"
    _hd = io.open(p, "rb").read(200000)
    assert b'"version"' in _hd and b'"node_count_at_save"' in _hd, "主快照缺少版本/节点计数段"


# ==========================================================================
# 刀1 · 脉冲发布契约
# ==========================================================================
def test_355_d1_contract_doc_exists():
    p = os.path.join(_ROOT, "docs", "路灯与星轨对话", "交付报告", "脉冲发布契约_355.md")
    assert os.path.isfile(p)
    t = io.open(p, encoding="utf-8").read()
    for k in ("self._emit", "EventBus", "InsightBoard", "[355契约]"):
        assert k in t, "契约文档缺少 %s" % k


def test_355_d1_no_pulsebus_in_production():
    """生产面禁止 PulseBus / get_pulse_bus / 小写 pulse_bus 模块引用。"""
    bad = []
    roots = [os.path.join(_ROOT, "organs"), os.path.join(_ROOT, "nucleus"),
             os.path.join(_ROOT, "base")]
    files = [os.path.join(_ROOT, "config.py"), os.path.join(_ROOT, "main.py")]
    for r in roots:
        for dirpath, _dn, fn in os.walk(r):
            if "__pycache__" in dirpath:
                continue
            files += [os.path.join(dirpath, f) for f in fn if f.endswith(".py")]
    for f in files:
        t = io.open(f, encoding="utf-8", errors="ignore").read()
        for m in re.finditer(r"^\s*(?:from|import)\s+nucleus[.\w]*[Pp]ulse_?[Bb]us", t, re.M):
            bad.append((os.path.basename(f), m.group(0).strip()))
    assert not bad, "生产面仍有自造总线引用: %s" % bad[:5]


def test_355_d1_three_entrypoints_present():
    """三件套必须在位：_emit / EventBus.publish / InsightBoard.post。"""
    assert "def _emit(" in _src("base/BasePulseOrgan.py")
    eb = _src("nucleus/events/EventBus.py")
    assert "def publish(" in eb and "def publish_async(" in eb
    assert "def post(" in _src("nucleus/InsightBoard.py")


# ==========================================================================
# 刀2 · 方法体完整性双判据（含 94e9 尾部覆盖专验）
# ==========================================================================
def test_355_d2_tail_coverage_catches_truncation():
    """行数比过关但尾部丢了 ⇒ 判据 B 必须独立拦住（94e9 形态）。"""
    from nucleus.evolution.patch_generator import check_tail_coverage
    _full = _method("organs/core/PulseSystemManager.py", "_auto_health_check")
    _cut = "\n".join(_full.split("\n")[:4])          # 只留签名 + 两行
    _r = check_tail_coverage(_ROOT, "organs/core/PulseSystemManager.py",
                             "_auto_health_check", _cut)
    assert _r["verdict"] == "truncated", "尾部覆盖判据失效"
    assert _r["ratio"] < 0.8


def test_355_d2_tail_coverage_passes_on_full_body():
    from nucleus.evolution.patch_generator import check_tail_coverage
    _full = _method("organs/core/PulseSystemManager.py", "_auto_health_check")
    _r = check_tail_coverage(_ROOT, "organs/core/PulseSystemManager.py",
                             "_auto_health_check", _full)
    assert _r["verdict"] == "ok"
    assert _r["ratio"] >= 0.8


def test_355_d2_line_ratio_still_wired():
    """判据 A 不能被判据 B 顶掉：两者都在。"""
    from nucleus.evolution import patch_generator as PG
    _t = _src("nucleus/evolution/patch_generator.py")
    assert "BODY_INTEGRITY_MIN_RATIO" in _t
    assert "def check_body_integrity(" in _t
    assert "def check_tail_coverage(" in _t
    _r = PG.check_body_integrity(_ROOT, "organs/core/PulseSystemManager.py",
                                 "_auto_health_check",
                                 "def _auto_health_check(self):\n    return\n")
    assert _r["verdict"] == "truncated"


# ==========================================================================
# 刀4 · 守卫模板重写（标志在回调入口置位 / 回调结束复位）
# ==========================================================================
def test_355_d4_guard_set_at_callback_entry():
    """健康检查：标志必须在回调 _auto_health_check 内、且在 try 之前置位。"""
    body = _method("organs/core/PulseSystemManager.py", "_auto_health_check")
    assert "_health_check_in_progress = True" in body
    _i_flag = body.index("_health_check_in_progress = True")
    _i_try = body.index("try:")
    assert _i_flag < _i_try, "标志没有在回调入口（try 之前）置位"


def test_355_d4_guard_released_in_callback_finally():
    """复位必须在回调自己的 finally 里，且续期不丢。"""
    body = _method("organs/core/PulseSystemManager.py", "_auto_health_check")
    _fin = body[body.index("finally:"):]
    assert "_health_check_in_progress = False" in _fin
    assert "_schedule_health_check()" in _fin, "finally 里没有续期，链会停摆"


def test_355_d4_guard_not_in_scheduler_finally():
    """负控：调度函数 _schedule_health_check 内**不得**出现标志复位（旧模板形态）。"""
    body = _method("organs/core/PulseSystemManager.py", "_schedule_health_check")
    assert "_health_check_in_progress" not in body, "旧模板（调度函数里复位）又回来了"


def test_355_d4_start_idempotent_and_snapshot_guard():
    _s = _method("organs/motor/PulseLegs.py", "start")
    assert "_learn_worker_running" in _s and "return" in _s
    assert _s.index("_learn_worker_running") < _s.index("super().start()"), "幂等守卫必须在 start 之前"
    _t = _method("organs/senses/PulseTouch.py", "_trigger_snapshot")
    assert "_snapshot_in_progress = True" in _t
    _fin = _t[_t.index("finally:"):]
    assert "_schedule_next_snapshot()" in _fin, "快照续期不在 finally，异常即停摆"


def test_355_d4_emit_moved_out_of_lock():
    """lock_with_emit：_emit 必须在 with self._lock 之外。"""
    body = _method("organs/core/PulseMotivationCycle.py", "_emit_motivation_signals")
    _lines = body.split("\n")
    _w = [i for i, l in enumerate(_lines) if l.strip() == "with self._lock:"][0]
    _e = [i for i, l in enumerate(_lines) if "self._emit(Event.MOTIVATION_URGE" in l][0]
    assert _e > _w
    # _emit 那行的缩进必须等于 with 的缩进（即已退出 with 块），而不是 with 体缩进
    _with_ind = len(_lines[_w]) - len(_lines[_w].lstrip())
    # with 块的结束边界 = 其后第一条非空且缩进 <= with 的行
    _end = len(_lines)
    for j in range(_w + 1, len(_lines)):
        s = _lines[j]
        if s.strip() and (len(s) - len(s.lstrip())) <= _with_ind:
            _end = j
            break
    assert _e > _end, "_emit 仍在 with self._lock 块内（第 %d 行，块止于 %d 行）" % (_e, _end)


def test_355_d4_atomic_write_applied():
    """原子写：临时文件 + os.replace，且清理路径调 silent_exc（不静默）。"""
    for rel, name in (("organs/core/PulseProprioception.py", "_save_report_file"),
                      ("organs/endocrine/PulseHormones.py", "_write_emotion_log")):
        body = _method(rel, name)
        assert "os.replace(" in body, "%s 未原子写" % name
        assert "os.fsync(" in body, "%s 未落盘同步" % name


def test_355_d4_session_closed_on_failure():
    body = _method("organs/motor/PulseLegs.py", "_create_session")
    assert "except BaseException:" in body
    _tail = body[body.index("except BaseException:"):]
    assert "session.close()" in _tail and "raise" in _tail


# ==========================================================================
# 刀8 / 刀9 · 拒绝死因与清零终态
# ==========================================================================
def test_355_d89_reject_reasons_greppable():
    doc = _json(_REJECTED)
    assert doc["count"] == 24
    _miss = [r.get("id") for r in doc["rows"]
             if "[355拒绝]" not in str(r.get("reject_reason") or "")]
    assert not _miss, "有拒绝条缺 [355拒绝] 死因: %s" % _miss[:3]


def test_355_d89_two_named_rejects_have_expected_dying_reason():
    """刀8 点名的 251/556：死因须分别落到"重复且语义旧"/"读不存在文件"。"""
    rows = {str(r.get("id"))[-4:]: r for r in _json(_REJECTED)["rows"]}
    assert "重复" in rows["747e"]["reject_reason"]
    assert "不存在文件" in rows["ab6b"]["reject_reason"]


def test_355_d89_cross_all_rejected_as_false_positive():
    """15 条 cross 全部假阳性：改文引用不存在的总线，原调用合法。"""
    doc = _json(_REJECTED)
    _cross = [r for r in doc["rows"] if r.get("issue_type") == "cross_module_singleton_call"]
    assert len(_cross) == 15
    for r in _cross:
        assert "假阳性" in r["reject_reason"], r.get("id")


def test_355_d89_pending_zeroed_except_observation():
    """终态：pending 只剩判死档观察位 1 条。"""
    rows = _json(_PEND)
    assert len(rows) == 1, "pending 未清零，剩 %d 条" % len(rows)
    assert str(rows[0].get("id")).endswith("1b31fee790d6e9")
    assert rows[0].get("issue_type") == "dead_code_candidate"


def test_355_d89_applied_rows_are_static_verified():
    doc = _json(_APPLIED)
    assert doc["count"] == 7
    for r in doc["rows"]:
        assert r["applied"] is True
        assert r["problem_fixed"] == "static_verified"
        assert r["verification_granularity"] == "static_only", "不得谎报运行时验证"
        assert "[355清零]" in str(r.get("apply_note") or "")


def test_355_d89_applied_methods_exist_after_apply():
    """落地的 7 条：def 名必须就是 m323_truth.method_before（刀2 归一后处理）。"""
    for r in _json(_APPLIED)["rows"]:
        name = (r.get("m323_truth") or {}).get("method_before")
        assert name, r.get("id")
        assert _method(r["file"], name), "%s 里没有 %s" % (r["file"], name)


# ==========================================================================
# 门面负控：口径自洽
# ==========================================================================
def test_355_counts_add_up():
    """7 应用 + 24 拒绝 + 1 观察 = 刀3 之后的 32 条。"""
    a = _json(_APPLIED)["count"]
    rj = _json(_REJECTED)["count"]
    p = len(_json(_PEND))
    assert a + rj + p == 32, "账不平: %d+%d+%d" % (a, rj, p)
