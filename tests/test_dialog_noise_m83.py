# -*- coding: utf-8 -*-
"""第83批：日志噪音 + 对话链路残留 + 搜索链路修复 + 数据完整性。

覆盖：
- T-a1 内容去重日志刷屏治理（信任顶格降 DEBUG + 路径级 1 分钟节流）
- T-a2 LLM 补丁 ast.parse 必带 filename（消灭 <unknown> SyntaxWarning）
- T-b1 真短答案（问候/算术模板答案，带尾标点）不得被净化误清空
- T-b2 净化白名单补器官名方括号标记（[器官]/[大脑]/[肺] ...）
- T-c1(1) 语义增强关键词与主题零重叠 → 丢弃，不污染搜索链路
- T-c1(2) 审查零重叠 → 先重试一次，不直接终止
- T-d1 按文件读 Parquet 必须回填分区列 evol_level；from_dict 告警须带调用方
"""
from __future__ import annotations

import ast
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402


def _bare_iw():
    """构造未走 __init__ 的空壳，仅用于测纯文本处理方法。"""
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._active_search_correlation = {}
    iw._log = lambda *a, **k: None
    return iw


# ======================================================================
# T-b1：真短答案带尾标点不得被净化误清空
# ======================================================================

class TestTb1ShortAnswerNotCleared:
    @pytest.mark.parametrize("text", [
        "在呢，你说。",          # 问候"在吗"模板答案
        "你好呀，我在呢。",       # 问候"你好"模板答案
        "1+1 等于 2。",          # 算术模板答案
        "晚安，好梦。",           # 问候"晚安"模板答案
        "好的，正在搜索「科技新闻」相关信息，请稍候...",
    ])
    def test_punctuation_tail_not_treated_as_internal_marker(self, text):
        """标点归一化/首尾 strip 造成的长度变化不是"含内部标记词"。"""
        out = _bare_iw()._sanitize_internal_content(text, text)
        assert out.strip(), f"真短答案被误清空: {text!r} -> {out!r}"

    def test_missing_evol_preserved_exact(self):
        out = _bare_iw()._sanitize_internal_content("在呢，你说。", "在吗")
        assert "在呢" in out and "你说" in out


# ======================================================================
# T-b2：器官名方括号标记一律不放行
# ======================================================================

class TestTb2OrganMarkerSanitized:
    @pytest.mark.parametrize("marker", [
        "[器官]", "[大脑]", "[肺]", "[胃]", "[心]", "[肝]",
        "[肾]", "[皮肤]", "[胸腺]", "[双腿]", "[眼睛]",
    ])
    def test_organ_marker_hard_removed(self, marker):
        text = f"深层原理：{marker} [器官职责说明书·自动生成] PulseRiskPerception是框架内部组件"
        out = _bare_iw()._sanitize_internal_content(text, text)
        assert marker not in out, f"器官标记未清除: {marker!r} -> {out!r}"
        assert "职责说明书" not in out, f"标记后正文未清: {out!r}"

    def test_existing_sanitize_rules_still_work(self):
        """已覆盖标记不回退。"""
        iw = _bare_iw()
        for text in ("关联知识：[节点A] 内部内容",
                     "[核心智慧] 内部思考",
                     "代码片段: def f(): pass"):
            out = iw._sanitize_internal_content(text, text)
            assert "关联知识" not in out and "[核心智慧]" not in out and "代码片段" not in out


# ======================================================================
# T-a1：内容去重日志刷屏治理
# ======================================================================

class _LogRecorder:
    """捕获模块 logger 的 INFO/DEBUG/WARNING（注意：列表名不得与方法名同名）。"""

    def __init__(self):
        self.info_msgs = []
        self.debug_msgs = []
        self.warning_msgs = []
        self.error_msgs = []

    def info(self, msg, *a, **k):
        self.info_msgs.append(str(msg))

    def debug(self, msg, *a, **k):
        self.debug_msgs.append(str(msg))

    def warning(self, msg, *a, **k):
        self.warning_msgs.append(str(msg))

    def error(self, msg, *a, **k):
        self.error_msgs.append(str(msg))


def _dedup_line(msg: str, old_trust: float) -> bool:
    return "内容去重" in msg and f"信任{old_trust:.0f}" in msg


