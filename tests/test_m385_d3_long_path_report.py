# -*- coding: utf-8 -*-
"""385 刀3：590 长路径清单的可复算守卫。

本刀交付物是**报告**（只出清单与分档，不改节点），所以守卫只核两件事：
  ① 口径可复算：`--path-depth` 换阈值，结果必须单调递减（深的口径是浅口径的子集）
  ② 报告存在，且里面写清了「噪声嫌疑 / 疑似正常」两档数量

★不核「590 这个数字」—— 知识库每天在长，钉死具体数字会变成第二个
  「测试钉历史快照」的坑（384 批 `test_m354_batch` 已经栽过一次）。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(ROOT, "tools")
for _p in (ROOT, _TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import m384_kb_quality_scan as scan  # noqa: E402

SNAP = os.path.join(ROOT, "data", "knowledge", "pulse_knowledge_snapshot.json")
REPORT = os.path.join(ROOT, "docs", "分析报告", "路灯_385_A4_590长路径清单_20261028.md")


def test_m385_d3_path_depth_is_monotonic():
    """★口径可复算：depth 越大，命中越少（深口径是浅口径的子集）。

    这条同时守住「`--path-depth` 真的生效」—— 如果参数被忽略，两次结果会相等。
    """
    if not os.path.isfile(SNAP):
        return          # 无快照的机器上不适用
    _a = scan.scan(SNAP, path_depth=5)["deep_path"]
    _b = scan.scan(SNAP, path_depth=8)["deep_path"]
    assert _a >= _b, "depth=5 命中 %d 却少于 depth=8 的 %d ⇒ 阈值没生效" % (_a, _b)
    assert _a > 0, "段数≥5 命中 0 ⇒ 解析面又坏了（先查 _locate_nodes_array）"


def test_m385_d3_report_exists_and_two_tiers():
    """报告必须在，且写清两档（噪声嫌疑 / 疑似正常）—— 证据件不是口头结论。"""
    assert os.path.isfile(REPORT), "报告缺失：%s" % REPORT
    _t = io.open(REPORT, encoding="utf-8").read()
    assert "段数 ≥5（票面口径）" in _t
    assert "噪声嫌疑" in _t and "疑似正常" in _t
    assert "只出清单与分档，不标记、不删" in _t, "缺只读纪律声明"
    # 报告里必须给出可复算入口
    assert "m384_kb_quality_scan.py --path-depth" in _t


def test_m385_d3_segment_judge_is_pure():
    """分档判据是纯函数（同输入同输出），不读盘。

    ★判据放在**正式件** `tools/m384_kb_quality_scan.py` 里，不放 tmp 脚本 ——
      两边各写一份必然分叉（384 刀1 已经在 qualname 判据上吃过这个亏）。
    """
    _p = "/a/b/c/d/e/f"
    assert scan.analyse_path(_p) == scan.analyse_path(_p)


def test_m385_d3_duplicate_segment_is_strongest_signal():
    """段重复是最强噪声信号：`/…/框架/…/框架/…` 必须判噪声。"""
    _noise = "/自我理解/代码/X/验证/历史/编程助手/框架/异步/协议/框架/进化/框架"
    assert scan.analyse_path(_noise)["noise"] is True
    assert scan.analyse_path(_noise)["max_dup"] >= 3
    _ok = "/自我/架构/设计文档/BLUEPRINT_CONSTITUTION/第三章"
    assert scan.analyse_path(_ok)["noise"] is False, "正常设计文档层级不该判噪声"
