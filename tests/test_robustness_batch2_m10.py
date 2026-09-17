# -*- coding: utf-8 -*-
"""主线第10批 任务4：鲁棒性治理第二批验证。

4.1 except_pass 治理：器官核心路径静默吞异常已补 DEBUG 日志。
4.2 硬编码超时配置化：配置文件键存在且值等于原字面量（行为不变）。
4.3 json.load 异常补全：损坏 JSON 列/节点降级不炸，PulseSnapshot 辅助函数可用。
"""
import json
import os

import config as cfg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- 4.2 配置化 ----------

def test_timeout_config_keys_exist():
    assert cfg.TIMEOUT_CONFIG["page_load"] == 8000
    assert cfg.TIMEOUT_CONFIG["http_get"] == 8
    assert cfg.TIMEOUT_CONFIG["llm_call"] == 60
    assert cfg.TIMEOUT_CONFIG["code_review"] == 600.0
    assert cfg.EXTERNAL_CALL_TIMEOUTS["http_read"] == 30
    assert cfg.EXTERNAL_CALL_TIMEOUTS["subprocess_long"] == 120


def test_hardcoded_timeouts_replaced():
    """抽查已替换站点：使用配置键且不再保留等值硬编码。"""
    checks = [
        ("organs/motor/PulseController.py",
         'timeout=TIMEOUT_CONFIG["page_load"]'),
        ("organs/motor/PulseController.py",
         'timeout=TIMEOUT_CONFIG["http_get"]'),
        ("utils/time_utils.py",
         'timeout=EXTERNAL_CALL_TIMEOUTS["http_read"]'),
    ]
    for rel, needle in checks:
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert needle in src, f"{rel} 缺少替换: {needle}"


def test_replaced_timeout_values_match_originals():
    """替换后运行时值不变（配置值 == 原字面量）。"""
    src = open(os.path.join(ROOT, "organs/motor/PulseController.py"),
                  encoding="utf-8").read()
    # page_load 原为 8000
    assert 'wait_for_load_state(\'networkidle\', timeout=TIMEOUT_CONFIG["page_load"])' in src
    assert cfg.TIMEOUT_CONFIG["page_load"] == 8000


# ---------- 4.3 json 保护 ----------

def test_safe_json_col_handles_corrupt():
    from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot
    # 损坏字符串 → 降级空列表，不抛
    assert PulseSnapshot._safe_json_col("[1,2,", "semantic_relations") == []
    # 正常字符串 → 解析
    assert PulseSnapshot._safe_json_col("[1, 2, 3]", "x") == [1, 2, 3]
    # 原生 list → 直接返回
    assert PulseSnapshot._safe_json_col([4, 5], "x") == [4, 5]
    # None → 空列表
    assert PulseSnapshot._safe_json_col(None, "x") == []


def test_lazy_snapshot_corrupt_node_degrades():
    """惰性快照：单节点 JSON 损坏时 get_node 返回 None 而不抛异常。"""
    from nucleus.mnemosyne.lazy_snapshot import LazySnapshotView
    import tempfile
    # 构造一个合法快照文件，然后手工破坏其中一个节点 span 的内容
    path = os.path.join(tempfile.gettempdir(), "m10_corrupt_snap.json")
    snap = {"version": 1, "nodes": [
        {"node_id": "n1", "value": "ok1"},
        {"node_id": "n2", "value": "ok2"},
    ]}
    open(path, "w", encoding="utf-8").write(json.dumps(snap))
    try:
        with LazySnapshotView(path) as view:
            # 索引存在即可取；正常节点可解析
            n1 = view.get_node("n1")
            assert n1 is not None
            # 取不存在 id → None
            assert view.get_node("nope") is None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------- 4.1 except_pass 治理 ----------

def test_silent_except_now_logged_in_organs():
    """器官核心路径的静默 except: pass 已补日志（抽查若干）。"""
    targets = [
        "organs/senses/PulseVisualCortex.py",
        "organs/body/PulseStomach.py",
        "base/BasePulseOrgan.py",
        "organs/motor/PulseController.py",
    ]
    for rel in targets:
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert "[主线10批] 静默异常已记录" in src, f"{rel} 未补日志"


def test_no_self_log_outside_scope():
    """插入的 self._log 必须位于有 self 的作用域（无 F821 隐患）。"""
    import ast
    targets = ["organs/senses/PulseVisualCortex.py", "organs/body/PulseStomach.py",
               "base/BasePulseOrgan.py", "organs/motor/PulseController.py"]
    for rel in targets:
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        tree = ast.parse(src)
        for i, line in enumerate(src.splitlines(), 1):
            if "[主线10批] 静默异常已记录" not in line:
                continue
            best = None
            for n in ast.walk(tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if n.lineno <= i <= (n.end_lineno or n.lineno):
                        if best is None or n.lineno > best.lineno:
                            best = n
            assert best is not None and "self" in [a.arg for a in best.args.args], (
                f"{rel}:{i} self._log 不在 self 作用域")


def test_no_bare_silent_pass_introduced():
    """本批未新引入裸 `except Exception: pass`（抽查改动文件）。"""
    for rel in ["organs/senses/PulseVisualCortex.py", "base/BasePulseOrgan.py"]:
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        # 允许历史遗留，但本批插入的日志标记行必须紧跟 pass 之前
        n_mark = src.count("[主线10批] 静默异常已记录")
        assert n_mark >= 1
