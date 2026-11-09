# -*- coding: utf-8 -*-
"""480 刀13–17：尾巴清扫的判据集合。

## 判据来源（全部来自 2026-11-09 活体日志 / 源码实测）

  · 12:22:11 ERROR `'SafeEvolutionExecutor' object has no attribute '_module_logger'`
    ⇒ mode=execute 子进程整轮被外层 except 吞掉（刀13①）。
  · 13:09:59 探针 `status_mem/status_tmp/status_disk` 三处都是 `approved:1`，
    而外部读盘 `verified:6`、sha256 与几小时前相同
    ⇒ **写口返回之后被旧快照整体写回冲掉**（刀13② + 刀14）。
  · 台账 C8/C10/C12 三项经逐条核实为真，本轮按裁决落地。

## 这些测试**不**证明什么

不证明生产上 approved 一定能落地（那要活体跑 + 看下一次写口日志的
`inherited=` 字段）；这里只证明「旧快照回退在写口这一层被拦住了」。
"""
import json
import os
import sys
from collections import deque

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from nucleus.evolution import pending_write as pw


def _approved_row(pid="p1"):
    return {"patch_id": pid, "file": "a.py", "method": "m", "status": "approved",
            "approved_source": "auto", "approved_signer": "auto:bridge_447",
            "approved_at": 1700000000.0, "approved_judged_by": "machine",
            "approved_basis": {"gate": "all_pass"}}


def _stale_row(pid="p1"):
    return {"patch_id": pid, "file": "a.py", "method": "m", "status": "verified"}


@pytest.fixture
def clean_write(monkeypatch, tmp_path):
    """隔离写口：pending 放 tmp；406 审计留痕与本刀无关，直接短路，零污染。"""
    p = str(tmp_path / "pending_patches.json")
    monkeypatch.setattr(pw, "_m406_annotate",
                        lambda payload, writer, root=None: {"changed": 0, "written": 0})
    return p


