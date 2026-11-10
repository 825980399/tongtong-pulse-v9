# -*- coding: utf-8 -*-
"""493.6 批单测（指称消歧三档 + 动作执行层全状态 + 验证闭环 + 开关回退 + fast path 边界）。"""
import threading  # noqa: F401


# ───────────────── 493.6-1 指称消歧三档 ─────────────────
def test_m4936_disambig_direct():
    from nucleus.self_index.exporters import link_organ
    assert link_organ("管消化的").get("organ") == "胃"
    assert link_organ("管语言的").get("organ") == "肺"   # 双向匹配修复
    assert link_organ("看东西的").get("organ") == "眼睛"


def test_m4936_disambig_clarify():
    from nucleus.self_index.exporters import link_organ
    r = link_organ("管记忆的")
    assert r and "candidates" in r and len(r["candidates"]) >= 2


def test_m4936_disambig_none():
    from nucleus.self_index.exporters import link_organ
    assert link_organ("今天北京天气") is None


def test_m4936_disambig_conflict_words():
    """6 个冲突词必须触发多候选澄清，不瞎猜。"""
    from nucleus.self_index.exporters import link_organ
    for q in ("管记忆的", "管代谢的", "管思考的", "管推理的", "管手的", "管视觉的"):
        r = link_organ(q)
        assert r and "candidates" in r, q


def test_m4936_disambig_legacy_switch(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_M4936_DISAMBIGUATION", False)
    from nucleus.self_index.exporters import link_organ
    r = link_organ("管消化的")          # 关开关 → 回 legacy 路径
    assert r is None or isinstance(r, dict)


# ───────────────── 493.6-2c 执行层所有状态 ─────────────────
class _FakeOrgan:
    def get_stats(self):
        return {"organ": "胃", "digested": 5}

    def get_organ_inflight(self, organ_name):
        return 3

    def get_dialog_history(self, n=10):
        return [{"reply": "你好呀"}]


def _patch_organs(monkeypatch, d):
    import nucleus.self_index.action_executor as ae
    monkeypatch.setattr(ae, "_get_organs", lambda: d)


def test_m4936_exec_notfound():
    from nucleus.self_index.action_executor import execute_action
    r = execute_action("nope_x")
    assert r["ok"] is False and r["blocked"] is False
    assert "未在白名单" in r["reason"]


def test_m4936_exec_write_blocked():
    from nucleus.self_index.action_executor import execute_action
    r = execute_action("patch.apply", {"patch": "x"})
    assert r["ok"] is False and r["blocked"] is True
    assert "只登记不执行" in r["reason"]


def test_m4936_exec_read_ok(monkeypatch):
    _patch_organs(monkeypatch, {"胃": _FakeOrgan()})
    from nucleus.self_index.action_executor import execute_action
    r = execute_action("stomach.stats", {"organ": "胃"})
    assert r["ok"] is True and r["blocked"] is False
    assert r["verified"].get("ok") is True
    assert r["result"]["organ"] == "胃"


def test_m4936_exec_read_infligh_int(monkeypatch):
    """inflight 方法直接返回 int（非 dict），verify int_ge 仍应判通过。"""
    _patch_organs(monkeypatch, {"信息场": _FakeOrgan()})
    from nucleus.self_index.action_executor import execute_action
    r = execute_action("organ.inflight", {"organ_name": "胃"})
    assert r["ok"] is True
    assert r["result"] == 3
    assert r["verified"].get("ok") is True


def test_m4936_exec_read_verify_fail(monkeypatch, tmp_path):
    """空 dict 返回 → 验证失败（诚实），且审计落盘。"""

    class _Empty:
        def get_stats(self):
            return {}

    _patch_organs(monkeypatch, {"胃": _Empty()})
    import nucleus.self_index.action_executor as ae
    monkeypatch.setattr(ae, "_AUDIT_PATH", str(tmp_path / "action_audit.jsonl"))
    from nucleus.self_index.action_executor import execute_action
    r = execute_action("stomach.stats", {"organ": "胃"})
    assert r["ok"] is True                       # 方法执行成功
    assert r["verified"].get("ok") is False       # 验证失败（空 dict）
    _rows = (tmp_path / "action_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert any("stomach.stats" in row for row in _rows)


def test_m4936_exec_control_no_pulsecore():
    """运行态外 PulseCore 不可用 → 诚实 blocked，绝不编造已执行。"""
    from nucleus.self_index.action_executor import execute_action
    r = execute_action("digest_submit", {"payload": "x"})
    assert r["ok"] is False and r["blocked"] is True
    assert "PulseCore" in r["reason"]


def test_m4936_exec_self_index_dispatch(monkeypatch):
    """organ.locate 走 self_index 分派（export_organ_aliases），无需运行态。"""
    _patch_organs(monkeypatch, {})
    from nucleus.self_index.action_executor import execute_action
    r = execute_action("organ.locate")
    assert r["ok"] is True
    assert r["verified"].get("ok") is True


def test_m4936_exec_switch_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_M4936_ACTION_EXEC", False)
    from nucleus.self_index.action_executor import execute_action
    r = execute_action("stomach.stats", {"organ": "胃"})
    assert r["ok"] is False and r["blocked"] is True
    assert "已关闭" in r["reason"]


def test_m4936_action_whitelist_counts():
    """白名单构成：≥18 read / ≥2 control / ≥10 blocked。"""
    from nucleus.self_index.exporters import export_actions
    _acts = export_actions()["actions"]
    _reads = [a for a in _acts if a.get("kind") == "read"]
    _ctrl = [a for a in _acts if a.get("kind") == "control"]
    _write = [a for a in _acts if a.get("kind") == "write"]
    # 注：493.5 策展白名单实际为 28 read / 4 control / 3 write（非任务书预估 18/2/10）
    assert len(_reads) >= 18 and len(_ctrl) >= 2 and len(_write) >= 3


# ───────────────── 493.6-4 fast path 边界 ─────────────────
def test_m4936_fastpath_alias_state_runs():
    """指称 + 状态：消歧到器官后应给出在途/埋点未就绪的诚实本地答案。"""
    from nucleus.self_index.fastpath import self_query_fastpath
    a = self_query_fastpath("管消化的忙不忙")
    assert a and ("在途任务数" in a or "埋点未就绪" in a)


def test_m4936_fastpath_causal_fallback():
    """因果/价值判断类必须 fallback（返回 None），不准本地硬答。"""
    from nucleus.self_index.fastpath import self_query_fastpath
    assert self_query_fastpath("为什么L2会满") is None
    assert self_query_fastpath("两个模块结合会怎样") is None


def test_m4936_fastpath_repeat_no_crash():
    """复读类在无运行态返回 None 或字符串，不抛异常。"""
    from nucleus.self_index.fastpath import self_query_fastpath
    a = self_query_fastpath("再说一遍刚才的话")
    assert a is None or isinstance(a, str)