@pytest.fixture
def _pool_env():
    import nucleus.mnemosyne.PulseNodePool as _poolmod
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    rec = _LogRecorder()
    _orig = _poolmod._module_logger
    _poolmod._module_logger = rec
    pool = PulseNodePool(max_hot=100000, max_warm=100000)

    def _mk(tag: str, path: str, trust: float, extra: str = ""):
        n = PulseNode(value="第83批日志治理测试节点长文本甲乙丙丁戊己庚辛壬癸子丑寅卯" + tag + extra,
                      keywords=["m83log", "噪音治理测试"], space_path=path,
                      source_organ="m83test", evol_level="L1")
        n.trust_score = trust
        return n

    try:
        yield pool, rec, _mk
    finally:
        _poolmod._module_logger = _orig


class TestTa1DedupLogNoise:
    def test_trust_capped_merge_goes_debug(self, _pool_env):
        pool, rec, _mk = _pool_env
        pool.add(_mk("甲", "/测试/顶格", 95.0))
        rec.info_msgs.clear()
        rec.debug_msgs.clear()
        pool.add(_mk("甲", "/测试/顶格", 95.0, extra="甲乙"))
        assert not any(_dedup_line(m, 95) for m in rec.info_msgs), \
            f"信任顶格合并仍打 INFO: {rec.info_msgs}"
        assert any(_dedup_line(m, 95) for m in rec.debug_msgs), \
            f"信任顶格合并未降 DEBUG: {rec.debug_msgs}"

    def test_path_throttle_keeps_first_info_only(self, _pool_env):
        pool, rec, _mk = _pool_env
        pool.add(_mk("乙", "/测试/节流", 50.0))
        rec.info_msgs.clear()
        for i, ex in enumerate(("乙甲", "乙甲乙", "乙甲乙丙")):
            pool.add(_mk("乙", "/测试/节流", 50.0 + i, extra=ex))
        _hits = [m for m in rec.info_msgs if _dedup_line(m, 50)]
        assert len(_hits) == 1, f"同一路径 1 分钟内 INFO 未节流到 1 条: {_hits}"
        assert rec.debug_msgs, "被节流的合并应走 DEBUG 而非静默丢弃"

    def test_distinct_paths_each_get_first_info(self, _pool_env):
        pool, rec, _mk = _pool_env
        pool.add(_mk("丙", "/测试/路径A", 60.0))
        pool.add(_mk("丁", "/测试/路径B", 60.0))
        rec.info_msgs.clear()
        pool.add(_mk("丙", "/测试/路径A", 60.0, extra="丙甲"))
        pool.add(_mk("丁", "/测试/路径B", 60.0, extra="丁甲"))
        assert len([m for m in rec.info_msgs if _dedup_line(m, 60)]) == 2, \
            "不同路径应各自可打首条 INFO（节流须按路径隔离）"


# ======================================================================
# T-a2：LLM 补丁 ast.parse 必带 filename
# ======================================================================

class TestTa2AstParseFilename:
    _TARGETS = (
        "nucleus/reasoning/PatchManager.py",
        "nucleus/self_inspector.py",
        "nucleus/reasoning/SafeEvolutionExecutor.py",
    )

    @pytest.mark.parametrize("rel", _TARGETS)
    def test_no_bare_ast_parse_without_filename(self, rel):
        """任何 ast.parse 调用不得缺 filename（否则告警显示 <unknown>）。"""
        path = os.path.join(_ROOT, rel)
        with io.open(path, encoding="utf-8", errors="replace") as _f:
            src = _f.read()
        tree = ast.parse(src, filename=rel)
        _bad = []
        for _n in ast.walk(tree):
            if not isinstance(_n, ast.Call):
                continue
            _f2 = _n.func
            if not (isinstance(_f2, ast.Attribute) and _f2.attr == "parse"):
                continue
            if not (isinstance(_f2.value, ast.Name)
                    and _f2.value.id in ("ast", "_ast", "_ast_qc")):
                continue
            if not any(_k.arg == "filename" for _k in _n.keywords):
                _bad.append(getattr(_n, "lineno", -1))
        assert not _bad, f"{rel} 存在缺 filename 的 ast.parse: 行 {_bad}"


# ======================================================================
# T-c1(1)：语义增强关键词与主题重叠度校验
# ======================================================================

class TestTc1KeywordOverlap:
    def test_helper_detects_topic_overlap(self):
        from organs.motor.PulseController import PulseController
        f = PulseController._kws_match_topic
        assert f(["科技", "新闻"], "今天的科技新闻") is True
        assert f(["微服务", "架构"], "微服务架构演进") is True
        assert f(["探索", "适合", "生活"], "今天的科技新闻") is False

    def test_offtopic_semantic_keywords_discarded(self):
        from organs.motor.PulseController import PulseController
        pc = PulseController.__new__(PulseController)
        pc._log = lambda *a, **k: None

        class _Page:
            def wait_for_load_state(self, *a, **k):
                return None

            def inner_text(self, sel):
                return "今天的科技新闻：人工智能与大模型成为今日焦点，科技行业新闻密集发布。"

        pc._headless_page = _Page()
        pc._semantic_extract_keywords = lambda text, limit=6: ["探索", "适合", "生活"]
        pc._purify_search_keywords = lambda kws, topic, limit=6: list(kws)

        out = pc._extract_keywords_from_summary("搜索一下今天的科技新闻")
        assert "探索" not in out and "适合" not in out and "生活" not in out, \
            f"与主题零重叠的语义关键词未被丢弃: {out}"


