# -*- coding: utf-8 -*-
"""★383 刀1 · 释放链 tier 分布只读统计（P1）

票面前提已在 382 证伪（`TREE=True` 是真释放不是影子）。本刀**不开任何开关**。

设计要点（上轮失败的教训）：
`release_policy.py` 的 docstring 明确写着「释放路径不 import 新模块（顶部只导
gc/tracemalloc/time）」⇒ 统计口径放它那里（纯内存、零新 import），
**IO 落到已有 os/json 的 collector**。上轮我直接往 release_policy 加 `import io/os`
既违反契约、又因替换静默空跑而失败。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.mnemosyne import release_policy as rp  # noqa: E402
from organs.core.PulseMetricsCollector import PulseMetricsCollector  # noqa: E402


def setup_function(_fn):
    rp.reset_tier_distribution()


def _collector():
    """裸装 collector：只要 `_log` 与节流字段，不跑 __init__。"""
    _c = PulseMetricsCollector.__new__(PulseMetricsCollector)
    _c.logs = []
    _c._log = lambda level, msg: _c.logs.append((level, msg))
    _c._M383_DUMP_EVERY_SEC = 0.0        # 关节流，便于单测
    return _c


def test_m383_d1_counts_by_tier():
    rp.pick_tier(100.0)      # G1
    rp.pick_tier(1200.0)     # G2
    rp.pick_tier(1700.0)     # G3
    rp.pick_tier(8000.0)     # G4
    _d = rp.tier_distribution()
    assert _d["total"] == 4
    assert set(_d["counts"]) == {"G1_OBSERVE", "G2_SOFT_RELEASE",
                                 "G3_COLD_DEMOTE", "G4_ALARM_ONLY"}


def test_m383_d1_shares_sum_to_one():
    for _h in (100.0, 1800.0, 5000.0, 8000.0):
        rp.pick_tier(_h)
    _d = rp.tier_distribution()
    assert abs(sum(_d["shares"].values()) - 1.0) < 1e-6
    assert _d["shares"]["G3_COLD_DEMOTE"] == 0.5


def test_m383_d1_dump_writes_json(tmp_path):
    rp.pick_tier(100.0)
    rp.pick_tier(8000.0)
    _p = str(tmp_path / "tier_distribution.json")
    _out = _collector()._m383_dump_tier_distribution(_p)
    assert _out == _p and os.path.isfile(_p)
    with io.open(_p, encoding="utf-8") as _f:
        _j = json.load(_f)
    assert _j["total"] == 2 and "generated_at" in _j
    assert _j["counts"]["G1_OBSERVE"] == 1


def test_m383_d1_dump_is_throttled(tmp_path):
    """节流：窗口内第二次调用直接返回空串（分布是慢变量，每拍写盘没意义）。"""
    _c = _collector()
    _c._M383_DUMP_EVERY_SEC = 300.0
    _p = str(tmp_path / "t.json")
    assert _c._m383_dump_tier_distribution(_p) == _p
    assert _c._m383_dump_tier_distribution(_p) == ""


def test_m383_d1_pick_tier_semantics_unchanged():
    """包一层后 tier 边界必须逐字不变（G4 硬线 7000 / G3 1700 / G2 1200）。"""
    assert rp.pick_tier(1199.0)[0] == "G1_OBSERVE"
    assert rp.pick_tier(1200.0)[0] == "G2_SOFT_RELEASE"
    assert rp.pick_tier(1699.0)[0] == "G2_SOFT_RELEASE"
    assert rp.pick_tier(1700.0)[0] == "G3_COLD_DEMOTE"
    assert rp.pick_tier(7000.0)[0] == "G4_ALARM_ONLY"


def test_m383_d1_does_not_touch_release_switches():
    """★核心反控：统计过程不许打开任何释放开关。"""
    _t0, _l0 = config.ENABLE_MEMORY_RELEASE_TREE, config.ENABLE_MEMORY_RELEASE_LIVE
    for _h in (100.0, 1800.0, 8000.0):
        rp.pick_tier(_h)
    assert config.ENABLE_MEMORY_RELEASE_TREE == _t0 is False
    assert config.ENABLE_MEMORY_RELEASE_LIVE == _l0 is False


def test_m383_d1_release_policy_gained_no_new_import():
    """模块契约：release_policy 顶部只许 gc/time/tracemalloc/typing + 两个自家模块。"""
    with io.open(os.path.join(ROOT, "nucleus", "mnemosyne",
                              "release_policy.py"), encoding="utf-8") as _f:
        _src = _f.read()
    _head = _src.split("_M383_TIER_COUNTS", 1)[0]
    _imports = [l.strip() for l in _head.split("\n")
                if l.startswith("import ") or l.startswith("from ")]
    assert _imports == ["import gc", "import time", "import tracemalloc",
                        "from typing import Any",
                        "from nucleus._silent_except import silent_exc",
                        "from nucleus.logger import get_module_logger"], _imports
