# -*- coding: utf-8 -*-
"""185批 门控测试（2刀全零停窗）。

刀1 卫生刀：
  - ① 判据命中日志：182刀1第④条判据（反思低置信盲区→升级LLM）命中时记 INFO（触发领域+低置信标记）。
    以 AST 验证日志存在且判定调用传入盲区词表（不改判定逻辑）。
  - ② 入板计数日志：cognitive_reflection 发布到洞察黑板时记 INFO（节点对+置信度+下游max_age）。
    行为验证：_run_periodic_reflection 发布后仍记该 INFO 日志且含三项字段。
刀2 人格快照落盘 + 启动哈希比对告警：
  - 工具函数：首次落盘基线 / 读哈希 / 比对漂移。
  - 内核启动：无基线→落盘基线且不当 ALARM；落盘与当前构造不符→CRITICAL（不自动回滚）。
"""
import ast
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402
from organs.identity.PulsePersonalityKernel import PulsePersonalityKernel  # noqa: E402
from tools.export_personality_snapshot import (  # noqa: E402
    write_personality_snapshot,
    read_personality_snapshot_hash,
    compare_personality_snapshot_at_startup,
)

_IW_PATH = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")


# ════════ 刀1 ① 判据命中日志（AST 静态验证） ════════
def test_blade1_blind_domain_hit_log_present():
    """181刀2 升级判定块内须存在 ④判据命中 INFO 日志（触发领域+低置信标记），且判定调用传入盲区词表。"""
    src = open(_IW_PATH, encoding="utf-8").read()
    tree = ast.parse(src)
    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            t = node.test
            if isinstance(t, ast.Call):
                fname = t.func.id if isinstance(t.func, ast.Name) else getattr(t.func, "attr", "")
                if (fname == "_m181_should_escalate_on_quality" and
                        any(isinstance(kw, ast.keyword) and kw.arg == "blind_domains"
                             for kw in t.keywords)):
                    candidates.append(node)
    assert candidates, "未找到传入 blind_domains 的 _m181_should_escalate_on_quality 判定 If"

    found = False
    for target in candidates:
        for n in ast.walk(target):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_log":
                for a in n.args:
                    m = ast.unparse(a)
                    if "④判据命中" in m and "触发领域" in m and "低置信标记" in m:
                        found = True
                        break
            if found:
                break
        if found:
            break
    assert found, "④判据命中日志缺失（需含 触发领域 + 低置信标记）"


# ════════ 刀1 ② 入板计数日志（行为验证） ════════
class _MockBoard:
    def __init__(self):
        self.posted = False
        self.posted_type = None
        self.posted_conf = None

    def post(self, insight_type, content, source_loop, related_dimension="", confidence=0.5, keywords=None):
        self.posted = True
        self.posted_type = insight_type
        self.posted_conf = confidence
        return "insight_x"

    def query(self, *a, **k):
        return []


def test_blade1_cr_board_entry_log():
    """cognitive_reflection 入板须记 INFO 日志（节点对+置信度+下游max_age）。"""
    iw = PulseInnerWorld("内在世界")
    board = _MockBoard()
    iw._insight_board = board
    iw._inference_trace = []
    iw._cognitive_reflection = lambda: "stub cognitive reflection"
    captured = []
    iw._log = lambda level, msg, *a, **k: captured.append((level, msg))
    import random
    _orig = random.random
    random.random = lambda: 0.99  # 抑制跨域类比随机分支
    try:
        iw._run_periodic_reflection()
    finally:
        random.random = _orig
    assert board.posted and board.posted_type == "cognitive_reflection"
    msgs = [m for _lvl, m in captured if "cognitive_reflection入板" in m]
    assert msgs, "应记录 cognitive_reflection 入板 INFO 日志"
    msg = msgs[0]
    assert "节点对" in msg and "置信度" in msg and "max_age" in msg


# ════════ 刀2 人格快照落盘 + 启动哈希比对告警 ════════
def test_blade2_tools_roundtrip(tmp_path):
    """工具函数：首次落盘基线 / 读哈希一致 / 篡改后比对报漂移。"""
    k = PulsePersonalityKernel()
    snap = str(tmp_path / "snap.json")
    assert write_personality_snapshot(k, path=snap) is True
    assert os.path.isfile(snap)
    assert read_personality_snapshot_hash(path=snap) == k._compute_l3_anchors_hash()
    # 篡改落盘基线哈希
    d = json.loads(open(snap, encoding="utf-8").read())
    d["l3_anchors_hash"] = "sha256:deadbeef"
    open(snap, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False))
    disk, cur, mismatch = compare_personality_snapshot_at_startup(k, path=snap)
    assert mismatch is True and disk == "sha256:deadbeef"


def test_blade2_kernel_startup_first_run_writes_baseline(tmp_path, monkeypatch):
    """内核启动：无落盘基线→落盘基线且不发 ALARM。"""
    snap = tmp_path / "snap.json"
    monkeypatch.setenv("PULSE_PERSONALITY_SNAPSHOT_PATH", str(snap))
    captured = []
    _orig = PulsePersonalityKernel._log
    monkeypatch.setattr(PulsePersonalityKernel, "_log",
                        lambda self, lvl, msg, *a, **k: captured.append((lvl, msg)))
    try:
        PulsePersonalityKernel()
    finally:
        monkeypatch.setattr(PulsePersonalityKernel, "_log", _orig)
    assert snap.exists(), "首次启动应落盘基线快照"
    assert not any("哈希漂移" in m for _lvl, m in captured), "首次启动不应 ALARM"
    data = json.loads(snap.read_text(encoding="utf-8"))
    assert data.get("l3_anchors_hash", "").startswith("sha256:")


def test_blade2_kernel_startup_mismatch_alarms(tmp_path, monkeypatch):
    """内核启动：落盘基线与当前构造不符→ALARM（不自动回滚）。"""
    snap = tmp_path / "snap.json"
    monkeypatch.setenv("PULSE_PERSONALITY_SNAPSHOT_PATH", str(snap))
    # 第一次构造写入正确基线
    PulsePersonalityKernel()
    assert snap.exists()
    # 篡改落盘基线哈希
    d = json.loads(snap.read_text(encoding="utf-8"))
    d["l3_anchors_hash"] = "sha256:deadbeefdeadbeef"
    snap.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    # 第二次构造应 ALARM
    captured = []
    _orig = PulsePersonalityKernel._log
    monkeypatch.setattr(PulsePersonalityKernel, "_log",
                        lambda self, lvl, msg, *a, **k: captured.append((lvl, msg)))
    try:
        PulsePersonalityKernel()
    finally:
        monkeypatch.setattr(PulsePersonalityKernel, "_log", _orig)
    alarms = [m for _lvl, m in captured if "哈希漂移" in m]
    assert alarms, "哈希不符应发出 ALARM 日志"
    assert "sha256:deadbeefdeadbeef" in alarms[0]
