# -*- coding: utf-8 -*-
"""444 刀4/刀5：告警阈值 + 补丁判废（方法不存在 / O3 空码候选）。"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.evolution.PatchAutoApprover import PatchAutoApprover
from organs.core.PulseMetricsCollector import PulseMetricsCollector


# ---------------------------------------------------------------- 刀4
def test_thread_warn_threshold_is_raised():
    """稳态线程 120+，阈值必须 ≥150（50 时实测 10 小时刷 283 条）。"""
    assert int(getattr(config, "PROC_THREADS_WARN", 0)) >= 150


def test_memory_growth_threshold_is_raised():
    """增长率线从 10 MB/min 抬到 50（10 小时 232 条全是抖动）。"""
    assert float(getattr(config, "MEMORY_GROWTH_ALARM_MB_PER_MIN", 0)) >= 50.0


def test_resource_probe_does_not_warn_on_normal_thread_count():
    """正常进程（线程 <150）不该报超阈值。"""
    _c = object.__new__(PulseMetricsCollector)
    _c._m382_probe_beats = 0
    _c._log = lambda *a, **k: None
    _out = PulseMetricsCollector._m382_resource_probe(_c)
    assert _out["threads"] > 0, _out
    assert _out["warned"] is False, _out
    assert _out["why"] == "", _out


# ---------------------------------------------------------------- 刀5
def _mk_repo(tmp_path, body: str, method: str = "update"):
    _rel = "organs/fake_organ.py"
    _p = tmp_path / _rel
    _p.parent.mkdir(parents=True, exist_ok=True)
    _p.write_text(body, encoding="utf-8")
    return _rel


def test_obsolete_when_method_missing_but_body_still_present(tmp_path, monkeypatch):
    """★本刀核心：方法本身没了 ⇒ obsolete，哪怕那段方法体还在别的函数里。"""
    _body = ("class A:\n"
             "    def other(self):\n"
             "        if not self._running:\n"
             "            return\n")
    _rel = _mk_repo(tmp_path, _body)
    monkeypatch.setattr("nucleus.evolution.PatchAutoApprover._project_root",
                        lambda: str(tmp_path), raising=True)
    _pa = PatchAutoApprover()
    _patch = {"file": _rel, "method": "update",
              "original_code": "        if not self._running:\n            return\n"}
    assert _patch["original_code"] in _body, "前提：方法体仍是文件子串"
    assert _pa.is_obsolete(_patch) is True


def test_not_obsolete_when_method_exists(tmp_path, monkeypatch):
    _body = ("class A:\n"
             "    def update(self):\n"
             "        if not self._running:\n"
             "            return\n")
    _rel = _mk_repo(tmp_path, _body)
    monkeypatch.setattr("nucleus.evolution.PatchAutoApprover._project_root",
                        lambda: str(tmp_path), raising=True)
    _pa = PatchAutoApprover()
    _patch = {"file": _rel, "method": "update",
              "original_code": "        if not self._running:\n            return\n"}
    assert _pa.is_obsolete(_patch) is False


def test_new_method_patch_is_not_obsolete(tmp_path, monkeypatch):
    """original_code 为空 = 新增方法，允许目标方法还不存在。"""
    _rel = _mk_repo(tmp_path, "class A:\n    pass\n")
    monkeypatch.setattr("nucleus.evolution.PatchAutoApprover._project_root",
                        lambda: str(tmp_path), raising=True)
    _pa = PatchAutoApprover()
    assert _pa.is_obsolete({"file": _rel, "method": "brand_new",
                            "original_code": ""}) is False


def test_o3_empty_code_candidate_expires(tmp_path, monkeypatch):
    """O3 空码候选：超过 3 天未人工确认 ⇒ obsolete（否则永久占 pending）。"""
    _rel = _mk_repo(tmp_path, "def export_hub_routes():\n    pass\n")
    monkeypatch.setattr("nucleus.evolution.PatchAutoApprover._project_root",
                        lambda: str(tmp_path), raising=True)
    _pa = PatchAutoApprover()
    _base = {"file": _rel, "method": "export_hub_routes", "source": "o3_open_gate",
             "original_code": "", "modified_code": ""}
    _old = dict(_base, generated_at=time.time() - 4 * 86400)
    _new = dict(_base, generated_at=time.time() - 3600)
    assert _pa.is_obsolete(_old) is True
    assert _pa.is_obsolete(_new) is False