def _disk(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ==================== 刀14：状态单调保护 ====================

def test_01_stale_snapshot_cannot_erase_approved(clean_write):
    """核心判据：盘上 approved，调用方拿旧快照整体写回 ⇒ 仍必须 approved。"""
    p = clean_write
    pw.write_pending([_approved_row()], path=p, writer="setup")
    assert _disk(p)[0]["status"] == "approved"

    pw.write_pending([_stale_row()], path=p, writer="stale_writer")
    row = _disk(p)[0]
    assert row["status"] == "approved"
    assert row["approved_at"] == 1700000000.0
    assert row["approved_source"] == "auto"
    assert row["approved_basis"] == {"gate": "all_pass"}


def test_02_inherit_returns_count(clean_write):
    p = clean_write
    pw.write_pending([_approved_row()], path=p, writer="setup")
    stale = [_stale_row()]
    assert pw._m480_inherit_approvals(stale, p) == 1
    assert stale[0]["status"] == "approved"


def test_03_explicit_revoke_is_not_blocked(clean_write):
    """有意撤销会保留 approved_at ⇒ 不命中旧快照判据，绝不能被拦。"""
    p = clean_write
    pw.write_pending([_approved_row()], path=p, writer="setup")
    revoked = {"patch_id": "p1", "status": "rejected",
               "approved_at": 1700000000.0, "revoked_at": 1700000001.0}
    pw.write_pending([revoked], path=p, writer="revoker")
    assert _disk(p)[0]["status"] == "rejected"


def test_04_flag_off_restores_old_behavior(clean_write, monkeypatch):
    p = clean_write
    pw.write_pending([_approved_row()], path=p, writer="setup")
    monkeypatch.setattr(pw, "_m480_monotonic_on", lambda: False)
    pw.write_pending([_stale_row()], path=p, writer="stale_writer")
    assert _disk(p)[0]["status"] == "verified"


def test_05_no_approved_on_disk_is_noop(clean_write):
    p = clean_write
    pw.write_pending([_stale_row()], path=p, writer="setup")
    assert pw._m480_inherit_approvals([_stale_row()], p) == 0
    assert _disk(p)[0]["status"] == "verified"


def test_06_dict_wrapped_payload_also_protected(clean_write):
    """pending 有两种容器形态，保护必须都认。"""
    p = clean_write
    pw.write_pending({"patches": [_approved_row()]}, path=p, writer="setup")
    pw.write_pending({"patches": [_stale_row()]}, path=p, writer="stale_writer")
    assert _disk(p)["patches"][0]["status"] == "approved"


# ==================== 刀13：源码契约（防回潮） ====================

def test_07_no_self_module_logger():
    """★判据必须是**代码**而不是文本：注释里提到这个属性名是允许的。"""
    import ast
    with open(os.path.join(_ROOT, "nucleus", "reasoning",
                           "SafeEvolutionExecutor.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read().replace("\r\n", "\n"))
    bad = [n for n in ast.walk(tree)
           if isinstance(n, ast.Attribute) and n.attr == "_module_logger"
           and isinstance(n.value, ast.Name) and n.value.id == "self"]
    assert bad == [], f"仍有 {len(bad)} 处 self._module_logger"


def test_08_no_bare_pending_write():
    with open(os.path.join(_ROOT, "nucleus", "reasoning",
                           "PatchManager.py"), encoding="utf-8") as f:
        src = f.read()
    assert 'open(self._pending_file, "w"' not in src
    assert 'writer="PatchManager.sweep_runtime_failed"' in src


# ==================== C8：谨慎模式（裁决=补读点+TTL，不接决策链） ====================

def _cortex():
    from organs.brain.PulseCortex import PulseCortex
    o = PulseCortex.__new__(PulseCortex)
    o._risk_cautious_mode = True
    o._risk_cautious_since = 1000.0
    o._risk_cautious_trigger = "risk_moderate:probe"
    return o


def test_09_cautious_state_readable():
    st = _cortex().get_risk_cautious_state(now=1100.0)
    assert st["active"] is True
    assert st["trigger"] == "risk_moderate:probe"
    assert st["age_sec"] == pytest.approx(100.0)


def test_10_not_expired_stays_active():
    st = _cortex().get_risk_cautious_state(now=1000.0 + 3599.0)
    assert st["active"] is True


def test_11_ttl_resets_the_ratchet():
    o = _cortex()
    st = o.get_risk_cautious_state(now=1000.0 + 3601.0)
    assert st["active"] is False
    assert o._risk_cautious_mode is False       # ★真的复位，不只是返回值
    assert o._risk_cautious_since is None


def test_12_c8_wiring_both_sites():
    with open(os.path.join(_ROOT, "organs", "brain",
                           "PulseCortex.py"), encoding="utf-8") as f:
        src = f.read()
    assert src.count("self._m480_enter_risk_cautious(") == 2
    assert "def get_risk_cautious_state" in src


# ==================== C10：周期精神校验不再空转 ====================

def _kernel():
    from organs.identity.PulsePersonalityKernel import PulsePersonalityKernel
    k = PulsePersonalityKernel.__new__(PulsePersonalityKernel)
    k._core_anchors = {"identity": "曈曈", "father": "F", "brother": "B",
                       "mission": "M", "sister": "S"}
    k._core_values = ["善良"]
    k._immutable_boundaries = ["不伤害"]
    k._baseline_protected_terms = {"曈曈", "善良"}
    return k


def test_13_own_baseline_intact():
    r = _kernel().check_own_baseline_intact()
    assert r["safe"] is True
    assert r["missing"] == []
    assert r["anchors"] == 5 and r["terms"] == 2


def test_14_missing_anchor_detected():
    k = _kernel()
    k._core_anchors["identity"] = ""
    r = k.check_own_baseline_intact()
    assert r["safe"] is False
    assert "anchor:identity" in r["missing"]


def test_15_c10_has_real_check_and_rollback_branch():
    with open(os.path.join(_ROOT, "organs", "identity",
                           "PulseSpiritConstitution.py"), encoding="utf-8") as f:
        src = f.read()
    assert "check_own_baseline_intact()" in src
    assert 'proposed_content=""' in src        # A/B 回滚分支仍在
    assert 'result["spirit_integrity"]' in src  # 影子留痕


# ==================== C12：游标读取（老客户端零感知） ====================

@pytest.fixture
def reply_queue(monkeypatch):
    import functions.web_chat as wc
    q = deque(maxlen=200)
    monkeypatch.setattr(wc, "_reply_queue", q)
    monkeypatch.setattr(wc, "_reply_seq", 0)
    return wc, q


def test_16_legacy_pop_still_clears(reply_queue):
    wc, q = reply_queue
    wc.push_reply("a")
    wc.push_reply("b")
    got = wc.pop_replies()                     # 不传 since ⇒ 逐字旧行为
    assert [r["content"] for r in got] == ["a", "b"]
    assert len(q) == 0


def test_17_cursor_does_not_steal_other_clients_replies(reply_queue):
    wc, q = reply_queue
    wc.push_reply("a")                         # seq=1
    first = wc.pop_replies(since_id=0)
    assert [r["content"] for r in first] == ["a"]
    assert len(q) == 1                         # ★不清空

    wc.push_reply("b")                         # seq=2
    a_again = wc.pop_replies(since_id=1)       # 客户端 A 的游标
    b_first = wc.pop_replies(since_id=0)       # 客户端 B 从头读
    assert [r["content"] for r in a_again] == ["b"]
    assert [r["content"] for r in b_first] == ["a", "b"]
    assert len(q) == 2                         # 谁都没取走队列


def test_18_cursor_flag_off_falls_back(reply_queue, monkeypatch):
    wc, q = reply_queue
    monkeypatch.setattr(wc, "_m480_cfg",
                        lambda name, default: False if name == "ENABLE_M480_REPLY_CURSOR" else default)
    wc.push_reply("a")
    wc.push_reply("b")
    assert len(wc.pop_replies(since_id=1)) == 2
    assert len(q) == 0
