# -*- coding: utf-8 -*-
"""B156-8 / P2-79 · except_pass 续推 pilot 门控测试。

验证非核心层 pilot 文件（hardware / functions / organs，按任务书
utils→hardware→functions→organs→nucleus 顺序，utils 命中 0）的「吸收型 except」
已全部转为 `silent_exc` 可见化（控制流不变，仅异常不再被静默吞掉）。

复用 `tools/scan_except_pass.py` 的 `scan()` 复扫全仓，断言 pilot 文件集剩余为 0。
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PILOT_FILES = {
    "hardware/robot_body/tcp_client.py",
    "functions/health_ui.py",
    "functions/chat/chat_service.py",
    "organs/identity/PulsePersonalityKernel.py",
    "organs/motor/PulseController.py",
    "organs/motor/PulseFileDigester.py",
}


def _load_scanner():
    _p = os.path.join(ROOT, "tools", "scan_except_pass.py")
    _spec = importlib.util.spec_from_file_location("b156_8_exc_scan", _p)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


def test_pilot_files_no_absorbing_except():
    _mod = _load_scanner()
    _res = _mod.scan()
    _remaining = [h for h in _res["hits"] if h["file"] in PILOT_FILES]
    assert _remaining == [], (
        f"pilot 文件仍存在吸收型 except（应已转 silent_exc）：{_remaining}"
    )
