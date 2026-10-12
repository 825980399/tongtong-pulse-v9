# -*- coding: utf-8 -*-
"""198批 刀3：实弹刀预嵌设计件 隔离单测。

验收：设计件存在断言 + 放行三条件写入断言 + 隔离单测过。
不放行：不落 prompt / 不翻开关 / 不改 L2 缓存。
"""
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_DESIGN = os.path.join(_REPO, "docs", "分析报告", "路灯_198批_实弹刀预嵌设计.md")
_TASKBOOK = os.path.join(_REPO, "docs", "路灯与星轨对话", "任务书", "第198批_施工任务书_20261012.md")


def test_design_doc_exists():
    """设计件存在断言。"""
    assert os.path.exists(_DESIGN), f"设计件缺失：{_DESIGN}"


def test_design_doc_contains_design_params():
    """设计件含注入点 / 相关性阈值 0.55~0.6 / token 预算 3×120 / L2 缓存先后次序。"""
    src = open(_DESIGN, "r", encoding="utf-8").read()
    assert "organs/body/PulseLung.py" in src and ("911" in src), "缺少注入点锚定"
    assert "0.55" in src and "0.6" in src, "缺少相关性阈值 0.55~0.6"
    assert "3×120" in src, "缺少 token 预算 3×120"
    assert "注入前原文" in src and "剔除注入段" in src, "缺少 L2 缓存先后次序（query注入前原文+回写剔除注入段）"


def test_design_doc_contains_three_release_conditions():
    """设计件含放行三条件：影子读数 / 阈值预算两定数 / L2 命中率基线。"""
    src = open(_DESIGN, "r", encoding="utf-8").read()
    assert "影子读数" in src, "放行条件①缺失：影子读数"
    assert "阈值" in src and "预算" in src and "两定数" in src, "放行条件②缺失：阈值/预算两定数"
    assert "L2 命中率基线" in src or "L2命中率基线" in src, "放行条件③缺失：L2命中率基线"


def test_taskbook_records_three_release_conditions():
    """放行三条件已写入198书（作为放行条件而非排期承诺）。"""
    src = open(_TASKBOOK, "r", encoding="utf-8").read()
    assert "放行三条件" in src, "198书未写入『放行三条件』小节"
    assert "影子读数" in src
    assert "阈值/预算两定数" in src or "阈值预算两定数" in src
    assert "L2命中率基线" in src or "L2 命中率基线" in src
    # 明确为「放行条件而非排期承诺」
    assert "放行条件而非排期承诺" in src or "不排期" in src
