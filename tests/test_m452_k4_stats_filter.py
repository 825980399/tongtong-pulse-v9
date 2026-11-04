# -*- coding: utf-8 -*-
"""452 刀4：get_stats 过滤来源标签。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.mnemosyne.ReasoningExperience import (
    REAL_DERIVATION_TYPES,
    ReasoningExperience,
    _m452_is_non_question,
)


def _store(exps):
    o = object.__new__(ReasoningExperience)
    o._experiences = list(exps)
    o._max_experiences = 200
    return o


def test_来源标签不进类型分布():
    exps = ([{"source": "quality_feedback", "derivation_type": "remote_api_confirmed"}
             for _ in range(114)]
            + [{"source": "local_fallback", "derivation_type": "unknown"} for _ in range(13)]
            + [{"source": "local", "derivation_type": "inductive"}]
            + [{"source": "remediation", "derivation_type": "failure_mode"}])
    s = _store(exps).get_stats()
    assert s["type_distribution"] == {"inductive": 1, "failure_mode": 1}, s
    assert s["filtered_count"] == 127, s
    assert s["real_type_count"] == 2, s
    assert s["total_experiences"] == 129, s      # 总数不因过滤而变


def test_真实盘面129条过滤干净():
    from nucleus.mnemosyne.ReasoningExperience import get_reasoning_experience
    s = get_reasoning_experience().get_stats()
    assert "remote_api_confirmed" not in s["type_distribution"], s
    assert "unknown" not in s["type_distribution"], s
    assert s["filtered_count"] > 0, s


def test_real_types_与路由白名单一致():
    """★改一张表忘改另一张 ⇒ 自画像和路由口径漂移。"""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "organs", "brain", "PulseInnerWorld.py")
    with open(p, encoding="utf-8", errors="replace") as f:
        src = f.read()
    for t in REAL_DERIVATION_TYPES:
        assert f'"{t}"' in src, f"路由白名单缺 {t}"


def test_过滤是可审计的不是静默丢弃():
    s = _store([{"source": "x", "derivation_type": "unknown"}] * 3).get_stats()
    assert s["filtered_count"] == 3 and s["type_distribution"] == {}, s


# ---------------------------------------------------------------- 刀8-1 入库闸
def test_搜索主题被挡():
    assert _m452_is_non_question("site:baike.baidu.com 技术架构 概念 原理 -拼音") is True


def test_内省语句被挡():
    assert _m452_is_non_question('内化"求真"本能——反思证据') is True


def test_陈述句被挡():
    assert _m452_is_non_question("我需要更多的一手资料和实践来建立扎实的认知") is True


def test_真问题放行():
    for q in ("请推导规则1→A→B", "你是否包含身份相关的知识", "现在几点了？",
              "那你在读取运行日志的时候会关注什么？"):
        assert _m452_is_non_question(q) is False, q


def test_空问题被挡():
    assert _m452_is_non_question("") is True