# ======================================================================
# T-c1(2)：审查零重叠先重试一次，不直接终止
# ======================================================================

class TestTc1ReviewRetry:
    def _bare_reviewer(self):
        iw = _bare_iw()
        iw._search_experience = {}
        iw._search_experience_max = 500
        iw._insight_board = None
        iw._direct_to_lung_questions = set()
        iw._make_experience_key = lambda topic: "k_" + str(topic)[:12]
        iw._observe_search_quality = lambda *a, **k: None
        iw._pick_search_cid = lambda payload, topic: "cid-m83"
        iw._fallback_to_lung_model = lambda *a, **k: {"status": "fallback"}
        iw._emitted = []
        iw._emit = lambda et, payload, **kw: iw._emitted.append((et, payload))
        return iw

    _PAYLOAD = {
        "stage": 1,
        "search_topic": "今天的科技新闻",
        "keywords": ["探索", "适合", "生活"],
        "articles_found": 0,
        "note": "",
    }

    def _statuses(self, iw):
        return [p.get("status") for _e, p in iw._emitted]

    def test_first_zero_overlap_retries_instead_of_terminating(self):
        iw = self._bare_reviewer()
        iw._handle_search_stage_feedback(dict(self._PAYLOAD))
        assert "terminate" not in self._statuses(iw), \
            f"零重叠首次即终止（审查误判）: {self._statuses(iw)}"
        assert any(p.get("deep_search") for _e, p in iw._emitted), \
            "零重叠首次应发出重试提取请求"

    def test_second_zero_overlap_terminates(self):
        iw = self._bare_reviewer()
        iw._handle_search_stage_feedback(dict(self._PAYLOAD))
        iw._handle_search_stage_feedback(dict(self._PAYLOAD))
        assert "terminate" in self._statuses(iw), \
            f"重试后仍无关应终止: {self._statuses(iw)}"

    def test_relevant_keywords_never_terminate(self):
        iw = self._bare_reviewer()
        p = dict(self._PAYLOAD)
        p["keywords"] = ["科技", "新闻", "今日"]
        iw._handle_search_stage_feedback(p)
        assert "terminate" not in self._statuses(iw)


# ======================================================================
# T-d1：Parquet 分区列 evol_level 回填 + 告警定位
# ======================================================================

class TestTd1EvolLevelBackfill:
    def test_load_real_nodes_backfills_evol_level(self):
        """按文件读 Parquet 时分区列不在文件内 → 必须显式回填。"""
        sys.path.insert(0, os.path.join(_ROOT, "tools"))
        import benchmark_hot_cold_faiss_kal as _bench
        rows = _bench._load_real_nodes(os.path.join(_ROOT, "data", "knowledge", "parquet"))
        assert isinstance(rows, list) and rows, "真实 Parquet 读取为空"
        _missing = [i for i, r in enumerate(rows) if not r.get("evol_level")]
        assert not _missing, f"{len(_missing)}/{len(rows)} 行缺 evol_level（分区列未回填）"
        _levels = {str(r.get("evol_level")) for r in rows}
        assert _levels <= {"L1", "L2", "L3"} and len(_levels) >= 2, \
            f"分层回填异常: {_levels}"

    def test_from_dict_warning_names_caller(self):
        import nucleus.mnemosyne.PulseNode as _pnmod
        rec = _LogRecorder()
        _orig = _pnmod._module_logger
        _orig_n = _pnmod._m80_evol_missing["n"]
        _pnmod._module_logger = rec
        _pnmod._m80_evol_missing["n"] = 0
        try:
            _pnmod.PulseNode.from_dict({"node_id": "n1", "value": "v"})
        finally:
            _pnmod._module_logger = _orig
            _pnmod._m80_evol_missing["n"] = _orig_n
        assert rec.warning_msgs, "缺 evol_level 未告警"
        _msg = rec.warning_msgs[0]
        assert ".py:" in _msg, f"告警未标出具体调用方: {_msg}"
        assert "第83批" in _msg, f"告警未标注本批来源: {_msg}"
